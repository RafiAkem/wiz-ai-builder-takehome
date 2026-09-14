def submission(**overrides):
    payload = {
        "form_id": "form_demo_request",
        "form_name": "Book a Demo",
        "page_url": "/book-a-demo",
        "submitted_at": "2026-09-14T12:00:00Z",
        "name": "Yuki Aina",
        "email": "Y.AINA@SINGHLOGISTICS.IO",
        "phone": "+86 138 2424 7912",
        "company": "Singh Logistics and Co.",
        "country": "China",
        "message": "Asked for a product demonstration.",
    }
    payload.update(overrides)
    return payload


def test_ingest_updates_exact_email_and_preserves_notes(client):
    before = client.get("/leads/100234811").json()
    response = client.post("/leads/ingest", json=submission())
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["action"] == "updated"
    assert result["lead"]["id"] == 100234811
    assert before["notes"] in result["lead"]["notes"]
    assert "product demonstration" in result["lead"]["notes"]


def test_reingesting_submission_is_idempotent(client):
    first = client.post("/leads/ingest", json=submission()).json()["results"][0]
    total_after_first = client.get("/dashboard").json()["total"]
    second = client.post("/leads/ingest", json=submission()).json()["results"][0]
    assert first["action"] == "updated"
    assert second["action"] == "updated"
    assert second["lead"]["id"] == first["lead"]["id"]
    assert client.get("/dashboard").json()["total"] == total_after_first


def test_ingest_updates_on_normalized_phone_match(client):
    result = client.post("/leads/ingest", json=submission(
        email="changed@example.org", phone="86138-2424-7912"
    )).json()["results"][0]
    assert result["action"] == "updated"
    assert result["lead"]["id"] == 100234811


def test_ingest_creates_when_identity_evidence_is_weak(client):
    response = client.post("/leads/ingest", json=submission(
        name="Yuki Aina", email="different.person@example.org", phone="+1 202 555 0199"
    ))
    result = response.json()["results"][0]
    assert result["action"] == "created"
    assert result["lead"]["email"] == "different.person@example.org"
    assert client.get("/dashboard").json()["total"] == 2050
