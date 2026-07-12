from fastapi.testclient import TestClient

from moldgen.main import app


def test_health():
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_frontend_served():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "moldgen" in resp.text
