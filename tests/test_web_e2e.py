"""Browser end-to-end test of web/ (real Chrome via Playwright) against the real API on a throw-away world: S1-S7 by clicking.
Skipped when Playwright or Chrome is not installed."""
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from caregrid import api, config  # noqa: E402
from caregrid.admin import reset_demo  # noqa: E402

S1 = 0
S2, S3, S4A, S4B, S6 = 1, 2, 3, 4, 5            # indexes of the example buttons on the intake page
T = 20000


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn

    root = tmp_path_factory.mktemp("web")
    mp = pytest.MonkeyPatch()
    for name, val in (("DATA_DIR", root / "data"), ("BRAIN_DIR", root / "brain"), ("EVAL_DIR", root / "eval"), ("DB_PATH", root / "db.sqlite"),
                      ("LLM_PROVIDER", "mock")):
        mp.setattr(config, name, val)
    api.reset_process_state()
    reset_demo()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)
    api.reset_process_state()
    mp.undo()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chrome is not available for Playwright: {e}")
        yield b
        b.close()


class Web:
    def __init__(self, browser, base):
        self.ctx = browser.new_context()
        self.page = self.ctx.new_page()
        self.base = base
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(f"JS error: {e}"))
        self.page.on("response", lambda r: self.errors.append(f"{r.status} {r.url}") if r.status >= 500 else None)
        self.page.goto(base + "/index.html")

    def as_user(self, uid):
        self.page.evaluate(f"localStorage.setItem('cg_user','{uid}')")
        return self

    def go(self, path, wait):
        self.page.goto(self.base + path)
        self.page.wait_for_selector(wait, timeout=T)
        return self.page

    def submit(self, example, uid="U1"):
        self.as_user(uid)
        p = self.go("/intake.html", "#examples button")
        p.click(f"#examples button[data-i='{example}']")
        p.click("#submit")
        p.wait_for_selector("#result a.btn-primary", timeout=T)
        return p


@pytest.fixture(scope="module")
def web(browser, server):
    w = Web(browser, server)
    yield w
    assert not w.errors, w.errors


def text(p, sel="#content"):
    return p.inner_text(sel)


def test_every_page_loads_for_every_role_without_errors(web):
    for uid in ("U1", "U2", "U3", "U4", "U5", "U6", "U7"):
        web.as_user(uid)
        for path, sel in (("/index.html", ".tile"), ("/intake.html", "#examples button"), ("/case.html", "#case-root .tile, #content .card"),
                          ("/approval.html", "#packet, #queue"), ("/audit.html", "#table"), ("/knowledge.html", "#plist a"), ("/comms.html", "#list")):
            p = web.go(path, sel)
            assert "Traceback" not in text(p), (uid, path)
        sidebar = text(web.page, "#app-sidebar")
        assert "LLM: mock" in sidebar and "Reset demo" in sidebar


def test_s1_answered_and_input_cleared(web):
    p = web.submit(S1)
    t = text(p, "#result")
    assert "Answered automatically" in t and "KA-02" in t and "HIGH" in t
    assert p.input_value("#req-text") == ""


def case_id_of(p):
    return p.eval_on_selector("#result a.btn-primary", "a => a.href").split("=")[-1]


def test_s2_one_numbered_message_with_masked_chips(web):
    p = web.submit(S2)
    t = text(p, "#result")
    assert "ONE message" in t and "1." in t and "2." in t and "3." in t and "masked before storage" in t.lower()
    assert "Ramesh" not in t and "Lake Road" not in t
    web.go("/case.html?case=" + case_id_of(p), "#case-root .tile")
    assert "TEAM-ENROLL" in text(web.page)


def test_s3_conflict_and_s4_refusals(web):
    p = web.submit(S3)
    t = text(p, "#result")
    assert "human review" in t and "TEAM-IT" in t and "POLICY_CONFLICT" in t
    cid = case_id_of(p)
    web.as_user("U7")
    p = web.go("/case.html?case=" + cid, "#case-root .tile")
    p.click(".tab[data-t=recommendation]")
    t = text(p)
    assert "KA-31" in t and "KA-32" in t and "Band capped at Medium" in t
    p = web.submit(S4A)
    t = text(p, "#result")
    assert "cannot be answered" in t and "TEAM-CLINICAL" in t and "CLINICAL" in t
    p = web.submit(S4B)
    t = text(p, "#result")
    assert "cannot be answered" in t and "ACCESS_DENIED" in t and "TEAM-COMPLIANCE" in t


