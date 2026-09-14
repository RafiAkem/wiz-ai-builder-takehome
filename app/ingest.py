from datetime import datetime, timezone

from app.database import LeadStore
from app.models import FormSubmission
from app.normalization import (
    clean,
    email_domain,
    normalize_company,
    normalize_email,
    normalize_name,
    normalize_phone,
    parse_date,
    split_name,
)
from app.source_extraction import extract_source


def _internal_submission(submission: FormSubmission) -> dict[str, object]:
    first, last = split_name(submission.name)
    email = normalize_email(submission.email)
    phone = normalize_phone(submission.phone)
    source = extract_source(submission.message, page_url=submission.page_url)
    created = parse_date(submission.submitted_at)
    return {
        "first_name": first, "last_name": last, "full_name": clean(submission.name), "job_title": "",
        "company": clean(submission.company), "email": clean(submission.email), "phone": clean(submission.phone),
        "country": clean(submission.country), "status": "New", "lifecycle_stage": "Lead",
        "original_source": "Website form", "owner": "", "created_at": created, "modified_at": created,
        "notes": clean(submission.message), "lead_score": None, "source_channel": source.channel,
        "source_detail": source.detail, "normalized_name": normalize_name("", "", submission.name),
        "normalized_company": normalize_company(submission.company), "normalized_email": email,
        "email_domain": email_domain(email), "normalized_phone": phone,
    }


def ingest_submission(store: LeadStore, submission: FormSubmission) -> dict:
    incoming = _internal_submission(submission)
    leads = store.all_internal()
    exact = next((lead for lead in leads if incoming["normalized_email"] and lead["normalized_email"] == incoming["normalized_email"]), None)
    if exact is None and incoming["normalized_phone"]:
        exact = next(
            (lead for lead in leads if lead["normalized_phone"] == incoming["normalized_phone"]),
            None,
        )

    if exact is None:
        created = store.insert(incoming)
        return {"action": "created", "lead": created}

    notes = clean(exact["notes"])
    message = clean(submission.message)
    if message and message not in notes:
        notes = f"{notes}\n\nWebsite form: {message}" if notes else message
    fields = {
        "first_name": exact["first_name"] or incoming["first_name"],
        "last_name": exact["last_name"] or incoming["last_name"],
        "full_name": exact["full_name"] or incoming["full_name"],
        "company": exact["company"] or incoming["company"],
        "phone": exact["phone"] or incoming["phone"],
        "country": exact["country"] or incoming["country"],
        "notes": notes,
        "modified_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
        "source_channel": incoming["source_channel"], "source_detail": incoming["source_detail"],
        "normalized_name": exact["normalized_name"] or incoming["normalized_name"],
        "normalized_company": exact["normalized_company"] or incoming["normalized_company"],
        "normalized_phone": exact["normalized_phone"] or incoming["normalized_phone"],
    }
    updated = store.replace_ingest_fields(exact["id"], fields)
    return {"action": "updated", "lead": updated}
