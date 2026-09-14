import csv
import io
import os
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.config import DEFAULT_DATABASE_PATH
from app.database import PUBLIC_COLUMNS, LeadStore
from app.dedupe import candidate_groups
from app.ingest import ingest_submission
from app.llm_budget import guard
from app.models import DedupeRequest, FormSubmission, LeadPatch, SourceRequest
from app.source_extraction import GeminiSourceFallback, configured_fallback, extract_source_traced


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = os.environ.get("LEADS_DATABASE_PATH", str(DEFAULT_DATABASE_PATH))
    app.state.store = LeadStore(path)
    app.state.store.initialize()
    yield


app = FastAPI(title="AI-Assisted Mini Lead Management System", version="1.0.0", lifespan=lifespan)


def store(request: Request) -> LeadStore:
    return request.app.state.store

def _trusted_proxies() -> frozenset[IPv4Network | IPv6Network]:
    """Peer networks whose forwarded headers we accept.

    `TRUSTED_PROXIES` is a comma-separated list of CIDR ranges. Default is empty:
    the app trusts no one, so forwarded headers from any direct peer are ignored and
    the socket peer address is used. Configure it (e.g. `TRUSTED_PROXIES=127.0.0.1/32,::1/128`
    behind a local nginx, or the LB's range) only for peers that overwrite or append
    to the forwarded headers before the request reaches this app. A value a client
    can forge would let it rotate fake IPs and mint fresh rate-limit buckets.
    """
    raw = os.getenv("TRUSTED_PROXIES", "")
    networks: list[IPv4Network | IPv6Network] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            network = ip_network(part)
        except ValueError:
            continue  # a bad entry must never widen trust; skip it
        if isinstance(network, (IPv4Network, IPv6Network)):
            networks.append(network)
    return frozenset(networks)


def _is_trusted(peer: str) -> bool:
    try:
        address = ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in _trusted_proxies())


def client_ip(request: Request) -> str:
    """Resolve the caller's IP, trusting forwarded headers only from trusted peers.

    When the socket peer is a configured trusted proxy (nginx fronts this app and
    sets `X-Real-IP $remote_addr`, overwritten on every request, and appends the
    peer address to `X-Forwarded-For`), the client address is X-Real-IP, falling
    back to the LAST XFF hop — the one the proxy appended. The first XFF hop is
    attacker-controlled either way: rotating it must never mint a fresh bucket.
    Reached directly (tests, local dev, or no proxy configured), the socket peer is
    used and any forwarded headers present are ignored as client-forged noise.
    """
    peer = request.client.host if request.client else "unknown"
    if not _is_trusted(peer):
        return peer
    real = request.headers.get("x-real-ip")
    if real and real.strip():
        return real.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last_hop = forwarded.split(",")[-1].strip()
        if last_hop:
            return last_hop
    return peer


def llm_gate(request: Request):
    """Per-request handle on the LLM budget (ProviderGate protocol). Only consulted
    on a rules miss; each consume() charges this client for one provider attempt."""
    return guard.gate_for(client_ip(request))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/leads")
def list_leads(
    request: Request,
    status: str | None = None,
    owner: str | None = None,
    country: str | None = None,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    leads, total = store(request).list(status=status, owner=owner, country=country, q=q, limit=limit, offset=offset)
    return {"items": leads, "total": total, "limit": limit, "offset": offset}


@app.get("/leads/export")
def export_leads(
    request: Request, status: str | None = None, owner: str | None = None,
    country: str | None = None, q: str | None = None,
) -> StreamingResponse:
    leads, _ = store(request).list(status=status, owner=owner, country=country, q=q, limit=100_000)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=PUBLIC_COLUMNS)
    writer.writeheader()
    writer.writerows(leads)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="leads.csv"'},
    )


@app.post("/leads/ingest")
def ingest(request: Request, payload: FormSubmission | list[FormSubmission]) -> dict:
    submissions = payload if isinstance(payload, list) else [payload]
    results = [ingest_submission(store(request), submission) for submission in submissions]
    return {"results": results}


@app.post("/leads/dedupe-candidates")
def dedupe(request: Request, options: DedupeRequest = DedupeRequest()) -> dict:
    leads = store(request).all_internal()
    groups, compared = candidate_groups(leads, options.threshold)
    items = groups[: options.limit]
    return {
        "items": items,
        "count": len(groups),
        "candidate_pairs_compared": compared,
        "compared_population": len(leads),
        "returned": len(items),
        "truncated": len(items) < len(groups),
    }


@app.get("/leads/{lead_id}")
def get_lead(request: Request, lead_id: int) -> dict:
    lead = store(request).get(lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.patch("/leads/{lead_id}")
def patch_lead(request: Request, lead_id: int, patch: LeadPatch) -> dict:
    changes = patch.model_dump(exclude_unset=True)
    lead = store(request).update(lead_id, changes)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.post("/source/extract")
def source_extract(request: Request, payload: SourceRequest) -> dict:
    return extract_source_traced(
        payload.text, payload.original_source, payload.page_url, gate=llm_gate(request)
    ).as_dict()


@app.get("/llm-budget")
def llm_budget() -> dict:
    fallback = configured_fallback()
    live = isinstance(fallback, GeminiSourceFallback)
    return {
        "per_ip_hour_limit": guard.per_ip_limit,
        "global_day_limit": guard.global_limit,
        "global_used_today": guard.used_today(),
        "mode": "gemini" if live else "mock",
        "model": fallback.model if live else None,
    }


@app.get("/")
def ui() -> FileResponse:
    ui_path = Path(__file__).parent.parent / "ui" / "index.html"
    if not ui_path.is_file():
        raise HTTPException(status_code=404, detail="Dashboard UI not available")
    return FileResponse(ui_path)


@app.get("/dashboard")
def dashboard(request: Request) -> dict:
    return store(request).dashboard()
