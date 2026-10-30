"""Regression tests for the learning, ranking and feedback fixes.

zero weights can grow, the candidate pool keeps the best recipes, missing nutrition is
explicit, repeated clicks count once, proposals are normalised, the learner replays from
the event log, "only today" / "keep my goal" control lasting updates, "had it recently"
is a recency penalty, and hard constraints hold after reranking and goal changes.
"""
import time

import pytest
from conftest import FIXTURE, make_app

from foodrec import VALUE_CRITERIA
from foodrec.agent import Supervisor, policy
from foodrec.agent.intent import parse_rules
from foodrec.agent.trace import Trace
from foodrec.catalogue import Catalogue
from foodrec.constraints import violations
from foodrec.engine import recommend, repetition_penalty
from foodrec.features import Scorer, breakdown
from foodrec.learner import Learner
from foodrec.ranker import effective_weights, utility


def _shown(cat, h_values):
    return [{"recipe_id": r["id"], "scores": {"p": .5, "h": h, "s": .5, "t": .5}}
            for h, r in zip(h_values, cat.recipes)]


def _learner(cat, weights):
    lr = Learner(cat)
    lr.apply({"event_id": "e0", "type": "profile_created", "ts": 0.0,
              "payload": {"declared": {}, "weights": weights}})
    return lr


# ---- 1. zero weights can be learned ---------------------------------------------------------
def test_zero_weight_grows_with_feedback(cat):
    lr = _learner(cat, {"h": 0.0, "s": 0.5, "t": 0.5})
    for i in range(6):
        lr.apply({"event_id": f"c{i}", "type": "feedback", "ts": 1.0 + i,
                  "payload": {"type": "choose", "recipe_id": cat.recipes[4]["id"], "request_id": f"r{i}",
                              "reason": "health", "shown": _shown(cat, [.1, .2, .3, .4, .9])}})
    assert lr.values.observed["h"] > 0.1
    assert lr.stated["h"] == 0.0
    assert sum(lr.values.observed.values()) == pytest.approx(1.0)


def test_zero_weight_saved_goal_unchanged_until_approved(onboarded):
    app = onboarded
    app.save_goal("u", "No health", {"h": 0, "s": .5, "t": .5}, 0.3, "lightweight")
    v = app.store.goal("u")["version"]
    for _ in range(8):
        res = app.recommend("u")
        best = max(res["results"], key=lambda r: r["scores"]["h"] or 0)
        app.feedback("u", res["request_id"], best["recipe_id"], "choose", "health")
        policy.review(app, "u", Trace())
    goal = app.store.goal("u")
    assert goal["version"] == v and goal["weights"]["h"] == 0
    assert app.learner("u").values.observed["h"] > 0.05


# ---- 2. candidate pool ------------------------------------------------------------------------
@pytest.mark.parametrize("weights", [{"h": .4, "s": .3, "t": .3}, {"h": 0, "s": 1, "t": 0},
                                     {"h": .8, "s": .1, "t": .1}, {"h": .1, "s": .1, "t": .8}])
def test_zero_diversity_top_five_matches_full_catalogue(cat, scaler, learner, weights):
    cfg = {"weights": weights, "diversity": 0.0, "mode": "lightweight"}
    capped = recommend(cat, scaler, learner, {}, {**cfg, "candidate_cap": 12}, now=0.0)
    full = recommend(cat, scaler, learner, {}, {**cfg, "candidate_cap": 10 ** 6}, now=0.0)
    assert capped["candidates"]["capped"] and not full["candidates"]["capped"]
    assert [r["recipe_id"] for r in capped["results"]] == [r["recipe_id"] for r in full["results"]]


def test_pool_still_contains_specialists(cat, scaler, learner):
    res = recommend(cat, scaler, learner, {}, {"candidate_cap": 20}, now=0.0)
    ids = set(res["candidates"]["ids"])
    best_s = min(cat.recipes, key=lambda r: (-(scaler.scores(r)["s"] or 0), r["id"]))
    assert len(ids) == 20 and best_s["id"] in ids


# ---- 3. missing scores ------------------------------------------------------------------------
EFF = effective_weights({"h": .8, "s": .1, "t": .1}, 0.25)


