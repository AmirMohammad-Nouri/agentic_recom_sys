"""Deterministic reference cases for the recommendation core (brief, "First student tasks")."""
import pytest

from foodrec import CRITERIA
from foodrec.compare import compare
from foodrec.constraints import diagnose, eligible, request_filter_fails, validate_request_filters, violations
from foodrec.diversity import rerank
from foodrec.engine import _dedupe, public, recommend
from foodrec.explain import validate_narrative
from foodrec.learner import Learner
from foodrec.ranker import DEFAULT_WEIGHTS, resolve_weights, utility

W = {"p": .30, "h": .25, "s": .20, "c": .15, "t": .10}
S = {"p": .80, "h": .90, "s": .55, "c": .70, "t": .85}


# ---- score arithmetic ---------------------------------------------------------------
def test_01_reference_utility_is_0765():
    assert utility(W, S)[0] == pytest.approx(0.765, abs=1e-12)


def test_02_weights_normalise_to_one():
    w, _ = resolve_weights({"p": 3, "h": 2.5, "s": 2, "c": 1.5, "t": 1})
    assert sum(w.values()) == pytest.approx(1.0)
    assert w == pytest.approx(W)


def test_03_all_zero_weights_keep_last_valid():
    last = {"p": .5, "h": .5, "s": 0, "c": 0, "t": 0}
    w, msg = resolve_weights({k: 0 for k in CRITERIA}, last)
    assert w == last and "last valid" in msg


def test_04_negative_weight_rejected():
    with pytest.raises(ValueError):
        resolve_weights({"p": -1, "h": 1})


def test_05_missing_renormalises_instead_of_scoring_zero():
    u, eff, missing, _ = utility(W, {**S, "s": None}, "renormalize")
    assert missing == ["s"] and eff["s"] == 0 and sum(eff.values()) == pytest.approx(1)
    expected = (0.765 - 0.20 * 0.55) / 0.80
    assert u == pytest.approx(expected)


def test_06_missing_zero_policy_penalises():
    assert utility(W, {**S, "s": None}, "zero")[0] == pytest.approx(0.765 - 0.11)


def test_07_missing_exclude_policy_drops_recipe():
    assert utility(W, {**S, "s": None}, "exclude")[0] is None
    assert utility({**W, "s": 0}, {**S, "s": None}, "exclude")[0] is not None


# ---- restrictions ----------------------------------------------------------------------
def test_08_incomplete_allergen_data_fails_closed(cat):
    incomplete = [r for r in cat.recipes if not r["allergens_complete"]]
    assert incomplete
    for r in incomplete:
        assert any(c == "allergen_unknown" for c, _ in violations(r, {"allergens": ["sesame"]}))


def test_09_declared_allergen_excluded(cat):
    keep, _ = eligible(cat, {"allergens": ["milk"]})
    assert all("milk" not in r["allergens"] for r in keep)


def test_10_unknown_diet_flag_fails(cat):
    r = dict(cat.recipes[0], diet={**cat.recipes[0]["diet"], "vegan": None})
    assert violations(r, {"diets": ["vegan"]})


def test_11_strict_time_limit_excludes_but_soft_does_not(cat):
    strict, _ = eligible(cat, {"max_prep_minutes": {"value": 15, "strict": True}})
    soft, _ = eligible(cat, {"max_prep_minutes": {"value": 15, "strict": False}})
    assert all(r["prep_minutes"] <= 15 for r in strict)
    assert len(soft) == len(cat.recipes)


def test_12_unsupported_constraint_rejected(cat):
    with pytest.raises(ValueError):
        eligible(cat, {"allergens": ["kryptonite"]})


def test_13_empty_result_is_diagnosed_not_relaxed(cat, scaler, learner):
    declared = {"diets": ["vegan"], "max_prep_minutes": {"value": 1, "strict": True}}
    res = recommend(cat, scaler, learner, declared, {}, now=0.0)
    assert res["results"] == [] and res["empty"]["constraints"]
    assert {c["code"] for c in diagnose(cat, declared)["constraints"]} == {"diet:vegan", "time_limit"}


def test_14_request_filter_fails_closed_on_unknown_value(cat):
    f = validate_request_filters([{"field": "cost_eur", "op": "max", "value": 99}])[0]
    missing = next(r for r in cat.recipes if r.get("cost_eur_per_serving") is None)
    assert request_filter_fails(missing, f) == "value unknown"


# ---- duplicates, diversity ---------------------------------------------------------------
def test_15_duplicates_removed(cat):
    dup = dict(cat.recipes[0], id="r999")
    keep, dropped = _dedupe([cat.recipes[0], dup])
    assert len(keep) == 1 and dropped == [{"id": "r999", "duplicate_of": cat.recipes[0]["id"]}]


