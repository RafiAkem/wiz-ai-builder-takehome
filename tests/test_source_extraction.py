import pytest
from urllib.error import URLError

from app.source_extraction import (
    GeminiSourceFallback,
    MockSourceFallback,
    SourceResult,
    configured_fallback,
    extract_source,
    extract_source_traced,
)


@pytest.mark.parametrize(("text", "channel", "detail_fragment"), [
    ("Scanned our QR code at the SaaStr Annual booth.", "Event", "SaaStr Annual"),
    ("LinkedIn DM inbound asking about pricing.", "LinkedIn", "Inbound"),
    ("Referred by Elena Han, warm intro.", "Referral", "Elena Han"),
    ("Found us through organic google search then booked a demo.", "Organic Search", "Google"),
    ("Filled out the form on the contact page.", "Website", "Website form"),
    ("Manually added after a sales call.", "Manual/Sales", "Sales outreach"),
    ("No useful acquisition context.", "Other", "Unclassified"),
])
def test_extract_source(text, channel, detail_fragment):
    result = extract_source(text, fallback=MockSourceFallback())
    assert result.channel == channel
    assert detail_fragment in result.detail


def test_ambiguous_text_uses_injected_fallback():
    fallback = MockSourceFallback(SourceResult("Referral", "Partner network"))
    result = extract_source("Introduced through our ecosystem.", fallback=fallback)
    assert result == SourceResult("Referral", "Partner network")


