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
    assert isinstance(fallback, GeminiSourceFallback)
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

    def extract(self, text, original_source, page_url):
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
