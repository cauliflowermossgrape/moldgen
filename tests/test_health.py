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


def test_workdir_resolution(monkeypatch, tmp_path):
    import moldgen.main as main

    # explicit override wins
    monkeypatch.setenv("MOLDGEN_WORKDIR", str(tmp_path / "custom"))
    assert main._default_workdir() == tmp_path / "custom"
    monkeypatch.delenv("MOLDGEN_WORKDIR")
    # a source checkout keeps ./workdir (the dev workflow)
    assert main._default_workdir() == main.ROOT / "workdir"
    # installed / packaged: never inside the install location
    monkeypatch.setattr(main, "ROOT", tmp_path / "site-packages")
    monkeypatch.setattr(main.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert main._default_workdir() == tmp_path / "data" / "moldgen" / "workdir"
