from collections import defaultdict

from rapidfuzz.fuzz import ratio


EMAIL_LOCALPART_THRESHOLD = 80.0
COMPANY_NAME_THRESHOLD = 70.0
MAX_BLOCK_SIZE = 50

#: Public subset of a lead, same names as GET /leads items so the UI can render it directly.
#: Internal scoring fields (normalized_*, email_domain) are deliberately not exposed.
LEAD_PAYLOAD_FIELDS = (
    "id", "full_name", "company", "email", "phone", "status", "country", "notes",
    "source_channel", "source_detail",
)


def lead_payload(lead: dict) -> dict:
    return {field: lead.get(field) for field in LEAD_PAYLOAD_FIELDS}


def similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return ratio(left, right) / 100.0


def _email_localpart(lead: dict) -> str:
    email = lead["normalized_email"]
    return email.split("@", 1)[0] if "@" in email else ""


def score_pair(left: dict, right: dict) -> tuple[float, list[str]]:
    name = similarity(left["normalized_name"], right["normalized_name"])
    company = similarity(left["normalized_company"], right["normalized_company"])
    email = similarity(_email_localpart(left), _email_localpart(right))
    left_phone = left["normalized_phone"][-8:]
    right_phone = right["normalized_phone"][-8:]
    phone_match = bool(left_phone and left_phone == right_phone)
    exact_email = bool(left["normalized_email"] and left["normalized_email"] == right["normalized_email"])

    if exact_email:
        score = 0.99
    elif phone_match:
        score = 0.68 + 0.2 * name + 0.08 * company
    else:
        score = 0.5 * name + 0.27 * company + 0.23 * email
        if left["country"] and right["country"] and left["country"].casefold() == right["country"].casefold():
            score += 0.03
        if left_phone and right_phone and left_phone != right_phone:
            score -= 0.18

    reasons = []
    if exact_email:
        reasons.append("email addresses match exactly")
    elif email >= 0.8:
        reasons.append(f"email local parts are {email:.0%} similar")
    if phone_match:
        reasons.append("last 8 phone digits match")
    if name >= 0.75:
        reasons.append(f"names are {name:.0%} similar")
    if company >= 0.75:
        reasons.append(f"company names are {company:.0%} similar")
    return min(max(score, 0.0), 1.0), reasons


def generate_candidate_pairs(leads: list[dict]) -> set[tuple[int, int]]:
    phone_blocks: dict[str, list[int]] = defaultdict(list)
    domain_blocks: dict[str, list[int]] = defaultdict(list)
    company_blocks: dict[str, list[int]] = defaultdict(list)

    for index, lead in enumerate(leads):
        phone = lead["normalized_phone"][-8:]
        if phone:
            phone_blocks[phone].append(index)
        if lead["email_domain"]:
            domain_blocks[lead["email_domain"]].append(index)
        if lead["normalized_company"]:
            company_blocks[lead["normalized_company"]].append(index)

    candidates: set[tuple[int, int]] = set()

    def add_pairs(indexes: list[int], predicate) -> None:
        if len(indexes) > MAX_BLOCK_SIZE:
            return
        for position, left_index in enumerate(indexes):
            for right_index in indexes[position + 1:]:
                if predicate(leads[left_index], leads[right_index]):
                    candidates.add((left_index, right_index))

    for indexes in phone_blocks.values():
        add_pairs(indexes, lambda _left, _right: True)
    for indexes in domain_blocks.values():
        add_pairs(
            indexes,
            lambda left, right: ratio(_email_localpart(left), _email_localpart(right)) >= EMAIL_LOCALPART_THRESHOLD,
        )
    for indexes in company_blocks.values():
        add_pairs(
            indexes,
            lambda left, right: ratio(left["normalized_name"], right["normalized_name"]) >= COMPANY_NAME_THRESHOLD,
        )
    return candidates


def candidate_groups(leads: list[dict], threshold: float = 0.72) -> tuple[list[dict], int]:
    candidates = generate_candidate_pairs(leads)
    accepted: list[tuple[int, int, float, list[str]]] = []
    parent = list(range(len(leads)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, right_index in candidates:
        score, reasons = score_pair(leads[left_index], leads[right_index])
        if score >= threshold:
            accepted.append((left_index, right_index, score, reasons))
            union(left_index, right_index)

    members: dict[int, set[int]] = defaultdict(set)
    for left_index, right_index, _score, _reasons in accepted:
        root = find(left_index)
        members[root].update((left_index, right_index))

    groups = []
    for indexes in members.values():
        group_pairs = [pair for pair in accepted if pair[0] in indexes and pair[1] in indexes]
        best_score = max(pair[2] for pair in group_pairs)
        explanations = list(dict.fromkeys(reason for pair in group_pairs for reason in pair[3]))
        groups.append({
            "lead_ids": sorted(leads[index]["id"] for index in indexes),
            "confidence": round(best_score, 3),
            "explanation": explanations,
            "pairs": [
                {
                    "lead_ids": [leads[left]["id"], leads[right]["id"]],
                    "confidence": round(score, 3),
                    "reasons": reasons,
                    "leads": [lead_payload(leads[left]), lead_payload(leads[right])],
                }
                for left, right, score, reasons in sorted(group_pairs)
            ],
        })
    return sorted(groups, key=lambda item: (-item["confidence"], item["lead_ids"])), len(candidates)


def candidate_pairs(leads: list[dict], threshold: float = 0.72) -> list[dict]:
    groups, _candidate_count = candidate_groups(leads, threshold)
    return groups
