"""server.mode: local (everyone signs in, one administrator account), demo (public showcase
with an admin/user view, sample data in Monitor and a read-only Config page) and open (development and tests)."""
import json
import os
import subprocess
import sys

import pytest

from conftest import FIXTURE, OfflineLLM
from fastapi.testclient import TestClient

from foodrec.api import create_app
from foodrec.appconfig import AppConfig, defaults
from foodrec.config import load_settings
from foodrec.study import check_password, verify_password

VISITOR = "visitor-0123456789ab"


def _client(tmp_path, mode, admin_pw="admin-pass-1", participants=None):
    cfg = defaults()
    cfg["server"]["mode"] = mode
    cfg["access"]["admin"] = {"username": "admin", "password": admin_pw}
    cfg["study"]["participants"] = participants or [{"username": "p01", "password": "maple-river-42", "role": "baseline"}]
    path = tmp_path / "c.json"
    conf = AppConfig({}, path).save(cfg, path)          # the password is hashed on save
    s = load_settings(config=conf, data_dir=tmp_path / "d", qdrant="memory", catalogue_path=FIXTURE)
    return TestClient(create_app(s, llm=OfflineLLM())), path


def _login(c, user, pw):
    r = c.post("/api/login", json={"username": user, "password": pw})
    return {"Authorization": f"Bearer {r.json()['token']}"} if r.status_code == 200 else r


# ---- the administrator account ----------------------------------------------------------------------
def test_admin_password_is_hashed_on_save(tmp_path):
    c, path = _client(tmp_path, "local")
    adm = json.loads(path.read_text())["access"]["admin"]
    assert adm["password"] is None and verify_password("admin-pass-1", adm["password_hash"])
    assert "admin-pass-1" not in path.read_text()
    cfg = AppConfig.load(path)
    assert check_password(cfg, "admin", "admin-pass-1") and not check_password(cfg, "admin", "nope")


