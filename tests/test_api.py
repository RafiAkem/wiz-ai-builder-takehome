import csv
import io


def test_list_filter_search_patch_and_export(client):
    response = client.get("/leads", params={"status": "new", "q": "Yuki Aina"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == 100234811

    updated = client.patch("/leads/100234811", json={"status": "qualified", "owner": "Ada Lovelace"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "Qualified"
    assert updated.json()["owner"] == "Ada Lovelace"

    exported = client.get("/leads/export", params={"owner": "Ada Lovelace"})
    assert exported.status_code == 200
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert [int(row["id"]) for row in rows] == [100234811]


def test_detail_validation_and_dashboard(client):
    assert client.get("/leads/999").status_code == 404
    assert client.patch("/leads/100234811", json={}).status_code == 422
    assert client.patch("/leads/100234811", json={"email": "bad@example.com"}).status_code == 422
    assert client.patch("/leads/100234811", json={"status": "invented"}).status_code == 422

    dashboard = client.get("/dashboard").json()
    assert dashboard["total"] == 2049
    assert sum(dashboard["by_status"].values()) == 2049
    assert sum(dashboard["by_source_channel"].values()) == 2049


def test_dashboard_ui_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Lead Management Dashboard" in response.text


AMBIGUOUS_NOTE = {"text": "Introduced through our ecosystem."}


def test_extract_endpoint_reports_trace_and_throttles(client, monkeypatch):
    monkeypatch.setenv("LLM_RATE_PER_IP_HOUR", "2")
    monkeypatch.setenv("LLM_RATE_GLOBAL_DAY", "800")
    headers = {"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "10.0.0.1, 203.0.113.9"}

    hit = client.post("/source/extract", json={"text": "Scanned our QR code at the SaaStr Annual booth."}, headers=headers)
    assert hit.status_code == 200
    assert (hit.json()["answered_by"], hit.json()["model"], hit.json()["llm_throttled"]) == ("rules", None, False)
    assert client.get("/llm-budget").json()["global_used_today"] == 0  # rules hits never consume

    seen = []
    for _ in range(4):
        response = client.post("/source/extract", json=AMBIGUOUS_NOTE, headers=headers)
        assert response.status_code == 200
        seen.append((response.json()["answered_by"], response.json()["llm_throttled"]))
    assert seen == [("fallback", False), ("fallback", False), ("fallback", True), ("fallback", True)]

    assert client.get("/llm-budget").json() == {
        "per_ip_hour_limit": 2, "global_day_limit": 800, "global_used_today": 2,
        "mode": "mock", "model": None,
    }


DEDUPE_LEAD_FIELDS = {
    "id", "full_name", "company", "email", "phone", "status", "country", "notes",
    "source_channel", "source_detail",
}


def test_dedupe_groups_embed_pair_reasons_and_leads(client):
    response = client.post("/leads/dedupe-candidates", json={"limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 235
    assert body["candidate_pairs_compared"] == 328
    assert body["compared_population"] == 2049
    assert body["returned"] == 5
    assert body["truncated"] is True

    for group in body["items"]:
        assert group["explanation"]
        for pair in group["pairs"]:
            assert pair["reasons"] and all(isinstance(reason, str) for reason in pair["reasons"])
            assert len(pair["leads"]) == 2
            assert [lead["id"] for lead in pair["leads"]] == pair["lead_ids"]
            for lead in pair["leads"]:
                assert set(lead) == DEDUPE_LEAD_FIELDS
                assert lead["full_name"] and lead["email"] and lead["company"]


def test_dedupe_limit_truncates_but_count_is_total(client):
    limited = client.post("/leads/dedupe-candidates", json={"limit": 5}).json()
    full = client.post("/leads/dedupe-candidates", json={"limit": 500}).json()
    assert limited["count"] == full["count"] == 235
    assert (limited["returned"], limited["truncated"]) == (5, True)
    assert (full["returned"], full["truncated"]) == (235, False)
    assert [group["lead_ids"] for group in limited["items"]] == [group["lead_ids"] for group in full["items"][:5]]
    confidences = [group["confidence"] for group in full["items"]]
    assert confidences == sorted(confidences, reverse=True)

    assert client.post("/leads/dedupe-candidates", json={"limit": 0}).status_code == 422
    assert client.post("/leads/dedupe-candidates", json={"limit": 501}).status_code == 422


def test_rotating_forwarded_for_cannot_evade_the_per_ip_limit(client, monkeypatch):
    """A forged X-Forwarded-For prefix must not mint a fresh bucket per request.

    nginx overwrites X-Real-IP with the real peer and appends that peer to
    X-Forwarded-For, so only the last hop is trustworthy.
    """
    monkeypatch.setenv("LLM_RATE_PER_IP_HOUR", "1")
    monkeypatch.setenv("LLM_RATE_GLOBAL_DAY", "800")

    throttled = []
    for spoofed in range(4):
        response = client.post(
            "/source/extract",
            json=AMBIGUOUS_NOTE,
            headers={"X-Real-IP": "198.51.100.7", "X-Forwarded-For": f"10.0.0.{spoofed}, 198.51.100.7"},
        )
        assert response.status_code == 200
        throttled.append(response.json()["llm_throttled"])
    assert throttled == [False, True, True, True], "rotating the forged prefix minted new buckets"

    no_real_ip = [
        client.post(
            "/source/extract",
            json=AMBIGUOUS_NOTE,
            headers={"X-Forwarded-For": f"172.16.0.{spoofed}, 203.0.113.55"},
        ).json()["llm_throttled"]
        for spoofed in range(3)
    ]
    assert no_real_ip == [False, True, True], "last XFF hop was not used when X-Real-IP is absent"
