"""Deterministic reference cases for the recommendation core and the user model."""
import pytest

from foodrec import CRITERIA, VALUE_CRITERIA
from foodrec.compare import compare
from foodrec.constraints import diagnose, eligible, request_filter_fails, validate_request_filters, violations
from foodrec.diversity import rerank
from foodrec.engine import _dedupe, public, recommend
from foodrec.explain import validate_narrative
from foodrec.features import breakdown, convenience, healthiness, sustainability
from foodrec.learner import Learner, ValueModel
from foodrec.ranker import DEFAULT_WEIGHTS, effective_weights, resolve_weights, utility

W = {"h": .5, "s": .3, "t": .2}
S = {"p": .80, "h": .90, "s": .55, "t": .85}
EFF = effective_weights(W, 0.25)


# ---- score arithmetic ---------------------------------------------------------------
def test_01_reference_utility():
    assert EFF == pytest.approx({"p": .25, "h": .375, "s": .225, "t": .15})
    assert utility(EFF, S)[0] == pytest.approx(0.78875, abs=1e-12)


def test_02_weights_normalise_to_one():
    w, _ = resolve_weights({"h": 5, "s": 3, "t": 2})
    assert sum(w.values()) == pytest.approx(1.0) and w == pytest.approx(W)


def test_03_all_zero_weights_keep_last_valid():
    last = {"h": .5, "s": .5, "t": 0}
    w, msg = resolve_weights({k: 0 for k in VALUE_CRITERIA}, last)
    assert w == last and "last valid" in msg


def test_04_negative_weight_rejected():
    with pytest.raises(ValueError):
        resolve_weights({"h": -1, "s": 1})


def test_05_missing_renormalises_values_and_keeps_taste_share():
    u, eff, missing, _ = utility(EFF, {**S, "s": None}, "renormalize")
    assert missing == ["s"] and eff["s"] == 0 and sum(eff.values()) == pytest.approx(1)
    assert eff["p"] == pytest.approx(0.25)
    assert u == pytest.approx(0.25 * 0.8 + 0.75 * (0.5 * 0.9 + 0.2 * 0.85) / 0.7)


def test_06_missing_zero_policy_penalises():
    assert utility(EFF, {**S, "s": None}, "zero")[0] == pytest.approx(0.78875 - 0.225 * 0.55)


# ---- the three criteria are explainable formulas -----------------------------------------
def test_07_convenience_anchors():
    assert convenience({"n_ingredients": 3}) == 1.0
    assert convenience({"n_ingredients": 15}) == 0.0
    assert convenience({"n_ingredients": 9}) == pytest.approx(0.5)


def test_08_sustainability_log_scale():
    assert sustainability({"footprint": {"kg_co2e_per_kg": 0.5}}) == 1.0
    assert sustainability({"footprint": {"kg_co2e_per_kg": 30}}) == 0.0
    mid = sustainability({"footprint": {"kg_co2e_per_kg": (0.5 * 30) ** 0.5}})
    assert mid == pytest.approx(0.5)


def test_09_health_goal_changes_weights_not_data(cat):
    r = cat.recipes[0]
    h1, rows1 = healthiness(r, "eat_healthier")
    h2, rows2 = healthiness(r, "build_muscle")
    assert [x["value"] for x in rows1] == [x["value"] for x in rows2]
    assert {x["weight"] for x in rows1} == {1.0}
    assert next(x for x in rows2 if x["factor"] == "protein")["weight"] == 4


def test_10_gain_weight_flips_energy():
    rich = {"nutrition_per_100g": {"kcal": 450}}
    assert healthiness(rich, "lose_weight")[0] == 0.0
    assert healthiness(rich, "gain_weight")[0] == 1.0


def test_11_breakdown_reproduces_scores(cat, scaler):
    for r in cat.recipes[:20]:
        bd, sc = breakdown(r, "eat_healthier"), scaler.scores(r, "eat_healthier")
        assert bd["h"]["score"] == pytest.approx(sc["h"]) and bd["s"]["score"] == pytest.approx(sc["s"])
        used = [f for f in bd["h"]["factors"] if f["score"] is not None]
        assert sum(f["weight"] * f["score"] for f in used) / sum(f["weight"] for f in used) == pytest.approx(sc["h"])


# ---- restrictions ----------------------------------------------------------------------
def test_12_incomplete_allergen_data_fails_closed(cat):
    incomplete = [r for r in cat.recipes if not r["allergens_complete"]]
    assert incomplete
    for r in incomplete:
        assert any(c == "allergen_unknown" for c, _ in violations(r, {"allergens": ["sesame"]}))