def test_local_mode_refuses_to_start_without_admin_password(tmp_path):
    cfg = defaults()
    cfg["server"].update({"mode": "local", "port": 8799})
    cfg["dataset"].update({"catalogue_path": str(FIXTURE), "data_dir": str(tmp_path / "d")})
    path = tmp_path / "c.json"
    path.write_text(json.dumps(cfg))
    env = {**os.environ, "FOODREC_CONFIG": str(path)}
    env.pop("FOODREC_MODE")
    r = subprocess.run([sys.executable, "-m", "foodrec", "serve"], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 2 and "foodrec admin password" in r.stderr


# ---- local mode -------------------------------------------------------------------------------------------
def test_local_mode_everyone_signs_in(tmp_path):
    c, _ = _client(tmp_path, "local")
    assert c.get("/api/users/p01/state", headers={"X-User": "p01"}).status_code == 401   # X-User is ignored
    assert c.get("/api/config", headers={"X-User": "admin"}).status_code == 401
    admin = _login(c, "admin", "admin-pass-1")
    p01 = _login(c, "p01", "maple-river-42")
    assert c.get("/api/config", headers=admin).status_code == 200
    assert c.get("/api/config", headers=p01).status_code == 403                         # participants: no Config
    st = c.get("/api/users/p01/state", headers=p01).json()
    assert st["access"]["role"] == "baseline"
    assert c.get("/api/users/admin/state", headers=admin).json()["access"]["features"]["configure"]
    v = c.get("/api/config", headers=admin).json()
    assert "password_hash" not in json.dumps(v) and v["effective"]["access"]["admin"]["password_set"]


def test_sessions_end_when_the_password_changes_or_the_account_goes(tmp_path):
    c, path = _client(tmp_path, "local", participants=[
        {"username": "p01", "password": "maple-river-42", "role": "baseline"},
        {"username": "p02", "password": "cedar-oak-11", "role": "full"}])
    admin, p01, p02 = _login(c, "admin", "admin-pass-1"), _login(c, "p01", "maple-river-42"), _login(c, "p02", "cedar-oak-11")
    data = json.loads(path.read_text())
    data["study"]["participants"] = [{"username": "p01", "password": "new-pass-99", "role": "baseline"}]  # p02 removed
    assert c.put("/api/config", headers=admin, json={"config": data}).status_code == 200
    assert c.get("/api/users/p01/state", headers=p01).status_code == 401
    assert c.get("/api/users/p02/state", headers=p02).status_code == 401
    assert c.get("/api/config", headers=admin).status_code == 200      # the admin's hash was kept on save
    assert json.loads(path.read_text())["access"]["admin"]["password_hash"]


def test_sign_in_attempts_are_limited(tmp_path):
    c, _ = _client(tmp_path, "local")
    codes = [c.post("/api/login", json={"username": "p01", "password": f"x{i}"}).status_code for i in range(12)]
    assert codes[:10] == [401] * 10 and codes[-1] == 429


def test_export_never_contains_passwords(tmp_path):
    c, _ = _client(tmp_path, "local")
    doc = c.get("/api/export", headers=_login(c, "admin", "admin-pass-1")).text
    assert "maple-river-42" not in doc and "pbkdf2" not in doc


# ---- demo mode --------------------------------------------------------------------------------------------
def test_demo_admin_and_user_views(tmp_path):
    c, path = _client(tmp_path, "demo")
    h = {"X-User": VISITOR}
    assert c.post("/api/login", json={"username": "admin", "password": "admin-pass-1"}).status_code == 400
    assert c.get("/api/users/p01/state", headers={"X-User": "p01"}).status_code == 403   # not a visitor id
    assert c.get(f"/api/users/{VISITOR}/state", headers=h).json()["access"]["role"] == "full"
    assert c.get("/api/config", headers=h).status_code == 403                           # user view
    c.post("/api/demo/view", headers=h, json={"view": "admin"})
    v = c.get("/api/config", headers=h).json()
    assert v["read_only"] and v["api_keys"] == {}
    before = path.read_text()
    assert c.put("/api/config", headers=h, json={"config": {}}).status_code == 403
    assert c.post("/api/config/secret", headers=h, json={"name": "X_API_KEY", "value": "1"}).status_code == 403
    assert path.read_text() == before
    r = c.post("/api/config/population", headers=h, json={"population": {"size": 4, "prefix": "q", "digits": 2,
                                                                          "roles": {"full": 50, "baseline": 50}}})
    assert r.status_code == 200 and len(r.json()["participants"]) == 4          # a preview, nothing saved
    other = "visitor-ffffffffffff"
    c.post(f"/api/users/{other}/onboard", headers={"X-User": other}, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    c.post(f"/api/users/{VISITOR}/onboard", headers=h, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    users = [u["user_id"] for u in c.get("/api/admin/users", headers=h).json()["users"]]
    assert users == [VISITOR]                                                   # visitors never see each other
    doc = json.loads(c.get("/api/export", headers=h).text)
    assert [u["user_id"] for u in doc["users"]] == [VISITOR]
    assert c.get(f"/api/users/{other}/state", headers=h).status_code == 403


def test_export_file_names_carry_the_user(tmp_path):
    c, _ = _client(tmp_path, "local", participants=[
        {"username": "p01", "password": "maple-river-42", "role": "baseline"},
        {"username": "p02", "password": "cedar-oak-11", "role": "full"}])
    admin = _login(c, "admin", "admin-pass-1")
    for uid, pw in (("p01", "maple-river-42"), ("p02", "cedar-oak-11")):
        c.post(f"/api/users/{uid}/onboard", headers=_login(c, uid, pw), json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    name = lambda r: r.headers["content-disposition"].split('filename="')[1].rstrip('"')  # noqa: E731
    assert name(c.get("/api/export?users=p01", headers=admin)).startswith("foodrec-p01-")
    assert name(c.get("/api/export?users=p01,p02&format=zip", headers=admin)).startswith("foodrec-p01-p02-")
    assert name(c.get("/api/export", headers=admin)).startswith("foodrec-all-users-")
    own = c.get("/api/users/p02/export", headers=_login(c, "p02", "cedar-oak-11"))
    assert name(own).startswith("foodrec-p02-") and name(own).endswith(".json")


# ---- supervisor thresholds ---------------------------------------------------------------------------------
def test_supervisor_thresholds_come_from_the_config(tmp_path):
    from foodrec.agent import policy
    from foodrec.agent.trace import Trace
    cfg = defaults()
    cfg["supervisor"].update({"ask_gap": 0.9, "propose_gap": 0.95})
    path = tmp_path / "c.json"
    conf = AppConfig({}, path).save(cfg, path)
    s = load_settings(config=conf, data_dir=tmp_path / "d", qdrant="memory", catalogue_path=FIXTURE, server_mode="open")
    c = TestClient(create_app(s, llm=OfflineLLM()))
    core = c.app.state.core
    assert policy.thresholds(core, "u")["ask_gap"] == 0.9
    assert c.get("/api/users/u/state", headers={"X-User": "u"}).json()["thresholds"]["propose_gap"] == 0.95
    # the same behaviour that raises a question at the default gap stays quiet at 0.9
    core.onboard("u", {}, {"h": .1, "s": .8, "t": .1})
    for _ in range(4):
        res = core.recommend("u", {})
        for r in res["results"]:
            core.feedback("u", res["request_id"], r["recipe_id"], "choose", "health")
    assert not policy.review(core, "u", Trace())["decisions"]
    core.s.ask_gap, core.s.propose_gap = 0.10, 0.15                                 # the control: same choices, default gaps
    assert policy.review(core, "u", Trace())["decisions"]
    assert AppConfig({"supervisor": {"ask_gap": 0.3, "propose_gap": 0.2}}).errors   # a proposal can't need less


# ---- personal links ----------------------------------------------------------------------------------------
def test_local_links_sign_in_without_a_password(tmp_path):
    from foodrec.study import generate
    parts, _ = generate({"size": 2, "prefix": "p", "digits": 2, "roles": {"baseline": 100}, "access": "link"})
    assert all("password" not in p and len(p["link"]) >= 16 for p in parts)
    c, path = _client(tmp_path, "local", participants=parts)
    # no password exists, so an empty one must not get in
    assert c.post("/api/login", json={"username": "p01", "password": ""}).status_code == 401
    assert c.post("/api/join", json={"token": "wrong-token-wrong-token"}).status_code == 404
    out = c.post("/api/join", json={"token": parts[0]["link"]}).json()
    hd = {"Authorization": f"Bearer {out['token']}"}
    assert out["user_id"] == "p01"
    assert c.get("/api/users/p01/state", headers=hd).json()["access"]["role"] == "baseline"
    assert c.get("/api/users/p02/state", headers=hd).status_code == 403
    assert c.get("/join/abc").status_code == 200
    assert parts[0]["link"] not in json.dumps(AppConfig.load(path).sanitized())


def test_links_cannot_carry_the_admin_role():
    from foodrec.appconfig import DEFAULT_ROLES
    from foodrec.study import validate_study
    bad = {"population": {"access": "link", "roles": {"admin": 100}}}
    assert validate_study(bad, DEFAULT_ROLES)
    assert validate_study({"participants": [{"username": "x", "link": "a" * 20, "role": "admin"}]}, DEFAULT_ROLES)


def test_demo_population_is_links_for_this_visitor_only(tmp_path):
    c, path = _client(tmp_path, "demo")
    h = {"X-User": VISITOR}
    c.post("/api/demo/view", headers=h, json={"view": "admin"})
    before = path.read_text()
    pop = {"size": 4, "prefix": "q", "digits": 2, "roles": {"full": 50, "baseline": 50}}
    res = c.post("/api/config/population", headers=h, json={"population": pop}).json()
    assert len(res["participants"]) == 4 and all("password" not in p and p["link"] for p in res["participants"])
    assert path.read_text() == before
    assert c.post("/api/config/population", headers=h, json={"population": {**pop, "size": 51}}).status_code == 400
    assert c.post("/api/config/population", headers=h, json={"population": {**pop, "roles": {"admin": 100}}}).status_code == 400
    assert c.get("/api/config", headers=h).json()["demo_links"][0]["username"] == "q01"
    # a person with a link is a participant: their role, no admin view
    base = next(p for p in res["participants"] if p["role"] == "baseline")
    uid = c.post("/api/join", json={"token": base["link"]}).json()["user_id"]
    them = {"X-User": uid}
    assert c.get(f"/api/users/{uid}/state", headers=them).json()["access"]["role"] == "baseline"
    assert c.post("/api/demo/view", headers=them, json={"view": "admin"}).status_code == 403
    assert c.get("/api/config", headers=them).status_code == 403
    # generating again replaces the earlier links
    c.post("/api/config/population", headers=h, json={"population": pop})
    assert c.post("/api/join", json={"token": base["link"]}).status_code == 404


def test_demo_visitor_settings_touch_only_that_visitor(tmp_path):
    c, path = _client(tmp_path, "demo")
    h, other = {"X-User": VISITOR}, {"X-User": "visitor-ffffffffffff"}
    assert c.put("/api/demo/settings", headers=h, json={"settings": {"supervisor.ask_gap": 0.2}}).status_code == 403  # user view
    c.post("/api/demo/view", headers=h, json={"view": "admin"})
    before = path.read_text()
    ok = c.put("/api/demo/settings", headers=h, json={"settings": {"supervisor.ask_gap": 0.12, "ranking.show_similarity_score": False,
                                                                    "cycle.recommendations": 2}})
    assert ok.status_code == 200 and path.read_text() == before
    assert c.get(f"/api/users/{VISITOR}/state", headers=h).json()["thresholds"]["ask_gap"] == 0.12
    assert c.get("/api/users/visitor-ffffffffffff/state", headers=other).json()["thresholds"]["ask_gap"] == 0.10
    for bad in ({"llm.model": "x"}, {"supervisor.ask_gap": 5}, {"supervisor.propose_gap": 0.01}):
        assert c.put("/api/demo/settings", headers=h, json={"settings": bad}).status_code == 400
    # the score is hidden on the visitor's cards, the explanation stays
    c.post(f"/api/users/{VISITOR}/onboard", headers=h, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    c.post("/api/demo/view", headers=h, json={"view": "user"})
    r = c.post(f"/api/users/{VISITOR}/recommend", headers=h, json={}).json()["results"][0]
    assert "utility" not in r and r["reasons"]["summary"] and r["evidence"]["ranking"]["contributions"]


# ---- meal cycles -------------------------------------------------------------------------------------------
def _cycle_client(tmp_path, mode="open", **cycle):
    cfg = defaults()
    cfg["server"]["mode"] = mode
    cfg["cycle"].update({"enabled": True, "meals": "lunch,dinner", "recommendations": 2, "days": 2, **cycle})
    path = tmp_path / "c.json"
    conf = AppConfig({}, path).save(cfg, path)
    s = load_settings(config=conf, data_dir=tmp_path / "d", qdrant="memory", catalogue_path=FIXTURE, server_mode=mode)
    return TestClient(create_app(s, llm=OfflineLLM()))


def test_cycle_walks_meals_and_days_and_needs_every_reaction(tmp_path):
    c = _cycle_client(tmp_path)
    h = {"X-User": "u"}
    c.post("/api/users/u/onboard", headers=h, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    st = c.get("/api/users/u/cycle", headers=h).json()
    assert st["total"] == 2 * 2 * 2 and (st["day"], st["meal"]) == (1, "lunch")
    assert c.get("/api/users/u/cycle", headers=h).json()["result"]["request_id"] == st["result"]["request_id"]  # stable
    seen = []
    while not st["finished"]:
        seen.append((st["day"], st["meal"]))
        rows = st["result"]["results"]
        assert len(rows) == 2 and not st["complete"]
        assert c.post("/api/users/u/cycle/next", headers=h).status_code == 400          # reactions missing
        c.post("/api/users/u/feedback", headers=h, json={"request_id": st["result"]["request_id"], "recipe_id": rows[0]["recipe_id"], "type": "like"})
        c.post("/api/users/u/feedback", headers=h, json={"request_id": st["result"]["request_id"], "recipe_id": rows[1]["recipe_id"], "type": "reject"})
        assert c.get("/api/users/u/cycle", headers=h).json()["complete"]
        st = c.post("/api/users/u/cycle/next", headers=h).json()
    assert seen == [(1, "lunch"), (1, "dinner"), (2, "lunch"), (2, "dinner")]
    assert st["answered_total"] == st["total"] == 8
    st = c.post("/api/users/u/cycle/restart", headers=h).json()
    assert (st["round"], st["day"], st["meal"], st["answered_total"]) == (2, 1, "lunch", 0)
    doc = json.loads(c.get("/api/users/u/export", headers=h).text)
    assert len(doc["users"][0]["meal_cycle"]["reactions"]) == 8


def test_a_repeated_like_still_counts_as_a_reaction(tmp_path):
    c = _cycle_client(tmp_path, recommendations=1)
    h = {"X-User": "u"}
    c.post("/api/users/u/onboard", headers=h, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    st = c.get("/api/users/u/cycle", headers=h).json()
    rid = st["result"]["results"][0]["recipe_id"]
    c.post("/api/users/u/feedback", headers=h, json={"recipe_id": rid, "type": "like"})   # liked earlier, elsewhere
    assert not c.get("/api/users/u/cycle", headers=h).json()["complete"]
    out = c.post("/api/users/u/feedback", headers=h, json={"request_id": st["result"]["request_id"], "recipe_id": rid, "type": "like"}).json()
    assert out["duplicate"] and c.get("/api/users/u/cycle", headers=h).json()["complete"]


def test_demo_cycle_is_always_breakfast_lunch_dinner(tmp_path):
    c = _cycle_client(tmp_path, mode="demo", meals="snack", enabled=False)
    h = {"X-User": VISITOR}
    c.post(f"/api/users/{VISITOR}/onboard", headers=h, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    st = c.get(f"/api/users/{VISITOR}/cycle", headers=h).json()
    assert st["enabled"] and st["meals"] == ["breakfast", "lunch", "dinner"] and st["meal"] == "breakfast"
    assert list(c.get("/api/system").json()["options"]["meal_contexts"]) == ["breakfast", "lunch", "dinner"]


def test_cycle_is_off_by_default_and_checks_meal_names(tmp_path):
    assert AppConfig({"cycle": {"meals": "lunch,brunch"}}).errors
    c, _ = _client(tmp_path, "open")
    assert not c.get("/api/users/u/state", headers={"X-User": "u"}).json()["cycle"]["enabled"]


# ---- the sentence on the card ------------------------------------------------------------------------------
def test_plain_reason_is_readable_and_respects_hidden_criteria():
    from foodrec.explain import plain_reason
    ev = {"fit": [{"type": "preference", "name": "milk"}],
          "compromises": [{"criterion": "s", "score": .57, "list_median": .76}],
          "ranking": {"top_contributor": "h", "diversity": {"promoted": False},
                      "contributions": {"h": {"weight": .4, "score": .8, "contribution": .32},
                                        "s": {"weight": .3, "score": .57, "contribution": .17},
                                        "p": {"weight": .25, "score": .5, "contribution": .125}}}}
    text = plain_reason(ev)
    assert text.startswith("This dish was suggested mainly because of its healthiness, which scores very well.")
    assert "milk" in text and "sustainability is lower" in text
    assert not any(ch.isdigit() for ch in text)
    hidden = plain_reason(ev, ["p"])
    assert "ealthiness" not in hidden and "ustainability" not in hidden and "milk" in hidden


# ---- links last until the administrator ends them; the path is chosen per account --------------------------
def test_link_lasts_until_deactivated_or_removed(tmp_path):
    from foodrec.study import LINK_SESSION_SECONDS
    p = {"username": "p01", "link": "x" * 24, "role": "full"}
    c, path = _client(tmp_path, "local", participants=[p])
    core = c.app.state.core
    out = c.post("/api/join", json={"token": p["link"]}).json()
    hd = {"Authorization": f"Bearer {out['token']}"}
    assert core.sessions._read(out["token"])["exp"] > __import__("time").time() + LINK_SESSION_SECONDS - 60
    assert c.get("/api/users/p01/state", headers=hd).status_code == 200
    assert c.post("/api/join", json={"token": p["link"]}).status_code == 200               # reusable

    def save(participants):
        data = json.loads(path.read_text())
        data["study"]["participants"] = participants
        core.apply_config(core.cfg.save(data))
    save([{**p, "active": False}])
    assert c.get("/api/users/p01/state", headers=hd).status_code == 401                    # signed out at once
    assert c.post("/api/join", json={"token": p["link"]}).status_code == 403
    save([p])
    assert c.get("/api/users/p01/state", headers=hd).status_code == 200                    # active again
    save([])
    assert c.get("/api/users/p01/state", headers=hd).status_code == 401
    assert c.post("/api/join", json={"token": p["link"]}).status_code == 404


def test_deactivated_password_account_cannot_sign_in(tmp_path):
    c, _ = _client(tmp_path, "local", participants=[{"username": "p01", "password": "maple-river-42", "role": "full", "active": False}])
    assert c.post("/api/login", json={"username": "p01", "password": "maple-river-42"}).status_code == 401


def test_the_path_is_chosen_per_account(tmp_path):
    from foodrec.study import generate, validate_study
    c, _ = _client(tmp_path, "local", participants=[
        {"username": "plain", "link": "a" * 24, "role": "full"},                           # a link without a choice
        {"username": "free", "link": "b" * 24, "role": "full", "flow": "free"},
        {"username": "pw", "password": "maple-river-42", "role": "full"},                  # a password without a choice
        {"username": "pwg", "password": "cedar-oak-11", "role": "full", "flow": "guided"}])
    core = c.app.state.core
    assert not core.s.cycle_enabled
    assert {u: core.cycle_config(u)["enabled"] for u in ("plain", "free", "pw", "pwg")} == \
        {"plain": True, "free": False, "pw": False, "pwg": True}
    core.s.cycle_enabled = True
    assert core.cycle_config("pw")["enabled"] and not core.cycle_config("free")["enabled"]
    parts, _ = generate({"size": 1, "prefix": "p", "digits": 1, "roles": {"full": 100}, "access": "link", "flow": "free"},
                        [{"username": "p1", "link": "k" * 24, "active": False}])
    assert parts[0]["flow"] == "free" and parts[0]["link"] == "k" * 24 and parts[0]["active"] is False
    assert validate_study({"participants": [{"username": "x", "link": "a" * 20, "role": "full", "flow": "maybe"}]}, {"full": {}})


def test_demo_owner_can_deactivate_and_remove_a_link(tmp_path):
    c, _ = _client(tmp_path, "demo")
    h = {"X-User": VISITOR}
    c.post("/api/demo/view", headers=h, json={"view": "admin"})
    links = c.post("/api/config/population", headers=h, json={"population": {"size": 2, "prefix": "q", "digits": 1, "roles": {"full": 100}}}).json()["participants"]
    tok = links[0]["link"]
    uid = c.post("/api/join", json={"token": tok}).json()["user_id"]
    them = {"X-User": uid}
    assert c.get(f"/api/users/{uid}/state", headers=them).status_code == 200
    assert c.post("/api/demo/links", headers={"X-User": "visitor-ffffffffffff"}, json={"token": tok, "action": "deactivate"}).status_code == 403
    assert c.post("/api/demo/links", headers=h, json={"token": tok, "action": "deactivate"}).status_code == 200
    assert c.get(f"/api/users/{uid}/state", headers=them).status_code == 403                # also with the id kept in the browser
    assert c.post("/api/join", json={"token": tok}).status_code == 403
    assert c.post("/api/demo/links", headers=h, json={"token": tok, "action": "activate"}).status_code == 200
    assert c.get(f"/api/users/{uid}/state", headers=them).status_code == 200
    left = c.post("/api/demo/links", headers=h, json={"token": tok, "action": "remove"}).json()["demo_links"]
    assert len(left) == 1 and c.post("/api/join", json={"token": tok}).status_code == 404
    assert c.get(f"/api/users/{uid}/state", headers=them).status_code == 403                # not a new visitor either


def test_demo_user_view_is_guided(tmp_path):
    c = _cycle_client(tmp_path, mode="demo", enabled=False)
    h = {"X-User": VISITOR}
    c.post(f"/api/users/{VISITOR}/onboard", headers=h, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    assert c.get(f"/api/users/{VISITOR}/state", headers=h).json()["cycle"]["enabled"]
    assert c.post(f"/api/users/{VISITOR}/cycle/next", headers=h).status_code == 400        # a reaction to every dish first


def test_tour_is_remembered_per_user_not_per_browser(tmp_path):
    c = _cycle_client(tmp_path)
    for user in ("a", "b"):
        c.post(f"/api/users/{user}/onboard", headers={"X-User": user}, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    seen = lambda u: c.get(f"/api/users/{u}/state", headers={"X-User": u}).json()["tour_seen"]  # noqa: E731
    assert not seen("a") and not seen("b")
    assert c.post("/api/users/a/ui-events", headers={"X-User": "a"}, json={"kind": "tour_closed", "result": "skipped"}).status_code == 200
    assert seen("a") and not seen("b")


# ---- the administrator's Monitor page ---------------------------------------------------------------------
def test_monitor_groups_people_by_population_and_is_for_administrators(tmp_path):
    parts = [{"username": "a1", "password": "maple-river-42", "role": "baseline", "population": "Pilot A"},
             {"username": "b1", "password": "cedar-oak-11", "role": "full", "population": "Pilot B", "active": False},
             {"username": "b2", "password": "cedar-oak-12", "role": "full", "population": "Pilot B"},
             {"username": "c1", "password": "cedar-oak-13", "role": "full"}]
    c, _ = _client(tmp_path, "local", participants=parts)
    admin, a1 = _login(c, "admin", "admin-pass-1"), _login(c, "a1", "maple-river-42")
    assert c.get("/api/admin/monitor").status_code == 401
    assert c.get("/api/admin/monitor", headers=a1).status_code == 403
    assert c.get("/api/admin/monitor/users/a1", headers=a1).status_code == 403
    c.post("/api/users/a1/onboard", headers=a1, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    res = c.post("/api/users/a1/recommend", headers=a1, json={}).json()
    c.post("/api/users/a1/feedback", headers=a1, json={"request_id": res["request_id"], "recipe_id": res["results"][0]["recipe_id"], "type": "like"})
    c.get("/api/users/admin/state", headers=admin)
    data = c.get("/api/admin/monitor", headers=admin).json()
    assert not data["has_sample"]
    pops = {p["name"]: p for p in data["populations"]}
    assert set(pops) == {"Main population", "Pilot A", "Pilot B"}                 # administrators are not listed
    a = pops["Pilot A"]
    assert a["size"] == 1 and a["started"] == 1 and a["reactions"] == {"like": 1}
    assert a["people"][0]["state"] == "in_progress"
    assert {p["label"]: p["state"] for p in pops["Pilot B"]["people"]} == {"b1": "deactivated", "b2": "invited"}
    text = json.dumps(data)
    assert "maple-river-42" not in text and "password" not in text
    d = c.get("/api/admin/monitor/users/a1", headers=admin).json()
    assert d["interactions"][0]["type"] == "like" and d["interactions"][0]["title"]
    assert any(x["kind"] == "recommendation" for x in d["log"]) and "maple-river-42" not in json.dumps(d)
    assert c.get("/api/admin/monitor/users/b2", headers=admin).json()["interactions"] == []
    assert c.get("/api/admin/monitor/users/admin", headers=admin).status_code == 404
    assert c.get("/api/admin/monitor/users/nobody", headers=admin).status_code == 404


def test_demo_monitor_has_labelled_samples_and_only_this_visitors_links(tmp_path):
    c, _ = _client(tmp_path, "demo")
    h = {"X-User": VISITOR}
    assert c.get("/api/admin/monitor", headers=h).status_code == 403                  # user view
    c.post("/api/demo/view", headers=h, json={"view": "admin"})
    data = c.get("/api/admin/monitor", headers=h).json()
    assert data["has_sample"] and len(data["populations"]) == 3 and all(p["sample"] for p in data["populations"])
    assert data == c.get("/api/admin/monitor", headers=h).json()                      # the same every time
    states = {x["state"] for p in data["populations"] for x in p["people"]}
    assert states == {"invited", "started", "in_progress", "finished", "deactivated"}
    first = data["populations"][0]["people"][0]
    assert first["state"] == "finished"                                               # the tour opens this person
    d = c.get(f"/api/admin/monitor/users/{first['user_id']}", headers=h).json()
    assert d["sample"] and len(d["interactions"]) == first["reactions_total"] == first["cycle"]["total"]
    assert d["log"] and d["log"][0]["ts"] >= d["log"][-1]["ts"]                       # newest first
    assert c.get("/api/admin/monitor/users/sample-a01", headers={"X-User": "visitor-ffffffffffff"}).status_code == 403
    # samples are not stored, and links appear as real data for their owner alone
    assert not [u for u in json.loads(c.get("/api/export", headers=h).text)["users"] if u["user_id"].startswith("sample-")]
    made = c.post("/api/config/population", headers=h, json={"population": {"size": 2, "prefix": "q", "digits": 2, "roles": {"full": 100}}}).json()
    them = c.post("/api/join", json={"token": made["participants"][0]["link"]}).json()["user_id"]
    tv = {"X-User": them}
    c.post(f"/api/users/{them}/onboard", headers=tv, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    pops = {p["name"]: p for p in c.get("/api/admin/monitor", headers=h).json()["populations"]}
    mine = pops["Your links"]
    assert not mine["sample"] and mine["size"] == 2 and mine["started"] == 1
    assert c.get(f"/api/admin/monitor/users/{them}", headers=h).status_code == 200
    assert [u["user_id"] for u in json.loads(c.get(f"/api/export?users={them}", headers=h).text)["users"]] == [them]
    other = {"X-User": "visitor-ffffffffffff"}
    c.post("/api/demo/view", headers=other, json={"view": "admin"})
    assert "Your links" not in {p["name"] for p in c.get("/api/admin/monitor", headers=other).json()["populations"]}
    assert c.get(f"/api/admin/monitor/users/{them}", headers=other).status_code == 404
    # asking for someone else's data never widens the export
    assert [u["user_id"] for u in json.loads(c.get(f"/api/export?users={them}", headers=other).text)["users"]] == ["visitor-ffffffffffff"]


def test_populations_are_generated_side_by_side(tmp_path):
    from foodrec.study import add_population
    a, _ = add_population({"name": "Pilot A", "size": 2, "prefix": "a", "digits": 2, "roles": {"full": 100}}, [])
    both, _ = add_population({"name": "Pilot B", "size": 2, "prefix": "b", "digits": 2, "roles": {"baseline": 100}}, a)
    assert [(p["username"], p["population"]) for p in both] == [("a01", "Pilot A"), ("a02", "Pilot A"), ("b01", "Pilot B"), ("b02", "Pilot B")]
    again, _ = add_population({"name": "Pilot B", "size": 1, "prefix": "b", "digits": 2, "roles": {"baseline": 100}}, both)
    assert [p["username"] for p in again] == ["a01", "a02", "b01"]
    assert next(p for p in again if p["username"] == "b01")["password"] == next(p for p in both if p["username"] == "b01")["password"]
    with pytest.raises(ValueError, match="choose another prefix"):
        add_population({"name": "Pilot C", "size": 1, "prefix": "a", "digits": 2, "roles": {"full": 100}}, both)
    # participants without a name stay together, as before
    plain, _ = add_population({"size": 1, "prefix": "z", "roles": {"full": 100}}, [{"username": "old", "password": "maple-1", "role": "full"}])
    assert [p["username"] for p in plain] == ["z01"]


def test_each_side_has_its_own_tour(tmp_path):
    c = _cycle_client(tmp_path)
    h = {"X-User": "a"}
    c.post("/api/users/a/onboard", headers=h, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    seen = lambda: (lambda s: (s["tour_seen"], s["tour_seen_admin"]))(c.get("/api/users/a/state", headers=h).json())  # noqa: E731
    assert seen() == (False, False)
    c.post("/api/users/a/ui-events", headers=h, json={"kind": "tour_closed", "result": "done", "side": "admin"})
    assert seen() == (False, True)
    c.post("/api/users/a/ui-events", headers=h, json={"kind": "tour_closed", "result": "done"})
    assert seen() == (True, True)
