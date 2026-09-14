import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Protocol
from urllib import error, request

from app.config import DEFAULT_GEMINI_MODEL

from app.normalization import clean

CHANNELS = ("Website", "Event", "LinkedIn", "Organic Search", "Referral", "Manual/Sales", "Other")


@dataclass(frozen=True)
class SourceResult:
    channel: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class SourceFallback(Protocol):
    def extract(self, text: str, original_source: str, page_url: str) -> SourceResult: ...


class MockSourceFallback:
    def __init__(self, result: SourceResult | None = None):
        self.result = result or SourceResult("Other", "Unclassified")

    def extract(self, text: str, original_source: str, page_url: str) -> SourceResult:
        return self.result


class GeminiSourceFallback:
    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL):
        self.api_key = api_key
        self.model = model

    def extract(self, text: str, original_source: str, page_url: str) -> SourceResult:
        prompt = (
            "Classify this CRM lead source. Return JSON only with channel and detail. "
            f"channel must be one of: {', '.join(CHANNELS)}. "
            f"Notes: {text!r}; original_source: {original_source!r}; page_url: {page_url!r}"
        )
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }).encode()
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            f"?key={self.api_key}"
        )
        call = request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
        try:
            with request.urlopen(call, timeout=10) as response:
                body = json.load(response)
            content = body["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(content)
            channel = result.get("channel")
            detail = clean(result.get("detail"))
            if channel not in CHANNELS or not detail:
                raise ValueError("Gemini returned an invalid source result")
            return SourceResult(channel, detail)
        except (error.URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            return SourceResult("Other", clean(original_source) or "Unclassified")


def configured_fallback() -> SourceFallback:
    if os.getenv("SOURCE_LLM_MODE", "mock").casefold() == "gemini" and os.getenv("GEMINI_API_KEY"):
        return GeminiSourceFallback(os.environ["GEMINI_API_KEY"], os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL))
    return MockSourceFallback()


def _event_detail(text: str) -> str:
    match = re.search(r"(?:at|during) (?:the )?(.+?)(?: booth|,|\.|$)", text, re.IGNORECASE)
    event = clean(match.group(1)) if match else "Event"
    suffix = " — Booth QR Code" if "qr code" in text.casefold() else " — Booth"
    return event + suffix


def extract_source(
    text: str | None,
    original_source: str | None = None,
    page_url: str | None = None,
    fallback: SourceFallback | None = None,
) -> SourceResult:
    raw = clean(text)
    lowered = raw.casefold()
    source = clean(original_source).casefold()

    if re.search(r"\b(linked\s?in|linkedin|li dm)\b", lowered):
        return SourceResult("LinkedIn", "Inbound message" if "inbound" in lowered or "dm" in lowered else "LinkedIn")
    if any(term in lowered for term in ("booth", "conference", "expo", "summit", "festival", "congress", "saastr", "techcrunch disrupt")):
        return SourceResult("Event", _event_detail(raw))
    referral = re.search(r"referred by ([^,.]+)", raw, re.IGNORECASE)
    if referral or "warm intro" in lowered or source.startswith("referral"):
        return SourceResult("Referral", clean(referral.group(1)) if referral else "Referral")
    if re.search(r"\b(organic|googled|google search|search engine)\b", lowered) or source == "organic search":
        detail = "Google"
        if "book" in lowered and "demo" in lowered:
            detail += " — Booked a Demo"
        return SourceResult("Organic Search", detail)
    if any(term in lowered for term in ("manually added", "manual -", "cold outreach", "sales call", "phone call", "general info@")):
        return SourceResult("Manual/Sales", "Sales outreach or direct contact")
    if any(term in lowered for term in ("filled out", "form", "book-a-demo", "booked a demo", "website")) or page_url:
        page = clean(page_url).strip("/").replace("-", " ").title() if page_url else ""
        return SourceResult("Website", f"{page} Page" if page else "Website form")
    if source in {"direct traffic", "paid search", "other campaigns"}:
        return SourceResult("Website", clean(original_source))
    return (fallback or configured_fallback()).extract(raw, clean(original_source), clean(page_url))
