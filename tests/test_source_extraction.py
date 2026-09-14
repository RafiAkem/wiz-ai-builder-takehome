import pytest

from app.source_extraction import (
    GeminiSourceFallback,
    MockSourceFallback,
    SourceResult,
    configured_fallback,
    extract_source,
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