def test_configured_fallback_selects_supported_gemini_model(monkeypatch):
    monkeypatch.setenv("SOURCE_LLM_MODE", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    fallback = configured_fallback()
    assert fallback.model == "gemini-3.6-flash"


def test_configured_fallback_uses_mock_without_key(monkeypatch):
    monkeypatch.setenv("SOURCE_LLM_MODE", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert isinstance(configured_fallback(), MockSourceFallback)


# Every rules-reachable channel must be labelled "rules" and must never touch the LLM.
# ("Other" is absent by construction: it is the degraded label the fallback returns.)
RULES_HITS = (
    ("Scanned our QR code at the SaaStr Annual booth.", "Event"),
    ("LinkedIn DM inbound asking about pricing.", "LinkedIn"),
    ("Referred by Elena Han, warm intro.", "Referral"),
    ("Found us through organic google search then booked a demo.", "Organic Search"),
    ("Filled out the form on the contact page.", "Website"),
    ("Manually added after a sales call.", "Manual/Sales"),
)


def test_rules_hits_report_rules_layer_without_llm():
    for text, expected_channel in RULES_HITS:
        trace = extract_source_traced(text, fallback=MockSourceFallback())
        assert trace.channel == expected_channel, text
        assert trace.answered_by == "rules", text
        assert trace.model is None, text
        assert trace.llm_throttled is False, text
        assert trace.latency_ms >= 0, text


def test_rules_miss_reports_fallback_without_throttling(monkeypatch):
    monkeypatch.setenv("SOURCE_LLM_MODE", "mock")
    for fallback in (None, MockSourceFallback()):
        trace = extract_source_traced("Introduced through our ecosystem.", fallback=fallback)
        assert trace.answered_by == "fallback"
        assert trace.model is None
        assert trace.llm_throttled is False


class FakeLlmFallback:
    """Stands in for a successful Gemini round-trip. No network in this environment."""

    model = "fake-gemini-3.6-flash"
    served_by_llm = True

    def extract(self, text, original_source, page_url, retry_gate=None):
        return SourceResult("Referral", "Partner network")


def test_injected_llm_fallback_reports_llm_and_model():
    trace = extract_source_traced("Introduced through our ecosystem.", fallback=FakeLlmFallback())
    assert trace.answered_by == "llm"
    assert trace.model == "fake-gemini-3.6-flash"
    assert trace.channel == "Referral"
    assert trace.llm_throttled is False


def test_gemini_fallback_degrades_on_transport_error(monkeypatch):
    def unreachable(*_args, **_kwargs):
        raise URLError("network is unreachable")

    monkeypatch.setattr("app.source_extraction.request.urlopen", unreachable)
    trace = extract_source_traced(
        "Introduced through our ecosystem.", "Partner", fallback=GeminiSourceFallback("test-key")
    )
    assert trace.answered_by == "fallback"
    assert trace.model is None
    assert trace.detail == "Partner"


def _gemini_body(channel="Referral", detail="Partner network"):
    import json as _json
    payload = _json.dumps({"candidates": [{"content": {"parts": [{"text": _json.dumps({"channel": channel, "detail": detail})}]}}]})
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return payload.encode()
    return Response()


def test_gemini_retries_once_on_upstream_5xx(monkeypatch):
    """Gemini answers 503 under load; one retry should recover the model answer."""
    from urllib.error import HTTPError

    calls = []

    def flaky(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise HTTPError("https://example.invalid", 503, "Service Unavailable", {}, None)
        return _gemini_body()

    monkeypatch.setattr("app.source_extraction.request.urlopen", flaky)
    trace = extract_source_traced(
        "Introduced through our ecosystem.", "Partner", fallback=GeminiSourceFallback("test-key")
    )
    assert len(calls) == 2
    assert trace.answered_by == "llm"
    assert trace.model == "gemini-3.6-flash"
    assert (trace.channel, trace.detail) == ("Referral", "Partner network")


def test_gemini_does_not_retry_on_client_error(monkeypatch):
    from urllib.error import HTTPError

    calls = []

    def denied(*_args, **_kwargs):
        calls.append(1)
        raise HTTPError("https://example.invalid", 400, "Bad Request", {}, None)

    monkeypatch.setattr("app.source_extraction.request.urlopen", denied)
    trace = extract_source_traced(
        "Introduced through our ecosystem.", "Partner", fallback=GeminiSourceFallback("test-key")
    )
    assert len(calls) == 1, "a 4xx must not be retried"
    assert trace.answered_by == "fallback"
    assert trace.detail == "Partner"


def test_gemini_retry_charges_budget_so_attempts_match_billing(monkeypatch):
    """Each real provider attempt consumes one budget unit — the retry included."""
    from urllib.error import HTTPError

    from app.llm_budget import guard

    monkeypatch.setenv("LLM_RATE_PER_IP_HOUR", "2")
    monkeypatch.setenv("LLM_RATE_GLOBAL_DAY", "800")

    calls = []

    def flaky(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise HTTPError("https://example.invalid", 503, "Service Unavailable", {}, None)
        return _gemini_body()

    monkeypatch.setattr("app.source_extraction.request.urlopen", flaky)
    trace = extract_source_traced(
        "Introduced through our ecosystem.", "Partner",
        fallback=GeminiSourceFallback("test-key"), gate=guard.gate_for("203.0.113.9"),
    )
    assert trace.answered_by == "llm"
    assert len(calls) == 2, "the 5xx retry really ran"
    assert guard.used_today() == 2, "initial call plus retry both charged"
    guard.reset()
    monkeypatch.setenv("LLM_RATE_PER_IP_HOUR", "1")
    calls.clear()
    trace = extract_source_traced(
        "Introduced through our ecosystem.", "Partner",
        fallback=GeminiSourceFallback("test-key"), gate=guard.gate_for("203.0.113.9"),
    )
    assert len(calls) == 1, "an empty budget must not fund a second attempt"
    assert trace.answered_by == "fallback" and trace.llm_throttled is True, "budget-blocked retry must be visible"


def test_budget_refusal_degrades_without_calling_provider(monkeypatch):
    from app.llm_budget import guard

    monkeypatch.setenv("LLM_RATE_PER_IP_HOUR", "0")  # budget exhausted before any call
    calls = []
    def must_not_run(*_args, **_kwargs):
        calls.append(1)
        return _gemini_body()

    monkeypatch.setattr("app.source_extraction.request.urlopen", must_not_run)
    trace = extract_source_traced(
        "Introduced through our ecosystem.", "Partner",
        fallback=GeminiSourceFallback("test-key"), gate=guard.gate_for("203.0.113.9"),
    )
    assert calls == [], "a refused request must never reach the provider"
    assert trace.answered_by == "fallback"
    assert trace.llm_throttled is True
    assert (trace.channel, trace.detail) == ("Other", "Partner")


def test_provider_concurrency_bound_refuses_without_blocking(monkeypatch):
    """When every provider slot is held, the request degrades immediately instead of
    queueing behind an upstream outage."""
    import threading

    from app import source_extraction

    # Drain the real semaphore so the next request finds no slot.
    slots = []
    while source_extraction._PROVIDER_SLOTS.acquire(blocking=False):
        slots.append(True)

    started = threading.Event()

    def hold_slot(*_args, **_kwargs):
        started.set()
        return _gemini_body()

    try:
        trace = extract_source_traced(
            "Introduced through our ecosystem.", "Partner",
            fallback=GeminiSourceFallback("test-key"), gate=None,
        )
    finally:
        for _ in slots:
            source_extraction._PROVIDER_SLOTS.release()

    assert started.is_set() is False, "provider was never reached"
    assert trace.answered_by == "fallback"
    assert trace.llm_throttled is True
    assert trace.detail == "Partner"