def test_s5_approval_assistant_rbac_comms_and_amount(web):
    web.as_user("U4")
    p = web.go("/case.html?case=CASE-1024", "#case-root .tile")
    p.click("#assistant [data-q='Why is this case flagged?']")
    p.wait_for_selector("#chat .msg-bot", timeout=T)
    p.wait_for_function("document.querySelectorAll('#chat .msg-bot').length >= 1 && !document.querySelector('#chat .spinner')", timeout=T)
    chat = text(p, "#chat")
    assert "KA-40" in chat and "INV-1024" in chat and "62,500" in chat
    # Asha: assistant refuses, page never shows the amount
    web.as_user("U1")
    p = web.go("/case.html?case=CASE-1024", "#case-root .tile")
    p.click(".tab[data-t=evidence]")
    assert "ACCESS RESTRICTED" in text(p, "#tab-body") and "62,500" not in text(p)
    p.fill("#ask-input", "What is the amount?")
    p.press("#ask-input", "Enter")
    p.wait_for_function("document.querySelectorAll('#chat .msg-bot').length >= 1 && !document.querySelector('#chat .spinner')", timeout=T)
    assert "ACCESS RESTRICTED" in text(p, "#chat") and "62,500" not in text(p)
    p = web.go("/approval.html?case=CASE-1024", "#packet .card")
    assert p.is_disabled("#go") and "disabled" in text(p, "#packet")                # the server's reason is shown
    # Rahul approves with email + WhatsApp
    web.as_user("U4")
    p = web.go("/approval.html?case=CASE-1024", "#go")
    assert not p.is_disabled("#go")
    p.check(".chan[value=email]")
    p.check(".chan[value=whatsapp]")
    p.fill("#email", "dme.desk@clinic-supplies.example")
    p.fill("#note", "Cost confirmed against the vendor quote.")
    p.click("#go")
    p.wait_for_selector("text=Decision recorded", timeout=T)
    t = text(p, "#packet")
    low = t.lower()
    assert "notified" in low and "precedent" in low and "level" in low and "email" in low and "whatsapp" in low
    p = web.go("/comms.html", "#list tr[data-i]")
    assert "simulated" in text(p, "#list")


def test_s6_compounding_and_trust(web):
    p = web.submit(S6)
    cid = case_id_of(p)
    web.as_user("U2")
    p = web.go(f"/approval.html?case={cid}", "#go")
    p.click("#go")
    p.wait_for_selector("text=Decision recorded", timeout=T)
    assert "P-" in text(p, "#packet")
    prec = p.inner_text("#packet .mono >> nth=0")
    assert prec
    web.as_user("U1")
    p = web.go("/intake.html", "#examples button")
    p.fill("#req-text", "Provider NPI 1098765437 legally changed name from Arun Pillai to Arun Menon, bank letter attached.")
    p.click("#submit")
    p.wait_for_selector("#result a.btn-primary", timeout=T)
    assert "P-" in text(p, "#result") or "HUMAN" in text(p, "#result") or "human review" in text(p, "#result")
    dash = web.go("/index.html", "#queue-body, .tile")
    assert "provider_name_change" in text(dash)


def test_s7_pr_loop_in_the_browser(web):
    p = web.submit(S3)
    cid = case_id_of(p)
    web.as_user("U7")
    p = web.go(f"/approval.html?case={cid}", "#go")
    p.check("#pr")
    p.select_option("#pr-target", "KA-32")
    p.check("#pr-retire")
    p.fill("#note", "KA-32 is superseded by KA-31; retire it.")
    p.click("#go")
    p.wait_for_selector("text=Decision recorded", timeout=T)
    assert "knowledge pr opened" in text(p, "#packet").lower()
    web.as_user("U7")
    p = web.go("/knowledge.html?tab=prs", "text=Open PRs")
    assert p.is_disabled("button[data-act=approve]")
    web.as_user("U5")
    p = web.go("/knowledge.html?tab=prs", "button[data-act=approve]")
    assert "retire" in text(p) and "KA-32" in text(p)
    assert not p.is_disabled("button[data-act=approve]")
    p.click("button[data-act=approve]")
    p.click("#m-ok")
    p.wait_for_selector("text=Decided (1)", timeout=T)
    p.click(".tab[data-t=lint]")
    p.wait_for_selector("#lint-out >> text=finding", timeout=T)
    assert "CONTRADICTION" not in text(p, "#lint-out")
    p = web.submit(S3)
    assert "POLICY_CONFLICT" not in text(p, "#result")


def test_reset_demo_button_asks_for_confirmation(web):
    web.as_user("U3")
    p = web.go("/index.html", ".tile")
    p.click("#reset-demo")
    p.wait_for_selector("#modal-root .dialog", timeout=T)
    p.click("#m-cancel")
    assert p.query_selector("#modal-root .dialog") is None
    p.click("#reset-demo")
    p.click("#m-ok")
    p.wait_for_url("**/index.html", timeout=T)
    p.wait_for_selector(".tile", timeout=T)
    assert "CASE-1024" in text(web.go("/case.html?case=CASE-1024", "#case-root .tile"))
