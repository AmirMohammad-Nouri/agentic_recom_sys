"""Configurable criteria and interactions, LLM providers, study population and login, and
reloading when the config file changes."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from conftest import FIXTURE, OfflineLLM
from fastapi.testclient import TestClient

from foodrec.api import create_app
from foodrec.appconfig import AppConfig, defaults
from foodrec.config import load_settings
from foodrec.model_spec import DEFAULT_CRITERIA, DEFAULT_INTERACTIONS, normalize
from foodrec.reload import ConfigWatcher
from foodrec.study import Sessions, allocate, generate

ENERGY = {"taste_share": 0.2, "items": [
    {"kind": "healthiness", "enabled": False}, {"kind": "sustainability", "default_weight": 0.4},
    {"kind": "convenience", "default_weight": 0.3},
    {"id": "e", "kind": "field", "label": "Low energy", "field": "nutrition_per_100g.kcal", "direction": "lower",
     "best": 80, "worst": 400, "default_weight": 0.3, "keywords": ["energy", "calorie"]}]}


# ---- criteria and interactions ---------------------------------------------------------------------
def test_default_spec_is_valid_and_unchanged():
    spec, errs = normalize()
    assert errs == []
    assert [c["id"] for c in spec["criteria"]["items"]] == ["h", "s", "t"]
    assert {r["id"] for r in spec["interactions"]["reject_reasons"]} >= {"not_my_taste", "had_recently", "other"}


def test_spec_validation_and_auto_reasons():
    spec, errs = normalize(ENERGY, DEFAULT_INTERACTIONS)
    assert errs == []
    ids = {c["id"] for c in spec["criteria"]["items"] if c["enabled"]}
    assert ids == {"s", "t", "e"}
    choose = {r["id"]: r for r in spec["interactions"]["choose_reasons"]}
    assert "health" not in choose                      # its criterion is switched off
    assert choose["good_e"]["criterion"] == "e" and choose["good_e"]["auto"]
    bad = {"items": [{"id": "h", "kind": "field", "field": "x", "best": 1, "worst": 1},
                     {"id": "q", "kind": "field", "best": 0, "worst": 1},
                     {"kind": "convenience", "id": "zz"}]}
    errs = " | ".join(normalize(bad, None)[1])
    assert "not p, h, s or t" in errs and "needs 'field'" in errs and "always has the id 't'" in errs


def test_custom_criteria_end_to_end_in_a_subprocess(tmp_path):
    """Criteria are fixed at import, so a run with other criteria needs its own process."""
    code = f"""
import json, sys
sys.path.insert(0, {str(FIXTURE.parent.parent.parent / 'tests')!r})
from conftest import OfflineLLM, FIXTURE
from foodrec import VALUE_CRITERIA, CRITERION_LABELS
from foodrec.appconfig import AppConfig
from foodrec.config import load_settings
from foodrec.service import App
app = App(load_settings(config=AppConfig({{}}), data_dir={str(tmp_path)!r}, qdrant="memory", catalogue_path=FIXTURE), llm=OfflineLLM())
app.onboard("u", {{}}, {{"s": .2, "t": .2, "e": .6}})
res = app.recommend("u")
top = res["results"][0]
app.feedback("u", res["request_id"], top["recipe_id"], "choose", "good_e")
print(json.dumps({{"crit": VALUE_CRITERIA, "label": CRITERION_LABELS["e"], "scores": sorted(top["scores"]),
                  "bd": top["breakdown"]["e"]["field"], "observed": sorted(app.learner("u").values.observed)}}))