def test_13_declared_allergen_excluded(cat):
    keep, _ = eligible(cat, {"allergens": ["milk"]})
    assert all("milk" not in r["allergens"] for r in keep)


def test_14_strict_ingredient_limit_excludes_but_soft_does_not(cat):
    strict, _ = eligible(cat, {"max_ingredients": {"value": 6, "strict": True}})
    soft, _ = eligible(cat, {"max_ingredients": {"value": 6, "strict": False}})
    assert strict and all(r["n_ingredients"] <= 6 for r in strict)
    assert len(soft) == len(cat.recipes)


def test_15_unsupported_constraint_rejected(cat):
    with pytest.raises(ValueError):
        eligible(cat, {"allergens": ["kryptonite"]})


def test_16_empty_result_is_diagnosed_not_relaxed(cat, scaler, learner):
    declared = {"diets": ["vegan"], "max_ingredients": {"value": 1, "strict": True}}
    res = recommend(cat, scaler, learner, declared, {}, now=0.0)
    assert res["results"] == [] and res["empty"]["constraints"]
    assert {c["code"] for c in diagnose(cat, declared)["constraints"]} == {"diet:vegan", "ingredient_limit"}


def test_17_request_filter_fails_closed_on_unknown_value(cat):
    f = validate_request_filters([{"field": "protein_g", "op": "min", "value": 5}])[0]
    r = dict(cat.recipes[0], nutrition_per_serving={}, nutrition_per_100g={})
    assert request_filter_fails(r, f) == "value unknown"


def test_18_meal_filter_accepts_a_list(cat):
    f = validate_request_filters([{"field": "meal", "op": "is", "value": ["breakfast", "dessert"]}])[0]
    for r in cat.recipes:
        assert (request_filter_fails(r, f) is None) == (r["meal"] in ("breakfast", "dessert"))


# ---- duplicates, diversity ---------------------------------------------------------------
def test_19_duplicates_removed(cat):
    dup = dict(cat.recipes[0], id="x999")
    keep, dropped = _dedupe([cat.recipes[0], dup])
    assert len(keep) == 1 and dropped == [{"id": "x999", "duplicate_of": cat.recipes[0]["id"]}]


def test_20_zero_diversity_reproduces_utility_order(cat):
    rs = cat.recipes[:12]
    util = {r["id"]: i / 12 for i, r in enumerate(rs)}
    out = rerank(rs, util, cat.similarity, k=5, diversity=0.0)
    assert [x["recipe"]["id"] for x in out] == [r["id"] for r in sorted(rs, key=lambda r: -util[r["id"]])][:5]


def test_21_diversity_out_of_range_rejected(cat):
    with pytest.raises(ValueError):
        rerank(cat.recipes[:3], {r["id"]: 1 for r in cat.recipes[:3]}, cat.similarity, 2, 1.5)


# ---- comparison --------------------------------------------------------------------------
def test_22_compare_reports_changed_input_and_list_changes(cat, scaler, learner):
    a = public(recommend(cat, scaler, learner, {}, {"weights": DEFAULT_WEIGHTS}, now=0.0))
    b = public(recommend(cat, scaler, learner, {}, {"weights": {"h": 0, "s": 1, "t": 0}}, now=0.0))
    c = compare(a, b)
    assert {x["input"] for x in c["changed_inputs"]} == {f"weight:{k}" for k in VALUE_CRITERIA}
    assert c["entered"] and len(c["entered"]) == len(c["left"])


def test_23_compare_same_settings_says_nothing_changed(cat, scaler, learner):
    a = public(recommend(cat, scaler, learner, {}, {}, now=0.0))
    c = compare(a, a)
    assert c["changed_inputs"] == [] and c["note"] and not (c["entered"] or c["left"] or c["moved"])


def test_24_modes_share_candidates_and_rules(cat, scaler, learner):
    a = recommend(cat, scaler, learner, {"allergens": ["fish"]}, {"mode": "lightweight"}, now=0.0)
    b = recommend(cat, scaler, learner, {"allergens": ["fish"]}, {"mode": "extended"}, now=0.0)
    assert a["candidates"]["ids"] == b["candidates"]["ids"]
    assert a["pipeline"]["uses_llm"] is False and b["pipeline"]["uses_llm"] is False