def test_missing_health_never_gives_taste_only_utility():
    u, eff, missing, _ = utility(EFF, {"p": 1.0, "h": None, "s": .2, "t": .2})
    assert u is None and missing == ["h"]          # 80% of the value weight unknown: not ranked
    light = effective_weights({"h": .2, "s": .4, "t": .4}, 0.25)
    u, eff, _, _ = utility(light, {"p": 1.0, "h": None, "s": .2, "t": .2})
    assert eff["p"] == pytest.approx(0.25) and u == pytest.approx(0.25 + 0.75 * 0.2)
    assert utility(EFF, {"p": 1.0, "h": None, "s": None, "t": None})[0] is None


def test_min_nutrient_coverage(cat):
    r = dict(cat.recipes[0], nutrition_per_100g={"kcal": 100}, fsa_score=0.9)
    assert Scorer(cat, min_coverage=0.6).scores(r)["h"] is None
    assert Scorer(cat, min_coverage=0.0).scores(r)["h"] is not None
    bd = breakdown(r, None, 0.6)
    assert bd["h"]["below_min_coverage"] and bd["h"]["score"] is None and len(bd["h"]["missing"]) == 7


def test_missing_nutrition_is_reported_not_ranked_on_taste(learner):
    cat = Catalogue.load(FIXTURE)
    target = cat.recipes[3]
    target["nutrition_per_100g"], target["fsa_score"] = {}, None
    sc = Scorer(cat)
    for i in range(5):
        learner.apply({"event_id": f"l{i}", "type": "feedback", "ts": 1.0,
                       "payload": {"type": "like", "recipe_id": target["id"]}})
    heavy = recommend(cat, sc, learner, {}, {"weights": {"h": .8, "s": .1, "t": .1}, "diversity": 0,
                                             "candidate_cap": 10 ** 6}, now=0.0)
    assert target["id"] not in [r["recipe_id"] for r in heavy["results"]]
    assert heavy["eligibility"]["not_ranked_missing_data"] >= 1
    light = recommend(cat, sc, learner, {}, {"weights": {"h": .2, "s": .4, "t": .4}, "diversity": 0,
                                             "candidate_cap": 10 ** 6}, now=0.0)
    row = next(x for x in light["ranking_table"] if x["recipe_id"] == target["id"])
    assert row["scores"]["h"] is None
    shown = [r for r in light["results"] if r["recipe_id"] == target["id"]]
    for r in shown:
        assert r["missing_info"] and r["effective_weights"]["p"] == pytest.approx(0.25)


# ---- 4. repeated clicks -----------------------------------------------------------------------
def test_repeated_clicks_count_once(onboarded):
    app = onboarded
    res = app.recommend("u")
    rid = res["results"][1]["recipe_id"]
    n0 = len(app.store.events("u"))
    first = app.feedback("u", res["request_id"], rid, "choose", "easy")
    vm = app.learner("u").values
    n_values, observed = vm.n, dict(vm.observed)
    for _ in range(3):
        again = app.feedback("u", res["request_id"], rid, "choose", "easy")
        assert again["duplicate"] and again["event_id"] is None
    assert not first["duplicate"] and vm.n == n_values and vm.observed == observed
    concept = f"cuisine:{app.catalogue.get(rid)['cuisine']}"
    app.feedback("u", res["request_id"], rid, "like")
    likes = app.learner("u").taste.counts(concept)["likes"]
    for _ in range(3):
        assert app.feedback("u", res["request_id"], rid, "like")["duplicate"]
    assert app.learner("u").taste.counts(concept)["likes"] == likes
    assert len(app.store.events("u")) == n0 + 2


# ---- 5. proposal normalisation ----------------------------------------------------------------
def test_goal_options_are_normalised_and_bounded(onboarded):
    w = {"h": .4, "s": .4, "t": .2}
    opts = onboarded.goal_option_weights(w, "t", "h", max_drop=0.10)
    assert opts
    for o in opts:
        assert sum(o.values()) == pytest.approx(1.0) and min(o.values()) >= 0
        assert o["t"] > w["t"] and w["h"] - o["h"] <= 0.10 + 1e-9
    for o in onboarded.goal_option_weights({"h": 0, "s": 1, "t": 0}, "h"):
        assert sum(o.values()) == pytest.approx(1.0) and min(o.values()) >= 0 and o["h"] > 0


