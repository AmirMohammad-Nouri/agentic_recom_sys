"""HTTP API and web UI.

Run:  python -m foodrec            (or: uvicorn foodrec.api:app)
The chat endpoint streams newline-delimited JSON: one object per trace step while the
supervisor works, then a final {"type": "done"} object with the stored message.
"""
import json
import logging
import queue
import threading
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import CRITERION_LABELS
from .agent import Supervisor
from .agent import policy
from .agent.trace import Trace
from .config import load_settings
from .features import breakdown, health_grade
from .service import MEAL_CONTEXTS, App

WEB = Path(__file__).parent / "web"
log = logging.getLogger("foodrec.api")


def pipeline_trace(res):
    """Explain a ranking request step by step from its recorded result (no model involved)."""
    if res.get("empty") and not res.get("results"):
        return [{"kind": "reason", "title": "No eligible recipe", "detail": res["empty"]["message"], "source": "engine"}]
    el, cfg, tel = res["eligibility"], res["config"], res["telemetry"]
    st = tel["stages"]
    steps = [
        {"kind": "tool", "title": "Mandatory rules", "source": "engine",
         "detail": f"{el['eligible_count']} of {el['eligible_count'] + el['excluded_count']} recipes pass"
                   + (": " + ", ".join(f"{k} excluded {v}" for k, v in el["excluded_by_code"].items())
                      if el["excluded_by_code"] else "."),
         "duration_ms": st.get("filter", {}).get("wall_ms")}]
    if cfg["request_filters"]:
        steps.append({"kind": "tool", "title": "Request-only filters", "source": "engine",
                      "detail": f"{', '.join(f['label'] for f in cfg['request_filters'])} -> {el['after_request_filters']} remain. Not saved."})
    steps += [
        {"kind": "tool", "title": "Candidate set", "source": "engine",
         "detail": f"{len(res['candidates']['ids'])} candidates, {len(res['candidates']['duplicates_removed'])} duplicates removed"
                   + (", capped with per-criterion coverage" if res["candidates"]["capped"] else "") + ".",
         "duration_ms": st.get("candidates", {}).get("wall_ms")},
        {"kind": "tool", "title": f"Scoring ({cfg['mode']})", "source": "engine",
         "detail": f"Healthiness for the goal '{cfg['health_goal'].replace('_', ' ')}', sustainability from CO2e, "
                   f"convenience from ingredient count; taste scorer: {res['pipeline']['preference_scorer']}; "
                   f"cache {tel['cache']}; no language-model call.", "duration_ms": st.get("scoring", {}).get("wall_ms")},
        {"kind": "reason", "title": "Weighted utility", "source": "engine",
         "detail": "U = " + " + ".join(f"{cfg['weights_applied'][k]:.2f}*{CRITERION_LABELS[k]}" for k in cfg["weights_applied"])
                   + f" (taste share {round(100 * cfg['taste_share'])}%). {cfg['weights_message']}"},
        {"kind": "tool", "title": f"Diversity rerank (d = {cfg['diversity']:.2f})", "source": "engine",
         "detail": f"{sum(1 for r in res['results'] if r['evidence']['ranking']['diversity']['promoted'])} recipe(s) promoted "
                   f"for variety; utility rank and final rank are both recorded.",
         "duration_ms": st.get("rerank", {}).get("wall_ms")},
        {"kind": "answer", "title": "Evidence records built", "source": "engine",
         "detail": f"One evidence record per recipe; reasons are templates filled from it. Total {tel['total_wall_ms']:.1f} ms."},
    ]
    return steps


