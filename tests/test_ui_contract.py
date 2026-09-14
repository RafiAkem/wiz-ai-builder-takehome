"""Source-level contract checks for the static dashboard (ui/index.html).

The UI is a single static HTML file with no build step and no JS test runner
in this repository, so these tests assert on its source: the ingest payload
and response contract, the dedupe "Show more" control ordering, valid table
semantics in the duplicate comparison, and preserved output escaping.
"""

import re
from pathlib import Path

UI_PATH = Path(__file__).resolve().parent.parent / "ui" / "index.html"


def ui_source() -> str:
    return UI_PATH.read_text(encoding="utf-8")


def ingest_handler() -> str:
    match = re.search(
        r"ingest-form'\)\.addEventListener\('submit'.*?\n\}\);", ui_source(), re.S
    )
    assert match, "ingest submit handler not found"
    return match.group(0)


def test_ingest_sends_submitted_at():
    assert re.search(r"submitted_at\s*:", ingest_handler()), (
        "ingest payload must include submitted_at (required by FormSubmission)"
    )


def test_ingest_reads_results_action_and_lead_id():
    handler = ingest_handler()
    assert re.search(r"results\?\.\[0\]|results\[0\]", handler), (
        "response contract is {results: [{action, lead}]}"
    )
    assert "action" in handler
    assert re.search(r"lead\?\.id|lead\.id", handler)
    for stale in (r"r\.result\b", r"r\.lead_id\b", r"r\.id\b", r"r\.status\b"):
        assert not re.search(stale, handler), f"stale response field read: {stale}"


def test_ingest_hint_matches_on_email_or_phone():
    panel = re.search(r'id="panel-ingest".*?</section>', ui_source(), re.S).group(0)
    assert "email" in panel and "phone" in panel
    assert "name and company" not in panel


def test_dedupe_show_more_control_stays_at_end():
    match = re.search(r"function renderMore\(n\) \{.*?\n\}", ui_source(), re.S)
    assert match, "renderMore not found"
    fn = match.group(0)
    assert "replaceWith" not in fn, "no-op self-replacement must be gone"
    insert_before = fn.find("wrap.insertBefore(frag, more)")
    assert insert_before != -1, "new groups must be inserted above the existing control"
    remove = fn.find("more.remove()")
    assert remove != -1 and remove > insert_before, (
        "control must be removed only once every group is shown"
    )
    assert fn.rfind('id="show-more-wrap"') > insert_before, (
        "control must be re-created when more groups remain"
    )


def test_dedupe_comparison_uses_valid_table_semantics():
    src = ui_source()
    assert 'role="table"' not in src, "pseudo-table ARIA roles must be replaced"
    assert 'role="rowheader"' not in src
    match = re.search(r'<table class="cmp".*?</table>', src, re.S)
    assert match, "duplicate comparison must use semantic table markup"
    table = match.group(0)
    assert "<thead>" in table
    assert 'scope="col"' in table
    assert 'scope="row"' in table


def test_ingest_and_comparison_output_still_escaped():
    assert "esc(" in ingest_handler()
    assert "esc(" in re.search(r"function renderGroup\(g\) \{.*?\n\}", ui_source(), re.S).group(0)
