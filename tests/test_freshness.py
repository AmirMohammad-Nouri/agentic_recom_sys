"""Fresh lists (fatigue, variety across lists, "New for you") and My dishes."""
import pytest
from conftest import FIXTURE, OfflineLLM
from fastapi.testclient import TestClient

from foodrec import clock
from foodrec.api import create_app
from foodrec.appconfig import AppConfig
from foodrec.config import load_settings
from foodrec.service import App


@pytest.fixture(autouse=True)
def _real_clock():
    clock.reset()
    yield
    clock.reset()


def _app(tmp_path, **ranking):
    s = load_settings(config=AppConfig({"ranking": ranking} if ranking else {}), data_dir=tmp_path,
                      qdrant="memory", catalogue_path=FIXTURE)
    app = App(s, llm=OfflineLLM())
    app.onboard("u", {}, {"h": .4, "s": .3, "t": .3})
    return app


def _ids(res):
    return [r["recipe_id"] for r in res["results"]]


def test_list_is_stable_within_a_session(tmp_path):
    app = _app(tmp_path)
    a = app.recommend("u")
    b = app.recommend("u")
    assert _ids(a) == _ids(b)                         # the current session does not count yet


def test_ignored_dishes_tire_and_give_way(tmp_path):
    app = _app(tmp_path)
    first = _ids(app.recommend("u"))
    clock.advance(3 * 3600)
    second = app.recommend("u")
    assert _ids(second) != first                       # next session: variety across lists + fatigue
    fresh = app.freshness("u")
    shown = fresh["exposure"][first[0]]
    assert shown["views"] == 1 and shown["penalty"] == pytest.approx(app.s.fatigue_per_view, rel=0.05)
    assert set(first) <= set(fresh["history"])
    r = next((x for x in second["results"] if x["recipe_id"] in first), None)
    if r:
        assert r["freshness"]["exposure_penalty"] > 0 and "without a reaction" in " ".join(r["reasons"]["ranked_here"])


def test_fatigue_fades_and_skips_reacted_and_other(tmp_path):
    app = _app(tmp_path)
    res = app.recommend("u")
    chosen = res["results"][0]
    app.feedback("u", res["request_id"], chosen["recipe_id"], "choose", None)
    clock.advance(2 * 3600)
    f = app.freshness("u")
    assert chosen["recipe_id"] not in f["exposure"]     # a choice has its own repetition penalty
    assert "Other" not in f["cuisine"]
    p_now = f["exposure"][res["results"][1]["recipe_id"]]["penalty"]
    clock.advance(app.s.fatigue_half_life_days * 86400)
    p_later = app.freshness("u")["exposure"][res["results"][1]["recipe_id"]]["penalty"]
    assert p_later == pytest.approx(p_now / 2, rel=0.05)


def test_freshness_off_reproduces_the_old_ranking(tmp_path):
    app = _app(tmp_path, fatigue_per_view=0, cuisine_fatigue_per_slot=0, history_lists=0)
    first = _ids(app.recommend("u"))
    clock.advance(5 * 3600)
    assert _ids(app.recommend("u")) == first


def test_new_for_you_place(tmp_path):
    app = _app(tmp_path, discovery_slots=1, discovery_days=7)
    res = app.recommend("u")
    shown = {r["cuisine"] for r in res["results"]}
    news = [r for r in res["results"] if r["freshness"]["new_cuisine"]]
    assert news, "nothing was shown before, so the list offers a new cuisine"
    clock.advance(2 * 3600)
    res2 = app.recommend("u")
    new2 = [r for r in res2["results"] if r["freshness"]["new_cuisine"]]
    assert new2 and all(r["cuisine"] not in shown for r in new2)
    assert any("New for you" in x for r in new2 for x in r["reasons"]["ranked_here"])


def test_saved_dishes_wait_in_my_dishes(tmp_path):
    app = _app(tmp_path)
    res = app.recommend("u")
    rid = res["results"][2]["recipe_id"]
    app.feedback("u", res["request_id"], rid, "save", None)
    assert app.feedback("u", res["request_id"], rid, "save", None)["duplicate"]
    res2 = app.recommend("u")
    assert rid not in _ids(res2) and res2["eligibility"]["kept_for_later"] == 1
    assert [d["recipe_id"] for d in app.my_dishes("u")["saved"]] == [rid]
    taste_before = dict(app.learner("u").taste.likes)
    app.remove_dish("u", rid, "saved")
    assert app.my_dishes("u")["saved"] == [] and app.recommend("u")["eligibility"]["kept_for_later"] == 0
    assert not app.feedback("u", None, rid, "save", None)["duplicate"]       # can be saved again
    assert dict(app.learner("u").taste.likes) == taste_before                # taste counted once
    with pytest.raises(ValueError):
        app.remove_dish("u", res["results"][0]["recipe_id"], "saved")


def test_liked_and_cooked_lists(tmp_path):
    app = _app(tmp_path)
    res = app.recommend("u")
    a, b = res["results"][0]["recipe_id"], res["results"][1]["recipe_id"]
    app.feedback("u", res["request_id"], a, "like", None)
    app.feedback("u", res["request_id"], b, "choose", "taste")
    clock.advance(86400)
    app.feedback("u", None, b, "choose", None)
    d = app.my_dishes("u")
    assert [x["recipe_id"] for x in d["liked"]] == [a]
    assert d["cooked"][0]["recipe_id"] == b and d["cooked"][0]["count"] == 2 and d["cooked"][0]["why"] == ["taste"]


def test_dishes_api_respects_roles(tmp_path):
    cfg = {"access": {"users": {"b": "baseline"}, "default_role": "full",
                      "roles": {**AppConfig({}).roles}}}
    s = load_settings(config=AppConfig(cfg), data_dir=tmp_path, qdrant="memory", catalogue_path=FIXTURE)
    c = TestClient(create_app(s, llm=OfflineLLM()))
    for uid in ("b", "f"):
        c.post(f"/api/users/{uid}/onboard", json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
        res = c.post(f"/api/users/{uid}/recommend", json={}).json()
        c.post(f"/api/users/{uid}/feedback", json={"recipe_id": res["results"][0]["recipe_id"], "type": "save",
                                                   "request_id": res["request_id"]})
    full = c.get("/api/users/f/dishes").json()["saved"][0]
    base = c.get("/api/users/b/dishes").json()["saved"][0]
    assert full["scores"] and base["scores"] == {}            # baseline sees no scores
    rm = c.post("/api/users/f/dishes/remove", json={"recipe_id": full["recipe_id"], "collection": "saved"})
    assert rm.status_code == 200 and c.get("/api/users/f/dishes").json()["saved"] == []