def test_goal_options_need_approval(onboarded):
    app = onboarded
    v = app.store.goal("u")["version"]
    d = app.goal_options("u", "t", "h")
    assert d["kind"] == "goal_options" and app.store.goal("u")["version"] == v
    for o in d["body"]["options"]:
        assert len(o["recipes"]) == 5
    pick = d["body"]["options"][1]
    app.resolve_decision("u", d["decision_id"], "accept", pick["id"])
    g = app.store.goal("u")
    assert g["version"] == v + 1 and g["weights"] == pytest.approx(pick["weights"])


def test_policy_proposal_is_normalised_with_zero_weight(onboarded):
    app = onboarded
    app.save_goal("u", "No convenience", {"h": .5, "s": .5, "t": 0}, 0.3, "lightweight")
    for _ in range(14):
        res = app.recommend("u")
        r = min(res["results"], key=lambda x: x["n_ingredients"])
        app.feedback("u", res["request_id"], r["recipe_id"], "choose", "easy")
    policy.review(app, "u", Trace())
    for d in app.store.decisions("u", status="open"):
        if d["kind"] == "goal_proposal":
            pw = d["body"]["proposed_weights"]
            assert sum(pw.values()) == pytest.approx(1) and min(pw.values()) >= 0 and pw["t"] > 0


def test_chat_offers_options_for_small_tradeoff(onboarded):
    sup = Supervisor(onboarded)
    it = parse_rules("more convenient, with only a small reduction in healthiness", onboarded.catalogue, sup.vocab)
    assert it["intent"] == "adjust_goal" and it["criterion"] == "t" and it["secondary_criterion"] == "h"
    v = onboarded.store.goal("u")["version"]
    out = sup.chat("u", "more convenient, with only a small reduction in healthiness")
    d = out["message"]["attachments"]["decisions"][0]
    assert d["kind"] == "goal_options" and len(d["body"]["options"]) >= 2
    assert onboarded.store.goal("u")["version"] == v


# ---- 6. event replay ----------------------------------------------------------------------------
def test_full_learner_state_replays_from_event_log(tmp_path):
    a = make_app(tmp_path)
    a.onboard("u", {"preferred_cuisines": ["Italian"]}, {"h": 0, "s": .6, "t": .4})
    for i in range(4):
        res = a.recommend("u")
        r = res["results"][i % 5]
        a.feedback("u", res["request_id"], r["recipe_id"], "choose", "health")
        a.feedback("u", res["request_id"], r["recipe_id"], "choose", "health")       # repeated click
    res = a.recommend("u")
    a.feedback("u", res["request_id"], res["results"][0]["recipe_id"], "reject", "had_recently")
    a.feedback("u", res["request_id"], res["results"][1]["recipe_id"], "reject", "too_complicated", scope="today")
    a.feedback("u", res["request_id"], res["results"][2]["recipe_id"], "like")
    state = a.learner("u").state_dict()
    b = make_app(tmp_path)
    assert b.learner("u").state_dict() == state


# ---- 7. only today / keep my goal --------------------------------------------------------------
def test_only_today_feedback_changes_no_model(onboarded):
    app = onboarded
    res = app.recommend("u")
    lr = app.learner("u")
    taste0, values0, n0 = lr.taste.to_dict(), dict(lr.values.observed), lr.values.n
    rid = res["results"][0]["recipe_id"]
    out = app.feedback("u", res["request_id"], rid, "reject", "too_complicated", scope="today")
    assert out["scope"] == "today"
    assert lr.taste.to_dict()["likes"] == taste0["likes"] and lr.taste.to_dict()["dislikes"] == taste0["dislikes"]
    assert lr.values.observed == values0 and lr.values.n == n0
    today = app.recommend("u")
    assert rid not in [r["recipe_id"] for r in today["results"]]
    assert "today" in today["weights_source"]
    assert today["config"]["values_applied"]["t"] > app.store.goal("u")["weights"]["t"]
    # previews compare the saved goal, not today's adjustment
    assert app.what_if("u", weights=app.store.goal("u")["weights"])["comparison"]["entered"] == []
    # the adjustment expires with the day
    assert lr.temporary_effects(time.time() + 2 * 86400)["nudges"] == {}


