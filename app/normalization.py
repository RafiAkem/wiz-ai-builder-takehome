import re
from datetime import datetime, timezone
from email.utils import parseaddr

STATUS_LABELS = {
    "new": "New",
    "contacted": "Contacted",
    "connected": "Connected",
    "qualified": "Qualified",
    "opportunity": "Opportunity",
    "closed won": "Closed Won",
    "closed lost": "Closed Lost",
}
COMPANY_SUFFIXES = {
    "ab", "and", "co", "company", "corp", "corporation", "gmbh", "holdings",
    "inc", "incorporated", "limited", "llc", "ltd", "plc", "pte", "sa", "studio",
}


def clean(value: object | None) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_text(value: object | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean(value).casefold()).strip()


def normalize_status(value: object | None) -> str:
    normalized = normalize_text(value)
    return STATUS_LABELS.get(normalized, clean(value).title())


def normalize_email(value: object | None) -> str:
    address = parseaddr(clean(value))[1].casefold()
    return address if "@" in address else ""


def email_domain(value: object | None) -> str:
    email = normalize_email(value)
    return email.rsplit("@", 1)[1] if email else ""


def normalize_phone(value: object | None) -> str:
    digits = re.sub(r"\D", "", clean(value))
    return digits if len(digits) >= 7 else ""


def normalize_company(value: object | None) -> str:
    tokens = normalize_text(value).split()
    meaningful = [token for token in tokens if token not in COMPANY_SUFFIXES]
    return " ".join(meaningful or tokens)


def split_name(full_name: object | None) -> tuple[str, str]:
    parts = clean(full_name).split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def display_name(first_name: object | None, last_name: object | None, full_name: object | None = None) -> str:
    return clean(full_name) or clean(f"{clean(first_name)} {clean(last_name)}")


def normalize_name(first_name: object | None, last_name: object | None, full_name: object | None = None) -> str:
    return normalize_text(display_name(first_name, last_name, full_name))


def parse_date(value: object | None) -> str | None:
    raw = clean(value)
    if not raw:
        return None
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            parsed = datetime.strptime(raw, "%m/%d/%Y")
        except ValueError as exc:
            raise ValueError(f"Unsupported date format: {raw}") from exc
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat(timespec="seconds")
