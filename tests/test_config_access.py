"""Config file, access roles, study registration, the standard export and scenario runs."""
import io
import json
import zipfile

import pytest
from conftest import FIXTURE, OfflineLLM, make_app
from fastapi.testclient import TestClient

from foodrec.access import redact_result
from foodrec.api import create_app
from foodrec.appconfig import AppConfig, defaults, merge, validate
from foodrec.config import load_settings
from foodrec.export import export, tables, to_zip
from foodrec.scenario import Runner, check, lookup

BASELINE = {"study": {"assignment": "round_robin", "assign_roles": ["full", "baseline"]}}


def _app(tmp_path, cfg=None):
    from foodrec.service import App
    s = load_settings(config=AppConfig(cfg or {}), data_dir=tmp_path, qdrant="memory", catalogue_path=FIXTURE)
    return App(s, llm=OfflineLLM())


def _client(tmp_path, cfg=None, path=None):
    s = load_settings(config=AppConfig(cfg or {}, path), data_dir=tmp_path, qdrant="memory", catalogue_path=FIXTURE)
    return TestClient(create_app(s, llm=OfflineLLM()))


def _onboard(c, uid):
    r = c.post(f"/api/users/{uid}/onboard", json={"declared": {}, "weights": {"h": .4, "s": .4, "t": .2}})
    assert r.status_code == 200, r.text
    return r.json()


# ---- config file --------------------------------------------------------------------------------
def test_defaults_are_valid_and_file_precedence(tmp_path, monkeypatch):
    assert validate(defaults()) == []
    monkeypatch.setenv("FOODREC_LLM_MODEL", "from-env")
    monkeypatch.setenv("FOODREC_TOP_K", "7")
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"llm": {"model": "from-file", "url": None}}))
    s = load_settings(config=AppConfig.load(f), data_dir=tmp_path)
    assert s.llm_model == "from-file" and s.sources["llm_model"] == "config"   # file beats environment
    assert s.top_k == 7 and s.sources["top_k"] == "env"                       # not in the file: environment
    assert s.sources["ollama_url"] in ("default", "env")                      # null: environment or default


def test_validation_reports_problems():
    bad = merge(defaults(), {"llm": {"ctx": "big"}, "ranking": {"top_k": 99},
                             "access": {"default_role": "nobody", "users": {"x": "ghost"}},
                             "study": {"registration": "maybe"}, "extra": 1})
    errs = " | ".join(validate(bad))
    for frag in ("llm.ctx", "ranking.top_k", "default_role", "access.users.x", "study.registration", "unknown section"):
        assert frag in errs


def test_file_users_replace_defaults():
    cfg = AppConfig({"access": {"users": {"p01": "baseline"}}})
    assert cfg.data["access"]["users"] == {"p01": "baseline"}   # "demo": "admin" is not inherited
    assert cfg.access("p01")["role"] == "baseline" and cfg.access("demo")["role"] == "full"


# ---- access roles ---------------------------------------------------------------------------------
def test_baseline_result_is_redacted(onboarded):
    res = onboarded.recommend("u", save=False)
    acc = AppConfig({}).access("x")
    acc_b = {**acc, **{k: v for k, v in AppConfig({"access": {"users": {"x": "baseline"}}}).access("x").items()}}
    out = redact_result(res, acc_b)
    r = out["results"][0]
    assert r["scores"] == {} and "utility" not in r and "breakdown" not in r
    assert r["reasons"] == {"fits": [], "ranked_here": [], "compromises": [], "gaps": []}
    assert "ranking_table" not in out and out["narrative"] is None
    assert r["ingredients_detail"]                      # baseline still sees food details
    full = redact_result(res, acc)
    assert full["results"][0]["utility"] == res["results"][0]["utility"]


def test_hidden_criterion_is_removed_everywhere(onboarded):
    cfg = AppConfig({"access": {"roles": {"r": {"visible_criteria": ["h", "s"]}}, "default_role": "r"}})
    r = redact_result(onboarded.recommend("u", save=False), cfg.access("u"))["results"][0]
    assert set(r["scores"]) == {"h", "s"}
    assert set(r["evidence"]["ranking"]["contributions"]) <= {"h", "s"}


