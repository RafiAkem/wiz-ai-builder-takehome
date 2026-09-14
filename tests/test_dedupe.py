from app.dedupe import candidate_groups, generate_candidate_pairs, score_pair


def lead(identifier, name, company, email, phone="", country="Singapore"):
    normalized_email = email.casefold()
    return {
        "id": identifier,
        "normalized_name": name.casefold(),
        "normalized_company": company.casefold(),
        "normalized_email": normalized_email,
        "email_domain": normalized_email.split("@")[-1],
        "normalized_phone": phone,
        "country": country,
    }


def test_candidate_generation_finds_typo_without_all_pairs():
    leads = [
        lead(1, "Sanjay Martins", "chen digital", "sanjay.martins@chendigital.net", "525590073072"),
        lead(2, "Sanjai Martins", "chen digital", "sanjay.martin@chendigital.net", "005590073072"),
        lead(3, "Sanjay Martin", "other company", "other@example.org", "12025550101"),
    ]
    groups, compared = candidate_groups(leads, threshold=0.72)
    assert groups[0]["lead_ids"] == [1, 2]
    assert "last 8 phone digits match" in groups[0]["explanation"]
    assert compared < len(leads) * (len(leads) - 1) // 2


def test_email_localpart_fuzzy_blocking():
    leads = [
        lead(1, "Dana Cole", "first", "dana.cole@example.org"),
        lead(2, "Dana Cole", "second", "dana.colee@example.org"),
    ]
    assert generate_candidate_pairs(leads) == {(0, 1)}


def test_union_find_combines_connected_duplicate_pairs():
    leads = [
        lead(1, "Mira Tan", "acme", "mira@one.test", "11122223333"),
        lead(2, "Mira Tan", "acme", "mira@two.test", "00022223333"),
        lead(3, "Mira Tann", "acme", "mira@three.test", "99988887777"),
    ]
    groups, _ = candidate_groups(leads, threshold=0.72)
    assert groups[0]["lead_ids"] == [1, 2, 3]
    assert len(groups[0]["pairs"]) >= 2


def test_same_company_similar_name_with_conflicts_stays_below_threshold():
    left = lead(1, "Alex Lee", "acme", "alex@acme.test", "1111111111")
    right = lead(2, "Alex Lee", "acme", "someone@acme.test", "9999999999")
    score, _ = score_pair(left, right)
    assert score < 0.8