def test_16_zero_diversity_reproduces_utility_order(cat):
    rs = cat.recipes[:12]
    util = {r["id"]: i / 12 for i, r in enumerate(rs)}
    out = rerank(rs, util, cat.similarity, k=5, diversity=0.0)
    assert [x["recipe"]["id"] for x in out] == [r["id"] for r in sorted(rs, key=lambda r: -util[r["id"]])][:5]
    assert all(x["utility_rank"] == x["final_rank"] for x in out)


def test_17_first_item_is_highest_utility_at_any_diversity(cat):
    rs = cat.recipes[:12]
    util = {r["id"]: (hash(r["id"]) % 97) / 97 for r in rs}
    for d in (0.0, 0.5, 1.0):
        assert rerank(rs, util, cat.similarity, 5, d)[0]["utility_rank"] == 1


def test_18_diversity_out_of_range_rejected(cat):
    with pytest.raises(ValueError):
        rerank(cat.recipes[:3], {r["id"]: 1 for r in cat.recipes[:3]}, cat.similarity, 2, 1.5)


# ---- comparison --------------------------------------------------------------------------
def test_19_compare_reports_changed_input_and_list_changes(cat, scaler, learner):
    a = public(recommend(cat, scaler, learner, {}, {"weights": DEFAULT_WEIGHTS}, now=0.0))
    b = public(recommend(cat, scaler, learner, {}, {"weights": {"p": 0, "h": 0, "s": 1, "c": 0, "t": 0}}, now=0.0))
    c = compare(a, b)
    assert {x["input"] for x in c["changed_inputs"]} == {f"weight:{k}" for k in CRITERIA}
    assert c["entered"] and len(c["entered"]) == len(c["left"])


def test_20_compare_same_settings_says_nothing_changed(cat, scaler, learner):
    a = public(recommend(cat, scaler, learner, {}, {}, now=0.0))
    c = compare(a, a)
    assert c["changed_inputs"] == [] and c["note"] and not (c["entered"] or c["left"] or c["moved"])


def test_21_modes_share_candidates_and_rules(cat, scaler, learner):
    a = recommend(cat, scaler, learner, {"allergens": ["fish"]}, {"mode": "lightweight"}, now=0.0)
    b = recommend(cat, scaler, learner, {"allergens": ["fish"]}, {"mode": "extended"}, now=0.0)
    assert a["candidates"]["ids"] == b["candidates"]["ids"]
    assert a["pipeline"]["uses_llm"] is False and b["pipeline"]["uses_llm"] is False


# ---- explanations & learning ---------------------------------------------------------------
def test_22_narrative_with_unsupported_number_rejected():
    pack = {"numbers": [0.5], "titles_in_list": ["A"], "all_titles": ["A", "B"], "claims": []}
    assert validate_narrative("A scores 0.97.", pack)[0] is False
    assert validate_narrative("A is mentioned with B.", pack)[0] is False
    assert validate_narrative("A will cure you.", pack)[0] is False


def test_23_learner_is_a_pure_function_of_events(cat):
    evs = [{"event_id": "a", "type": "profile_created", "ts": 0.0, "payload": {"declared": {}, "weights": W}},
           {"event_id": "b", "type": "feedback", "ts": 10.0, "payload": {"type": "like", "recipe_id": "r001"}}]
    l1, l2 = Learner(cat), Learner(cat)
    for e in evs:
        l1.apply(e)
        l2.apply(e)
    assert l1.state_dict() == l2.state_dict()


def test_24_feedback_never_changes_stated_goal(cat, learner):
    shown = [{"recipe_id": r["id"], "scores": {"p": .5, "h": i / 5, "s": .5, "c": .5, "t": .5}}
             for i, r in enumerate(cat.recipes[:5])]
    before = dict(learner.stated)
    learner.apply({"event_id": "f", "type": "feedback", "ts": 1.0,
                   "payload": {"type": "choose", "recipe_id": cat.recipes[4]["id"], "reason": "health", "shown": shown}})
    assert learner.stated == before
    assert learner.choice.n_choices == 1


def test_25_tradeoff_answer_moves_revealed_weights(learner):
    s0 = learner.choice.inferred()["s"]["mean"]
    for i in range(3):
        learner.apply({"event_id": f"q{i}", "type": "question_answered", "ts": float(i),
                       "payload": {"winner": "s", "loser": "p"}})
    assert learner.choice.inferred()["s"]["mean"] > s0
