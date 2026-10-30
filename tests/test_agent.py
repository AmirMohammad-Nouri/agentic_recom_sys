"""Supervisor, policy, knowledge layer and API."""
import json

import pytest
from conftest import make_app
from fakes import FakeLLM

from foodrec.agent import Supervisor, policy
from foodrec.agent.intent import parse_rules, validate_model
from foodrec.agent.supervisor import validate_answer
from foodrec.agent.trace import Trace


@pytest.fixture
def sup(onboarded):
    return Supervisor(onboarded)


def intent(sup, text):
    return parse_rules(text, sup.app.catalogue, sup.vocab)


# ---- intent parsing -----------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("something easy with lentils", "recommend"),
    ("why is #2 here?", "explain_recipe"),
    ("compare #1 and #3", "compare_recipes"),
    ("what if I cared more about sustainability?", "what_if"),
    ("why do you think convenience matters to me?", "why_preference"),
    ("what did I reject last week?", "history"),
    ("how is healthiness calculated?", "knowledge"),
    ("I'm allergic to sesame", "update_profile"),
    ("is my goal consistent with what I do?", "review"),
    ("hello", "help"),
])
def test_rule_intents(sup, text, expected):
    assert intent(sup, text)["intent"] == expected


def test_mentioning_a_food_is_not_an_allergy(sup):
    it = intent(sup, "I love peanut sauce, any ideas?")
    assert it["intent"] != "update_profile" and it["profile_change"] is None


def test_rule_filters(sup):
    f = intent(sup, "high protein Indian dinner with at most 8 ingredients without onion")["filters"]
    assert f == {"protein_min": 25, "max_ingredients": 8, "cuisine": "Indian", "exclude_ingredient": "onion"}


def test_model_output_is_validated(sup):
    it, notes = validate_model({"intent": "recommend", "filters": {"cuisine": "Atlantis", "protein_min": 9999,
                                                                    "max_ingredients": 6},
                                "recipe_refs": ["Unicorn Stew", "#2"]}, "x", sup.app.catalogue, sup.vocab)
    assert it["filters"] == {"max_ingredients": 6.0} and it["recipe_refs"] == ["#2"]
    assert len(notes) == 3


# ---- supervisor turns (offline: rules + templates) -----------------------------------
def test_chat_recommend_offline(sup):
    out = sup.chat("u", "something with eggs")
    kinds = [s["kind"] for s in out["message"]["trace"]]
    assert kinds[0] == "perceive" and "tool" in kinds and kinds[-1] == "answer"
    res = out["message"]["attachments"]["result"]
    assert res["config"]["request_filters"][0]["label"] == "Ingredient includes eggs"
    assert all("eggs" in r["ingredients"] for r in res["results"])


def test_references_resolve_against_latest_nonempty_list(sup):
    sup.app.recommend("u")
    sup.chat("u", "something with at most 1 ingredients")  # empty result
    out = sup.chat("u", "why is #2 here?")
    assert "Which recipe" not in out["message"]["text"]


def test_profile_change_waits_for_confirmation(sup):
    app = sup.app
    before = app.store.profile("u")["declared"]["allergens"]
    out = sup.chat("u", "I'm allergic to sesame")
    d = out["message"]["attachments"]["decisions"][0]
    assert app.store.profile("u")["declared"]["allergens"] == before
    app.resolve_decision("u", d["decision_id"], "accept")
    assert "sesame" in app.store.profile("u")["declared"]["allergens"]


def test_model_path_streams_and_rejects_unsupported_numbers(onboarded):
    llm = FakeLLM(intent={"intent": "recommend"}, answer="The best one has utility 0.999.")
    onboarded.llm = llm
    events = []
    out = Supervisor(onboarded).chat("u", "dinner ideas", sink=events.append, think=True)
    assert any(e["type"] == "model_token" for e in events)
    assert out["method"] == "template"
    assert any(s["kind"] == "check" and s["status"] == "failed" for s in out["message"]["trace"])


def test_model_answer_accepted_when_grounded(onboarded):
    onboarded.llm = FakeLLM(intent={"intent": "help"}, answer="I can suggest five recipes and explain them.")
    out = Supervisor(onboarded).chat("u", "hi")
    assert out["method"] == "model"


def test_validate_answer():
    titles = ["Dal Tadka Classic", "Falafel Wrap Deluxe", "Salsa"]
    assert validate_answer("Utility 0.82 for Dal Tadka Classic.", [0.82], ["Dal Tadka Classic"], titles)[0]
    assert not validate_answer("Try Falafel Wrap Deluxe.", [], ["Dal Tadka Classic"], titles)[0]
    assert validate_answer("Add some salsa on top.", [], [], titles)[0]  # one-word titles are ordinary words
    assert not validate_answer("This is clinically proven.", [], [], [])[0]


# ---- monitoring policy ------------------------------------------------------------------
def _choose_simplest(app, n):
    for _ in range(n):
        res = app.recommend("u")
        r = min(res["results"], key=lambda x: x["n_ingredients"])
        app.feedback("u", res["request_id"], r["recipe_id"], "choose", "easy")


def test_conflict_asks_one_question_at_a_time(onboarded):
    _choose_simplest(onboarded, 8)
    raised = []
    for _ in range(3):
        raised += policy.review(onboarded, "u", Trace())["decisions"]
    open_q = onboarded.store.decisions("u", status="open")
    assert len([d for d in open_q if d["kind"] in ("tradeoff_question", "goal_proposal")]) == 1
    assert onboarded.store.goal("u")["version"] == 1  # never changed by the policy


