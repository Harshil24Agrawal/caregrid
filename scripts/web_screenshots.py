"""Full-page screenshots of every web/ page as Asha, Rahul and Meera -> docs/screenshots/.
Builds a throw-away world (mock LLM), drives a few requests through the real API so the pages have content, and uses the installed Chrome.
Run: python scripts/web_screenshots.py"""
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("CAREGRID_NO_DOTENV", "1")
os.environ["LLM_PROVIDER"] = "mock"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from caregrid import api, config  # noqa: E402
from caregrid.admin import reset_demo  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
H = lambda u: {"X-CareGrid-User": u}  # noqa: E731
S1 = "What supporting documents are accepted for provider record changes?"
S2 = "Dr. Ramesh Iyer wants to update his billing address to 14 Lake Road, Chennai. NPI 123456789."
S3 = "A clinic staff member is locked out of the provider portal, email staff@clinic.example, provider NPI 1234567890. Can we reset it?"
S4A = "Should this patient double her insulin dose?"
S6A = "Provider NPI 1234567890 legally changed name from Priya Nair to Priya Menon, W-9 attached."


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="cg_shots_"))
    for name, val in (("DATA_DIR", tmp / "data"), ("BRAIN_DIR", tmp / "brain"), ("EVAL_DIR", ROOT / "eval"), ("DB_PATH", tmp / "db.sqlite")):
        setattr(config, name, val)
    api.reset_process_state()
    reset_demo()
    c = TestClient(api.app)
    for text in (S1, S2, S4A):
        c.post("/api/requests", headers=H("U1"), json={"text": text})
    c.post("/api/cases/CASE-1024/decision", headers=H("U4"), json={"action": "approve", "channels": ["email", "whatsapp"], "contact_email": "dme.desk@clinic-supplies.example"})
    s6 = c.post("/api/requests", headers=H("U1"), json={"text": S6A}).json()["case"]["id"]
    c.post(f"/api/cases/{s6}/decision", headers=H("U2"), json={"action": "approve"})
    s3 = c.post("/api/requests", headers=H("U1"), json={"text": S3}).json()["case"]["id"]
    c.post(f"/api/cases/{s3}/decision", headers=H("U7"), json={"action": "approve", "propose_pr": True, "note": "KA-32 is superseded by KA-31; retire it.",
                                                                "meta_changes": {"retire": True, "target_page": "KA-32"}})
    c.post("/api/requests", headers=H("U1"), json={"text": S3})                       # a case waiting for review again
    hid = c.get("/api/demo/samples", headers=H("U1")).json()["health_id_valid"]
    c.post("/api/requests", headers=H("U1"), json={"text": f"{S1} Patient {hid}."})   # S8: linked to a patient, so the timeline has a new row
    c.post("/api/requests", headers=H("U1"), json={"text": "How do I change a provider's billing address?"})   # a how-to answered from WF-03

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.1)
    base = f"http://127.0.0.1:{port}"
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()

    pages = [("dashboard", "/index.html", ".kpi"), ("new-request", "/intake.html", "#req-text"), ("cases", "/case.html", "#rows"),
             ("case-CASE-1024", "/case.html?case=CASE-1024", "#tab-body"), ("knowledge-pages", "/knowledge.html?page=KA-12&v=3", "#pdetail .card h2"),
             ("knowledge-attention", "/knowledge.html?tab=lint", "#body .card"), ("knowledge-changes", "/knowledge.html?tab=prs", "#body .card"),
             ("audit-log", "/audit.html", "#table table"), ("audit-messages", "/audit.html?tab=messages", "#msgs"),
             ("patients", "/patients.html", "#lookup"), ("patient-record", "/patients.html?ref=PRF-2001", "#content .card")]
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        for uid, who in (("U1", "asha"), ("U4", "rahul"), ("U5", "meera")):
            ctx = b.new_context(viewport={"width": 1360, "height": 900})
            ctx.add_init_script(f"try{{localStorage.setItem('cg_user','{uid}')}}catch(e){{}}")
            pg = ctx.new_page()
            for name, path, sel in pages:
                pg.goto(base + path)
                pg.wait_for_selector(sel, timeout=20000)
                if name == "new-request":                                        # show a finished result, not an empty box
                    pg.fill("#req-text", S2)
                    pg.click("#submit")
                    pg.wait_for_selector("#run a.btn.primary", timeout=20000)
                if name == "case-CASE-1024":
                    pg.click("#assistant [data-q='Why is this case flagged?']")
                    pg.wait_for_function("document.querySelectorAll('#chat .msg.bot').length >= 1 && !document.querySelector('#chat .spinner')", timeout=20000)
                pg.wait_for_timeout(500)
                pg.screenshot(path=str(OUT / f"{who}_{name}.png"), full_page=True)
            ctx.close()
        b.close()
    srv.should_exit = True
    print(f"wrote {len(list(OUT.glob('*.png')))} screenshots to {OUT}")


if __name__ == "__main__":
    main()
