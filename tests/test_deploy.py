"""Deployment: health, the optional access code gate (constant-time, rate-limited, never echoed), first-start seeding."""
from fastapi.testclient import TestClient

from caregrid import api
from tests.test_api import H, client  # noqa: F401


def test_health_is_open_and_holds_no_data(client, monkeypatch):
    monkeypatch.setenv("APP_ACCESS_CODE", "s3cret-code")
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "demo_mode": True}


def test_without_a_code_nothing_is_gated(client, monkeypatch):
    monkeypatch.delenv("APP_ACCESS_CODE", raising=False)
    assert client.get("/api/cases", headers=H["asha"]).status_code == 200 and client.get("/index.html").status_code == 200


def test_with_a_code_the_ui_and_api_are_gated_until_the_right_code(client, monkeypatch):
    monkeypatch.setenv("APP_ACCESS_CODE", "s3cret-code")
    fresh = TestClient(api.app)
    page = fresh.get("/index.html")
    assert page.status_code == 401 and "Enter the access code" in page.text and "s3cret-code" not in page.text
    assert fresh.get("/api/cases", headers=H["asha"]).status_code == 401 and fresh.get("/api/users").status_code == 401
    bad = fresh.post("/api/gate", json={"code": "nope"})
    assert bad.status_code == 401 and "nope" not in bad.text and "s3cret" not in bad.text and not bad.cookies
    ok = fresh.post("/api/gate", json={"code": "s3cret-code"})
    assert ok.status_code == 200 and "httponly" in ok.headers["set-cookie"].lower() and "s3cret-code" not in ok.headers["set-cookie"]
    assert fresh.get("/api/cases", headers=H["asha"]).status_code == 200 and fresh.get("/index.html").status_code == 200


def test_the_gate_is_rate_limited(client, monkeypatch):
    monkeypatch.setenv("APP_ACCESS_CODE", "s3cret-code")
    api._gate_attempts.clear()
    fresh = TestClient(api.app)
    codes = [fresh.post("/api/gate", json={"code": f"guess{i}"}).status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
    assert fresh.post("/api/gate", json={"code": "s3cret-code"}).status_code == 429             # even the right code waits
    api._gate_attempts.clear()


def test_first_start_seeds_an_empty_deployment(tmp_path, monkeypatch):
    from caregrid import config

    for name, val in (("DATA_DIR", tmp_path / "data"), ("BRAIN_DIR", tmp_path / "brain"), ("EVAL_DIR", tmp_path / "eval"), ("DB_PATH", tmp_path / "db.sqlite")):
        monkeypatch.setattr(config, name, val)
    api.reset_process_state()
    api._first_start()
    assert (tmp_path / "db.sqlite").exists() and (tmp_path / "brain" / "index.md").exists()
    assert len(api.get_store().list_cases()) > 20
    api.reset_process_state()