def test_api_enforces_roles(tmp_path):
    c = _client(tmp_path, {"access": {"users": {"b": "baseline"}}})
    _onboard(c, "b")
    res = c.post("/api/users/b/recommend", json={"weights": {"h": 1, "s": 0, "t": 0}}).json()
    assert res["access"]["role"] == "baseline" and res["results"][0]["scores"] == {}
    assert res["config"]["weights_requested"] != {"h": 1, "s": 0, "t": 0}   # no priority controls: saved goal
    assert c.post("/api/users/b/chat", json={"text": "hi"}).status_code == 403
    assert c.get("/api/users/b/insight").status_code == 403
    assert c.post("/api/users/b/goal", json={"weights": {"h": 1, "s": 0, "t": 0}}).status_code == 403
    fb = c.post("/api/users/b/feedback", json={"recipe_id": res["results"][0]["recipe_id"], "type": "like",
                                               "request_id": res["request_id"]}).json()
    assert fb["review"]["status"] == "disabled"
    # the stored snapshot keeps everything plus what was visible
    snap = c.app.state.core.store.request(res["request_id"])
    assert snap["results"][0]["scores"] and snap["access"]["visible_criteria"] == []


def test_supervisor_skips_blocked_skills(tmp_path):
    from foodrec.agent import Supervisor
    app = _app(tmp_path, {"access": {"users": {"n": "no_explanations"}}})
    app.onboard("n", {}, {"h": .4, "s": .4, "t": .2})
    app.recommend("n")
    out = Supervisor(app).chat("n", "Why is #1 recommended?")
    assert "not available" in out["message"]["text"]
    assert not any(s["kind"] == "tool" and s["title"] == "explain_recipe" for s in out["message"]["trace"])


# ---- study registration and assignment ------------------------------------------------------------
def test_round_robin_and_max_users(tmp_path):
    app = _app(tmp_path, {**BASELINE, "study": {**BASELINE["study"], "max_users": 2}})
    for uid in ("a", "b"):
        app.onboard(uid, {}, {"h": .4, "s": .4, "t": .2})
    assert [app.access(u)["role"] for u in ("a", "b")] == ["full", "baseline"]
    assert app.access("a")["source"] == "assigned"
    with pytest.raises(PermissionError, match="full"):
        app.onboard("c", {}, None)
    app.onboard("admin", {}, None)                       # administrators are never limited


def test_listed_only_and_closed(tmp_path):
    app = _app(tmp_path, {"study": {"registration": "listed_only"}, "access": {"users": {"p1": "full"}}})
    assert not app.registration("stranger")["allowed"] and app.registration("p1")["allowed"]
    app.onboard("p1", {}, None)
    app.apply_config(AppConfig({"study": {"registration": "closed"}, "access": {"users": {"p1": "full"}}}))
    assert app.registration("p1")["allowed"]              # already onboarded: may continue
    assert not app.registration("p2")["allowed"]


# ---- config API ------------------------------------------------------------------------------------
def test_config_api_requires_admin_and_applies_live(tmp_path):
    f = tmp_path / "foodrec.config.json"
    c = _client(tmp_path / "data", path=f)
    assert c.get("/api/config", headers={"X-User": "someone"}).status_code == 403
    v = c.get("/api/config", headers={"X-User": "admin"}).json()
    assert {x["key"] for x in v["fields"]} >= {"model", "catalogue_path", "top_k"}
    new = {"llm": {"model": "qwen3:4b"}, "ranking": {"top_k": 3}, "dataset": {"knowledge_dir": "elsewhere"},
           "access": {"default_role": "baseline", "roles": defaults()["access"]["roles"], "users": {"admin": "admin"}}}
    v = c.put("/api/config", headers={"X-User": "admin"}, json={"config": new}).json()
    assert json.loads(f.read_text())["llm"]["model"] == "qwen3:4b"
    core = c.app.state.core
    assert core.s.llm_model == "qwen3:4b" and core.s.top_k == 3          # live settings apply at once
    assert "dataset.knowledge_dir" in [p["setting"] for p in v["restart_pending"]]
    assert "llm.model" not in [p["setting"] for p in v["restart_pending"]]
    assert core.access("newbie")["role"] == "baseline"
    bad = c.put("/api/config", headers={"X-User": "admin"}, json={"config": {"llm": {"ctx": "x"}}})
    assert bad.status_code == 400