def create_app(settings=None, llm=None):
    settings = settings or load_settings()
    core = App(settings, llm=llm)
    sup = Supervisor(core)
    api = FastAPI(title="FoodRec", version="0.3.0")
    api.state.core, api.state.supervisor = core, sup

    @api.exception_handler(ValueError)
    async def _value_error(_: Request, e: ValueError):
        return JSONResponse({"error": str(e)}, status_code=400)

    # ---- web ----------------------------------------------------------------------------
    api.mount("/static", StaticFiles(directory=WEB), name="static")

    @api.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    # ---- system -------------------------------------------------------------------------
    @api.get("/api/system")
    def system():
        return {**core.system(), "skills": sup.skills.catalog(), "thresholds": policy.THRESHOLDS}

    def _brief(r):
        sc = core.scaler.scores(r)
        return {"recipe_id": r["id"], "title": r["title"], "cuisine": r["cuisine"], "meal": r.get("meal"),
                "image_url": r.get("image_url"), "n_ingredients": r.get("n_ingredients"),
                "kcal": r.get("kcal_per_serving"), "co2e_kg_per_kg": (r.get("footprint") or {}).get("kg_co2e_per_kg"),
                "health_grade": health_grade(sc["h"]), "scores": sc,
                "diet": r["diet"], "allergens": r["allergens"],
                "ingredients": [i["name"] for i in r["ingredients"]]}

    @api.get("/api/recipes")
    def recipes(q: str = "", limit: int = 40, ids: str = None, meal: str = None):
        """Search the catalogue (title or exact ingredient), or fetch ids=a,b,c."""
        found = core.search_recipes(q, max(1, min(limit, 200)), ids.split(",") if ids else None, meal)
        return [_brief(r) for r in found]

    @api.get("/api/recipes/{rid}/breakdown")
    def recipe_breakdown(rid: str, goal: str = None, uid: str = None):
        """How each of the three scores of one recipe is calculated."""
        if rid not in core.catalogue.by_id:
            raise HTTPException(404, "unknown recipe")
        if goal is None and uid:
            goal = ((core.store.profile(uid) or {}).get("declared") or {}).get("health_goal")
        r = core.catalogue.get(rid)
        return {**_brief(r), "breakdown": breakdown(r, goal, core.scaler.min_coverage), "url": r.get("url"),
                "nutrition_per_100g": r.get("nutrition_per_100g"), "nutrition_per_serving": r.get("nutrition_per_serving"),
                "fsa_score": r.get("fsa_score"), "allergens_complete": r.get("allergens_complete"),
                "ingredients_detail": [{k: i.get(k) for k in ("name", "text", "grams", "grams_basis", "category")}
                                       for i in r["ingredients"]], "label_source": r.get("label_source")}

    # ---- user state ---------------------------------------------------------------------
    @api.get("/api/users/{uid}/state")
    def state(uid: str):
        return core.state(uid)

    @api.post("/api/users/{uid}/onboard")
    def onboard(uid: str, body: dict = Body(...)):
        return core.onboard(uid, body.get("declared") or {}, body.get("weights"), body.get("diversity", 0.3),
                            body.get("mode", "lightweight"))

    @api.put("/api/users/{uid}/profile")
    def profile(uid: str, body: dict = Body(...)):
        return core.update_profile(uid, body["declared"], "user", body.get("note"))

    @api.get("/api/users/{uid}/profile/history")
    def profile_history(uid: str):
        return core.store.profile_history(uid)

    @api.post("/api/users/{uid}/goal")
    def goal(uid: str, body: dict = Body(...)):
        return core.save_goal(uid, body.get("name") or "My priorities", body["weights"], body.get("diversity", 0.3),
                              body.get("mode", "lightweight"))

    @api.get("/api/users/{uid}/goal/history")
    def goal_history(uid: str):
        return core.store.goal_history(uid)

    @api.post("/api/users/{uid}/goal/revert")
    def goal_revert(uid: str, body: dict = Body(...)):
        return core.revert_goal(uid, int(body["version"]))

    # ---- recommendations ------------------------------------------------------------------
    @api.post("/api/users/{uid}/recommend")
    def recommend(uid: str, body: dict = Body(default={})):
        if not core.store.profile(uid):
            raise HTTPException(409, "complete onboarding first")
        ov = {k: body.get(k) for k in ("weights", "diversity", "mode")}
        if body.get("meal") in MEAL_CONTEXTS:
            ov["request_filters"] = [{"field": "meal", "op": "is", "value": MEAL_CONTEXTS[body["meal"]],
                                      "source": f"{body['meal']} context"}]
        res = core.recommend(uid, ov, origin="ui")
        res["pipeline_trace"] = pipeline_trace(res)
        return res

    @api.post("/api/users/{uid}/preview")
    def preview(uid: str, body: dict = Body(...)):
        """Local preview of a settings change; nothing is stored."""
        wi = core.what_if(uid, body.get("weights"), body.get("diversity"), body.get("mode"))
        return {"comparison": wi["comparison"],
                "titles": {r["recipe_id"]: r["title"] for r in wi["alternative"].get("results", []) +
                           wi["base"].get("results", [])},
                "weights_applied": wi["alternative"]["config"]["weights_applied"]}

    @api.post("/api/users/{uid}/goal-options")
    def goal_options(uid: str, body: dict = Body(...)):
        """Several small goal changes with their lists, stored as one decision; nothing is saved
        until the user picks an option (POST /decisions/{id} with action=accept, answer=o1...)."""
        return core.goal_options(uid, body["increase"], body.get("decrease"), float(body.get("max_drop", 0.10)),
                                 source="ui")

    @api.get("/api/requests/{rid}")
    def get_request(rid: str):
        r = core.store.request(rid)
        if not r:
            raise HTTPException(404, "unknown request")
        return r

    @api.get("/api/compare")
    def compare(before: str, after: str):
        return core.compare_requests(before, after)

    # ---- feedback & supervisor decisions -----------------------------------------------------
    @api.post("/api/users/{uid}/feedback")
    def feedback(uid: str, body: dict = Body(...)):
        out = core.feedback(uid, body.get("request_id"), body["recipe_id"], body["type"], body.get("reason"),
                            body.get("context"), body.get("scope") or "lasting")
        tr = Trace()
        if out.get("duplicate"):
            tr.step("perceive", "Repeated click ignored",
                    f"{body['type']} on {core.catalogue.get(body['recipe_id'])['title']} was already recorded "
                    "for this list, so it is not counted twice.", source="learner")
            return {**out, "review": {"status": "duplicate", "decisions": [], "trace": tr.steps}}
        tr.step("perceive", "Feedback recorded",
                f"{body['type']} on {core.catalogue.get(body['recipe_id'])['title']}"
                + (f" (reason: {body['reason'].replace('_', ' ')})" if body.get("reason") else "")
                + (" for today only; nothing lasting was learned" if out.get("scope") == "today" else "")
                + f"; event {out['event_id']}. Your saved goal is not changed by feedback.",
                evidence=out["trail"][:12], source="learner")
        rv = policy.review(core, uid, tr, trigger="feedback")
        return {**out, "review": {"status": rv["status"], "decisions": rv["decisions"], "trace": tr.steps}}

    @api.get("/api/users/{uid}/decisions")
    def decisions(uid: str, status: str = None):
        return core.store.decisions(uid, status=status)

    @api.post("/api/users/{uid}/decisions/{did}")
    def resolve(uid: str, did: str, body: dict = Body(...)):
        return core.resolve_decision(uid, did, body["action"], body.get("answer"))

    @api.post("/api/users/{uid}/review")
    def review(uid: str):
        tr = Trace()
        rv = policy.review(core, uid, tr, trigger="manual")
        return {"status": rv["status"], "decisions": rv["decisions"], "conflicts": rv["conflicts"], "trace": tr.steps}

    # ---- insight & audit ----------------------------------------------------------------------
    @api.get("/api/users/{uid}/insight")
    def insight(uid: str):
        return core.insight(uid)

    @api.post("/api/users/{uid}/beliefs")
    def correct(uid: str, body: dict = Body(...)):
        return core.correct_belief(uid, body["concept"], float(body["mean"]))

    @api.get("/api/users/{uid}/events")
    def events(uid: str, limit: int = 100):
        return core.store.events(uid, limit=limit, newest_first=True)

    # ---- supervisor chat --------------------------------------------------------------------------
    @api.get("/api/users/{uid}/messages")
    def messages(uid: str):
        return core.store.messages(uid)

    @api.delete("/api/users/{uid}/messages")
    def clear_messages(uid: str):
        core.store.clear_messages(uid)
        return {"ok": True}

    @api.post("/api/users/{uid}/chat")
    def chat(uid: str, body: dict = Body(...)):
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "empty message")
        q = queue.Queue()

        def work():
            try:
                out = sup.chat(uid, text, sink=q.put, think=body.get("think"))
                q.put({"type": "done", **out})
            except Exception as e:  # noqa: BLE001
                log.exception("chat failed")
                q.put({"type": "error", "error": f"{type(e).__name__}: {e}"})
            q.put(None)

        threading.Thread(target=work, daemon=True).start()

        def stream():
            while True:
                item = q.get()
                if item is None:
                    break
                yield json.dumps(item, default=float) + "\n"
        return StreamingResponse(stream(), media_type="application/x-ndjson")

    # ---- knowledge ---------------------------------------------------------------------------------
    @api.get("/api/graph/stats")
    def graph_stats():
        return core.graph.stats()

    @api.get("/api/graph/search")
    def graph_search(q: str, label: str = None):
        return core.graph.search_nodes(q, [label] if label else None)

    @api.get("/api/graph/neighborhood")
    def graph_neighborhood(uid: str, depth: int = 1):
        return core.graph.neighborhood(uid, depth=depth)

    @api.get("/api/knowledge/search")
    def knowledge_search(q: str, kinds: str = None, k: int = 6):
        return {"hits": core.retriever.search(q, k=k, kinds=kinds.split(",") if kinds else None),
                "retriever": core.retriever.description}

    @api.get("/api/knowledge/documents")
    def documents():
        return core.store.documents()

    @api.post("/api/knowledge/documents")
    def add_document(body: dict = Body(...)):
        if not body.get("title", "").strip() or not body.get("text", "").strip():
            raise HTTPException(400, "title and text are required")
        return {"doc_id": core.add_document(body["title"], body["text"]), "retriever": core.retriever.description}

    @api.delete("/api/knowledge/documents/{doc_id}")
    def delete_document(doc_id: str):
        core.delete_document(doc_id)
        return {"ok": True}

    return api


def main():
    import os

    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Ollama call is noise
    uvicorn.run(create_app(), host=os.environ.get("FOODREC_HOST", "127.0.0.1"),
                port=int(os.environ.get("FOODREC_PORT", 8000)))


if __name__ == "__main__":
    main()