def test_25_narrative_with_unsupported_number_rejected():
    pack = {"numbers": [0.5], "titles_in_list": ["Apple Crumble Pie"],
            "all_titles": ["Apple Crumble Pie", "Beef Wellington Classic"], "claims": []}
    assert validate_narrative("Apple Crumble Pie scores 0.97.", pack)[0] is False
    assert validate_narrative("Apple Crumble Pie beats Beef Wellington Classic.", pack)[0] is False
    assert validate_narrative("Apple Crumble Pie will cure you.", pack)[0] is False


# ---- user model ------------------------------------------------------------------------------
def _shown(cat, h_values):
    return [{"recipe_id": r["id"], "scores": {"p": .5, "h": h, "s": .5, "t": .5}}
            for h, r in zip(h_values, cat.recipes)]


def test_26_learner_is_a_pure_function_of_events(cat):
    evs = [{"event_id": "a", "type": "profile_created", "ts": 0.0, "payload": {"declared": {}, "weights": W}},
           {"event_id": "b", "type": "feedback", "ts": 10.0, "payload": {"type": "like", "recipe_id": cat.recipes[1]["id"]}}]
    l1, l2 = Learner(cat), Learner(cat)
    for e in evs:
        l1.apply(e)
        l2.apply(e)
    assert l1.state_dict() == l2.state_dict()


def test_27_taste_is_a_like_ratio(cat):
    lr = Learner(cat)
    lr.taste.observe({"ingredient:x": 1.0}, True, 1.0, "e1", "like")
    lr.taste.observe({"ingredient:x": 1.0}, True, 1.0, "e2", "like")
    lr.taste.observe({"ingredient:x": 1.0}, False, 1.0, "e3", "reject")
    assert lr.taste.mean("ingredient:x") == pytest.approx((1 + 2) / (2 + 3))
    assert lr.taste.mean("ingredient:never_seen") == 0.5


def test_28_choosing_healthier_dishes_raises_observed_health(cat, learner):
    shown = _shown(cat, [.1, .2, .3, .4, .9])
    h0 = learner.values.observed["h"]
    learner.apply({"event_id": "f", "type": "feedback", "ts": 1.0,
                   "payload": {"type": "choose", "recipe_id": cat.recipes[4]["id"], "shown": shown}})
    assert learner.values.observed["h"] > h0
    assert sum(learner.values.observed.values()) == pytest.approx(1.0)


def test_29_feedback_never_changes_stated_goal(cat, learner):
    before = dict(learner.stated)
    learner.apply({"event_id": "f", "type": "feedback", "ts": 1.0,
                   "payload": {"type": "choose", "recipe_id": cat.recipes[4]["id"], "reason": "health",
                               "shown": _shown(cat, [.1, .2, .3, .4, .9])}})
    assert learner.stated == before and learner.values.counts == {**learner.values.counts, "choices": 1, "reasons": 1}


def test_30_inconsistent_reason_is_flagged(cat, learner):
    learner.apply({"event_id": "f", "type": "feedback", "ts": 1.0,
                   "payload": {"type": "choose", "recipe_id": cat.recipes[0]["id"], "reason": "health",
                               "shown": _shown(cat, [.1, .5, .6, .7, .9])}})
    assert learner.values.evidence[-1]["flag"] == "reason_inconsistent_with_choice"
    assert learner.values.counts["reasons"] == 0


def test_31_preference_match_and_confidence():
    vm = ValueModel(W)
    assert vm.match() == 1.0 and vm.confidence() == 0.0
    vm.observed = {"h": .3, "s": .3, "t": .4}
    assert vm.match() == pytest.approx(1 - 0.5 * (0.2 + 0 + 0.2))
    vm.n = 5
    assert vm.confidence() == 0.5


def test_32_tradeoff_answer_moves_observed_weights(learner):
    s0 = learner.values.observed["s"]
    for i in range(3):
        learner.apply({"event_id": f"q{i}", "type": "question_answered", "ts": float(i),
                       "payload": {"winner": "s", "loser": "t"}})
    assert learner.values.observed["s"] > s0


def test_33_saving_a_goal_moves_observed_halfway(learner):
    learner.values.observed = {"h": .2, "s": .2, "t": .6}
    learner.apply({"event_id": "g", "type": "goal_saved", "ts": 1.0, "payload": {"weights": {"h": .6, "s": .2, "t": .2}}})
    assert learner.values.observed == pytest.approx({"h": .4, "s": .2, "t": .4})
    assert learner.values.n == 0