"""
    env = {**os.environ, "FOODREC_MODEL_SPEC": json.dumps({"criteria": ENERGY})}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["crit"] == ["s", "t", "e"] and data["label"] == "Low energy"
    assert data["scores"] == ["e", "p", "s", "t"] and data["bd"] == "nutrition_per_100g.kcal"
    assert data["observed"] == ["e", "s", "t"]


def test_saved_weights_conform_to_the_criteria(onboarded):
    from foodrec.ranker import conform_weights
    assert conform_weights({"h": 2, "s": 1, "t": 1, "gone": 5}) == {"h": .5, "s": .25, "t": .25}
    assert sum(conform_weights({}).values()) == pytest.approx(1.0)


def test_unknown_reason_is_rejected(onboarded):
    res = onboarded.recommend("u")
    with pytest.raises(ValueError, match="not configured"):
        onboarded.feedback("u", res["request_id"], res["results"][0]["recipe_id"], "choose", "because")


# ---- LLM providers ---------------------------------------------------------------------------------
def test_openai_compatible_client_falls_back_to_json_object(tmp_path):
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "m1"}]}).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append(body.get("response_format", {}).get("type"))
            if body.get("response_format", {}).get("type") == "json_schema":
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error": "json_schema not supported"}')
                return
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for part in ('{"intent": ', '"help"}'):
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': part}}]})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from foodrec.llm import make_llm
        s = load_settings(config=AppConfig({}), data_dir=tmp_path, llm_provider="openai", llm_model="m1",
                          llm_base_url=f"http://127.0.0.1:{srv.server_port}/v1")
        llm = make_llm(s)
        assert llm.status(refresh=True)["available"]
        out = llm.chat([{"role": "user", "content": "hi"}], schema={"type": "object"})
        assert out["data"] == {"intent": "help"} and seen == ["json_schema", "json_object"]
        llm.chat([{"role": "user", "content": "again"}], schema={"type": "object"})
        assert seen[-1] == "json_object"        # the working mode is remembered
    finally:
        srv.shutdown()


def test_provider_change_applies_live(tmp_path):
    from foodrec.service import App
    s = load_settings(config=AppConfig({}), data_dir=tmp_path, qdrant="memory", catalogue_path=FIXTURE, llm_enabled=False,
                      embed_provider="none")
    app = App(s)                                   # a real client (disabled), not a test double
    assert app.llm.provider == "ollama"
    pending = app.apply_config(AppConfig({"llm": {"provider": "openai", "model": "gpt-x", "enabled": False}}))
    assert app.llm.provider == "openai" and app.llm.model == "gpt-x" and pending == []


# ---- study population and login --------------------------------------------------------------------
def test_allocation_and_generation():
    assert allocate(40, {"a": 50, "b": 50}) == {"a": 20, "b": 20}
    assert sum(allocate(25, {"a": 33, "b": 33, "c": 34}).values()) == 25
    parts, counts = generate({"size": 10, "prefix": "s", "digits": 2, "roles": {"full": 70, "baseline": 30}, "seed": 1})
    assert counts == {"full": 7, "baseline": 3} and [p["username"] for p in parts][:2] == ["s01", "s02"]
    again, _ = generate({"size": 12, "prefix": "s", "digits": 2, "roles": {"full": 70, "baseline": 30}, "seed": 1}, parts)
    assert again[0]["password"] == parts[0]["password"] and len(again) == 12   # existing passwords kept


def _study_client(tmp_path, login=True):
    cfg = defaults()
    cfg["study"].update({"login_required": login, "participants": [
        {"username": "p01", "password": "maple-river-42", "role": "baseline"},
        {"username": "boss", "password": "cedar-oak-11", "role": "admin"}]})
    s = load_settings(config=AppConfig(cfg), data_dir=tmp_path, qdrant="memory", catalogue_path=FIXTURE)
    return TestClient(create_app(s, llm=OfflineLLM()))


def test_login_enforcement(tmp_path):
    c = _study_client(tmp_path)
    assert c.get("/api/users/p01/state").status_code == 401
    assert c.post("/api/login", json={"username": "p01", "password": "nope"}).status_code == 401
    tok = c.post("/api/login", json={"username": "p01", "password": "maple-river-42"}).json()["token"]
    auth = {"Authorization": f"Bearer {tok}"}
    st = c.get("/api/users/p01/state", headers=auth).json()
    assert st["access"]["role"] == "baseline" and st["access"]["source"] == "participant"
    assert c.get("/api/users/boss/state", headers=auth).status_code == 403       # someone else's account
    assert c.get("/api/config", headers={**auth, "X-User": "admin"}).status_code == 403
    boss = c.post("/api/login", json={"username": "boss", "password": "cedar-oak-11"}).json()["token"]
    assert c.get("/api/config", headers={"Authorization": f"Bearer {boss}"}).status_code == 200
    assert c.get("/api/system").status_code == 200 and c.get("/config").status_code == 200


def test_admin_link_token_and_population_endpoint(tmp_path):
    from foodrec.study import admin_link_token
    c = _study_client(tmp_path)
    tok = admin_link_token(tmp_path)
    r = c.post("/api/config/population", headers={"X-Admin-Token": tok},
               json={"population": {"size": 4, "prefix": "q", "digits": 2, "roles": {"full": 50, "baseline": 50}}})
    assert r.status_code == 200 and r.json()["counts"] == {"full": 2, "baseline": 2}
    bad = c.post("/api/config/population", headers={"X-Admin-Token": tok},
                 json={"population": {"size": 4, "roles": {"full": 50}}})
    assert bad.status_code == 400 and "add up to 50" in bad.json()["detail"]


def test_sessions_expire_and_are_signed(tmp_path):
    s = Sessions(tmp_path, ttl_hours=-1)
    assert s.user(s.issue("u")) is None
    s = Sessions(tmp_path)
    t = s.issue("u")
    assert s.user(t) == "u" and s.user(t[:-1] + ("0" if t[-1] != "0" else "1")) is None


# ---- reloading -------------------------------------------------------------------------------------
def test_watcher_decisions(tmp_path):
    from foodrec.service import App
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"ranking": {"top_k": 5}}))
    cfg = AppConfig.load(path)
    app = App(load_settings(config=cfg, data_dir=tmp_path / "d", qdrant="memory", catalogue_path=FIXTURE), llm=OfflineLLM())
    restarts = []
    w = ConfigWatcher(app, path, restart=lambda: restarts.append(1))
    path.write_text(json.dumps({"ranking": {"top_k": 5}}, indent=4))   # same content, other formatting
    assert w.check() == "unchanged"
    path.write_text(json.dumps({"ranking": {"top_k": 3}}))
    assert w.check() == "applied" and app.s.top_k == 3
    path.write_text("{ not json")
    assert w.check() == "invalid" and app.s.top_k == 3 and app.config_problem
    path.write_text(json.dumps({"ranking": {"top_k": 3}, "criteria": ENERGY}))
    assert w.check() == "restart"
    import time
    time.sleep(1.0)
    assert restarts == [1]


# ---- API keys in .env --------------------------------------------------------------------------------
def test_set_dotenv_adds_updates_and_removes(tmp_path, monkeypatch):
    from foodrec.config import set_dotenv
    env = tmp_path / ".env"
    env.write_text("# my settings\nFOODREC_LLM=1\nOPENAI_API_KEY=old\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_dotenv("OPENAI_API_KEY", "sk-new", env)
    assert env.read_text() == "# my settings\nFOODREC_LLM=1\nOPENAI_API_KEY=sk-new\n"
    assert os.environ["OPENAI_API_KEY"] == "sk-new" and (env.stat().st_mode & 0o777) == 0o600
    set_dotenv("GROQ_API_KEY", "gsk", env)
    assert env.read_text().endswith("GROQ_API_KEY=gsk\n")
    set_dotenv("OPENAI_API_KEY", None, env)
    assert "OPENAI_API_KEY" not in env.read_text() and "OPENAI_API_KEY" not in os.environ
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ValueError):
        set_dotenv("bad name", "x", env)
    with pytest.raises(ValueError):
        set_dotenv("OK_KEY", "two\nlines", env)


def test_secret_endpoint_writes_dotenv(tmp_path, monkeypatch):
    from foodrec import config as cfgmod
    monkeypatch.setattr(cfgmod, "DOTENV", tmp_path / ".env")
    monkeypatch.delenv("TEST_PROVIDER_API_KEY", raising=False)
    c = _study_client(tmp_path, login=False)
    assert c.post("/api/config/secret", json={"name": "TEST_PROVIDER_API_KEY", "value": "abc"},
                  headers={"X-User": "nobody"}).status_code in (401, 403)
    r = c.post("/api/config/secret", json={"name": "TEST_PROVIDER_API_KEY", "value": "abc"}, headers={"X-User": "admin"})
    assert r.status_code == 200 and r.json()["set"] and "abc" not in r.text
    assert (tmp_path / ".env").read_text() == "TEST_PROVIDER_API_KEY=abc\n"
    keys = c.get("/api/config", headers={"X-User": "admin"}).json()["api_keys"]
    assert keys["TEST_PROVIDER_API_KEY"] is True
    monkeypatch.delenv("TEST_PROVIDER_API_KEY", raising=False)


# ---- one app per port ----------------------------------------------------------------------------------
def test_probe_tells_foodrec_from_other_programs():
    import socket
    from foodrec.cli import probe
    with socket.socket() as srv:                       # some other program listening
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        threading.Thread(target=lambda: srv.accept()[0].close(), daemon=True).start()
        assert probe("127.0.0.1", port)[0] == "other"
    assert probe("127.0.0.1", port)[0] == "free"
