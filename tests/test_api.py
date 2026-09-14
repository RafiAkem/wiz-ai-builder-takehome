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