def test_question_scopes_control_lasting_updates(onboarded):
    app = onboarded
    for _ in range(4):
        res = app.recommend("u")
        r = min(res["results"], key=lambda x: x["n_ingredients"])
        app.feedback("u", res["request_id"], r["recipe_id"], "choose", "easy")
    vm = app.learner("u").values
    drifted = dict(vm.observed)
    assert drifted != vm.stated
    d = app.add_decision("u", "tradeoff_question", "q", {
        "topic": "criterion:t", "options": [{"id": "today", "label": "Only today", "scope": "today",
                                             "nudge": {"t": 0.1}},
                                            {"id": "keep_goal", "label": "Keep my goal", "scope": "keep_goal"}]},
        "policy")
    app.resolve_decision("u", d["decision_id"], "answer", "today")
    assert vm.observed == pytest.approx(vm.stated)      # all of today's drift rolled back
    assert app.store.goal("u")["version"] == 1
    for _ in range(2):
        res = app.recommend("u")
        r = min(res["results"], key=lambda x: x["n_ingredients"])
        app.feedback("u", res["request_id"], r["recipe_id"], "choose", "easy")
    d2 = app.add_decision("u", "tradeoff_question", "q2", d["body"], "policy")
    app.resolve_decision("u", d2["decision_id"], "answer", "keep_goal")
    assert vm.observed == pytest.approx(vm.stated) and vm.n == 0


# ---- 8. had it recently ---------------------------------------------------------------------------
def test_had_recently_is_recency_not_dislike(onboarded):
    app = onboarded
    res = app.recommend("u")
    rid = res["results"][0]["recipe_id"]
    lr = app.learner("u")
    concepts = list(app.catalogue.concepts(app.catalogue.get(rid)))
    before = {c: lr.taste.mean(c) for c in concepts}
    app.feedback("u", res["request_id"], rid, "reject", "had_recently")
    assert {c: lr.taste.mean(c) for c in concepts} == before
    assert rid in lr.recent
    again = app.recommend("u")
    row = next(x for x in again["ranking_table"] if x["recipe_id"] == rid)
    old = next(x for x in res["ranking_table"] if x["recipe_id"] == rid)
    assert row["utility"] == pytest.approx(old["utility"] - 0.15, abs=1e-3)


def test_repetition_penalty_fades():
    cfg = {"repeat_penalty": 0.15, "repeat_half_life_days": 3.0, "repeat_window_days": 14.0}
    day = 86400.0
    assert repetition_penalty(0.0, 0.0, cfg) == pytest.approx(0.15)
    assert repetition_penalty(0.0, 3 * day, cfg) == pytest.approx(0.075)
    assert repetition_penalty(0.0, 15 * day, cfg) == 0.0
    assert repetition_penalty(None, 0.0, cfg) == 0.0


# ---- 9. hard constraints survive reranking and goal changes ---------------------------------------
def test_hard_constraints_hold_after_rerank_and_goal_changes(onboarded):
    app = onboarded
    declared = dict(app.store.profile("u")["declared"], diets=["vegetarian"],
                    disliked_ingredients=[{"name": "garlic", "strict": True}])
    app.update_profile("u", declared)
    declared = app.store.profile("u")["declared"]

    def check(res):
        assert res["results"]
        for r in res["results"]:
            assert violations(app.catalogue.get(r["recipe_id"]), declared) == []

    for w in ({"h": 1, "s": 0, "t": 0}, {"h": 0, "s": 0, "t": 1}, {"h": .2, "s": .7, "t": .1}):
        for div in (0.0, 0.5, 1.0):
            app.save_goal("u", "g", w, div, "lightweight")
            check(app.recommend("u"))
    d = app.goal_options("u", "t", "h")
    for o in d["body"]["options"]:
        for r in o["recipes"]:
            assert violations(app.catalogue.get(r["recipe_id"]), declared) == []
    app.resolve_decision("u", d["decision_id"], "accept", d["body"]["options"][0]["id"])
    check(app.recommend("u"))
    res = app.recommend("u")
    app.feedback("u", res["request_id"], res["results"][0]["recipe_id"], "reject", "not_healthy_enough", scope="today")
    check(app.recommend("u"))
    assert all(v >= 0 for v in app.store.goal("u")["weights"].values())
    assert set(app.store.goal("u")["weights"]) == set(VALUE_CRITERIA)