def test_dismissed_topic_cools_down(onboarded):
    _choose_simplest(onboarded, 8)
    rv = policy.review(onboarded, "u", Trace())
    d = (rv["decisions"] or onboarded.store.decisions("u", status="open"))[0]
    onboarded.resolve_decision("u", d["decision_id"], "dismiss")
    tr = Trace()
    assert not [x for x in policy.review(onboarded, "u", tr)["decisions"] if x["body"]["topic"] == d["body"]["topic"]]
    assert any("dismissed" in s["title"].lower() or "recently" in s["title"].lower() for s in tr.steps)


def test_strong_evidence_produces_proposal_with_counterfactual(onboarded):
    _choose_simplest(onboarded, 14)
    tr = Trace()
    rv = policy.review(onboarded, "u", tr)
    kinds = {d["kind"] for d in onboarded.store.decisions("u", status="open")}
    assert kinds & {"goal_proposal", "tradeoff_question"}
    for d in onboarded.store.decisions("u", status="open"):
        if d["kind"] == "goal_proposal":
            assert sum(d["body"]["proposed_weights"].values()) == pytest.approx(1)
            assert "counterfactual" in d["body"]
            res = onboarded.resolve_decision("u", d["decision_id"], "accept")
            assert onboarded.store.goal("u")["source"] == "accepted_proposal"
            assert res["decision"]["status"] == "accepted"


def test_taste_conflict_question(onboarded):
    italian = [r["id"] for r in onboarded.catalogue.recipes if r["cuisine"] == "Italian"]
    for rid in italian[:6]:
        onboarded.feedback("u", None, rid, "reject", "not_my_taste")
    rv = policy.review(onboarded, "u", Trace())
    q = [d for d in rv["decisions"] if d["kind"] == "taste_question"]
    assert q and "Italian" in q[0]["title"]
    onboarded.resolve_decision("u", q[0]["decision_id"], "answer", "remove")
    assert "Italian" not in onboarded.store.profile("u")["declared"]["preferred_cuisines"]


# ---- persistence & knowledge ------------------------------------------------------------
def test_learner_rebuilt_from_event_log(tmp_path):
    a = make_app(tmp_path)
    a.onboard("u", {}, None)
    _choose_simplest(a, 3)
    state = a.learner("u").state_dict()
    b = make_app(tmp_path)
    assert b.learner("u").state_dict()["values"]["observed"] == pytest.approx(state["values"]["observed"])
    assert len(b.graph.timeline("u")) == len(a.graph.timeline("u"))


def test_graph_queries(onboarded):
    g = onboarded.graph
    res = onboarded.recommend("u")
    rid = res["results"][0]["recipe_id"]
    onboarded.feedback("u", res["request_id"], rid, "like", context={"meal": "lunch", "place": "work"})
    assert g.timeline("u", types=["like"], place="work")[0]["recipe_id"] == rid
    assert g.timeline("u", types=["like"], place="home") == []
    assert g.recommended("u")[0]["request_id"] == res["request_id"]
    prof = g.ingredient_profile("eggs", "u")
    assert prof["recipes"] and prof["category"] == ["eggs"]
    alts = g.alternatives(rid, "s")
    assert all(a["score"] > a["base_score"] for a in alts)
    nb = g.neighborhood("user:u")
    assert any(e["type"] == "DECLARED" for e in nb["edges"])


def test_hybrid_retrieval_reports_both_rankers(app):
    hits = app.retriever.search("how is sustainability measured", k=3)
    assert hits[0]["doc_id"] == "method:sustainability"
    assert {"bm25_rank", "dense_rank", "rrf"} <= set(hits[0])
    app.add_document("Team note", "Our canteen serves lentil soup on Fridays.")
    assert app.retriever.search("canteen Fridays", k=1)[0]["kind"] == "user"


# ---- API -----------------------------------------------------------------------------------
def test_api_end_to_end(tmp_path):
    from fastapi.testclient import TestClient

    from foodrec.api import create_app
    from foodrec.config import load_settings
    from conftest import FIXTURE
    c = TestClient(create_app(load_settings(data_dir=tmp_path, qdrant="memory", catalogue_path=FIXTURE),
                              llm=FakeLLM(intent={"intent": "help"})))
    u = "/api/users/x"
    assert c.post(u + "/recommend", json={}).status_code == 409
    c.post(u + "/onboard", json={"declared": {"diets": ["vegetarian"]}})
    r = c.post(u + "/recommend", json={}).json()
    assert len(r["results"]) == 5 and r["pipeline_trace"]
    assert c.post(u + "/goal", json={"weights": {"h": 0, "s": 0, "t": 0}}).json()["message"].startswith("All")
    rid = r["results"][0]["recipe_id"]
    bd = c.get(f"/api/recipes/{rid}/breakdown").json()["breakdown"]
    assert {"h", "s", "t"} <= set(bd) and bd["h"]["factors"]
    assert c.get("/api/recipes", params={"q": "egg", "limit": 3}).json()
    fb = c.post(u + "/feedback", json={"request_id": r["request_id"], "recipe_id": r["results"][0]["recipe_id"],
                                      "type": "like"}).json()
    assert fb["review"]["trace"][0]["title"] == "Feedback recorded"
    lines = [json.loads(x) for x in c.post(u + "/chat", json={"text": "hi"}).text.splitlines()]
    assert lines[0]["type"] == "step" and lines[-1]["type"] == "done"
    assert c.post(u + "/onboard", json={"declared": {"allergens": ["kryptonite"]}}).status_code == 400
    assert c.get("/").status_code == 200