def test_admin_token(tmp_path, monkeypatch):
    monkeypatch.setenv("FOODREC_ADMIN_TOKEN", "s3cret")
    c = _client(tmp_path)
    assert c.get("/api/config", headers={"X-User": "admin"}).status_code == 401
    assert c.get("/api/config", headers={"X-Admin-Token": "s3cret"}).status_code == 200


# ---- export ----------------------------------------------------------------------------------------
def test_export_contents(tmp_path):
    app = _app(tmp_path, {"access": {"users": {"b": "baseline"}, "roles": {
        **defaults()["access"]["roles"], "baseline": {**defaults()["access"]["roles"]["baseline"],
                                                      "visible_criteria": ["h"]}}}})
    app.onboard("b", {}, {"h": .4, "s": .4, "t": .2})
    res = app.recommend("b")
    app.feedback("b", res["request_id"], res["results"][1]["recipe_id"], "choose", "health")
    app.log_ui_event("b", "page_view", {"page": "discover"})
    doc = export(app)
    u = next(x for x in doc["users"] if x["user_id"] == "b")
    assert u["access"]["role"] == "baseline" and u["access"]["visible_criteria"] == ["h"]
    rec = u["recommendations"][0]["results"][0]
    assert set(rec["visible_scores"]) == {"h"} and set(rec["scores"]) == {"p", "h", "s", "t"}
    i = u["interactions"][0]
    assert i["rank_in_list"] == 2 and set(i["visible_scores"]) == {"h"} and i["role"] == "baseline"
    assert u["summary"]["feedback"] == {"choose": 1} and u["summary"]["page_views"] == {"discover": 1}
    assert {c["criterion"]: c["visible"] for c in u["criteria"]} == {"p": False, "h": True, "s": False, "t": False}
    assert [x["kind"] for x in u["log"]][:2] == ["event", "recommendation"]
    t = tables(doc)
    assert t["recommendations"][0]["visible_h"] is True and t["recommendations"][0]["visible_s"] is False
    names = set(zipfile.ZipFile(io.BytesIO(to_zip(doc))).namelist())
    assert {"export.json", "users.csv", "recommendations.csv", "interactions.csv", "log.csv"} <= names


# ---- scenarios -------------------------------------------------------------------------------------
def test_scenario_check_and_lookup():
    probs = check({"steps": [{"action": "fly"}, {"action": "feedback", "type": "like"},
                             {"action": "repeat", "times": 2, "steps": [{"action": "chat"}]}]})
    assert any("unknown action 'fly'" in p for p in probs)
    assert any("missing 'recipe'" in p for p in probs) and any("missing 'text'" in p for p in probs)
    d = {"a": {"b": [{"c": 5}]}}
    assert lookup(d, "a.b[0].c") == 5 and lookup(d, "a.b.0.c") == 5 and lookup(d, "a.b.length") == 1


def test_scenario_run(tmp_path, monkeypatch):
    sc = {"name": "t", "user": "s", "base_config": "defaults",
          "config": {"llm": {"enabled": False}, "dataset": {"catalogue_path": str(FIXTURE)}},
          "steps": [
              {"action": "onboard", "declared": {"allergens": ["peanuts"]}, "weights": {"h": .3, "s": .3, "t": .4}},
              {"action": "repeat", "times": 2, "steps": [
                  {"action": "recommend"}, {"action": "feedback", "recipe": "best:t", "type": "choose", "reason": "easy"}]},
              {"action": "chat", "text": "Why is #1 recommended?"},
              {"action": "expect", "that": "chat.skills", "contains": "explain_recipe"},
              {"action": "expect", "that": "state.goal.version", "equals": 2, "label": "deliberately wrong"},
              {"action": "feedback", "recipe": "no such dish", "type": "like"}]}
    r = Runner(sc, data_dir=tmp_path, llm=OfflineLLM(), log=lambda *a: None)
    rep = r.run()
    assert rep["assertions_passed"] == 1 and rep["assertions_total"] == 2 and not rep["passed"]
    assert [s["status"] for s in rep["steps"]].count("error") == 1
    assert len([s for s in rep["steps"] if s["action"] == "feedback" and s["status"] == "ok"]) == 2
    doc = export(r.app, r.users)
    assert doc["users"][0]["summary"]["feedback"] == {"choose": 2}


def test_make_app_still_defaults(tmp_path):
    app = make_app(tmp_path)
    assert app.cfg.path is None and app.access("anyone")["role"] == "full"
