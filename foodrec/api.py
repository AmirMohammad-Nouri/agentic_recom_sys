"""HTTP API and web UI.

Run:  python -m foodrec            (or: uvicorn foodrec.api:app)
The chat endpoint streams newline-delimited JSON: one object per trace step while the
supervisor works, then a final {"type": "done"} object with the stored message.
"""
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import CRITERION_LABELS
from .access import redact_attachments, redact_message, redact_result
from .agent import Supervisor
from .agent import policy
from .agent.trace import Trace
from .appconfig import (ASSIGNMENT, FIELDS, REGISTRATION, ROOT, SECTIONS, AppConfig, criteria_catalog,
                        feature_catalog)
from .config import load_settings
from .export import export, to_json, to_zip
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

    @api.exception_handler(PermissionError)
    async def _forbidden(_: Request, e: PermissionError):
        return JSONResponse({"error": str(e)}, status_code=403)

    # ---- access roles -------------------------------------------------------------------
    def need(uid, *features):
        """403 unless the user's role has every one of `features`. Returns the access record."""
        acc = core.access(uid)
        missing = [f for f in features if not acc["features"].get(f)]
        if missing:
            raise HTTPException(403, f"not available for the role '{acc['role']}' ({', '.join(missing)})")
        return acc

    def viewer(request, *features):
        """For endpoints that are not user-scoped: check the role of the X-User header, if sent."""
        uid = request.headers.get("x-user")
        return need(uid, *features) if uid else None

    def admin(request):
        token = os.environ.get("FOODREC_ADMIN_TOKEN")
        if token:
            if request.headers.get("x-admin-token") != token:
                raise HTTPException(401, "admin token required (FOODREC_ADMIN_TOKEN)")
            return
        uid = request.headers.get("x-user")
        if not uid or not core.access(uid)["features"]["configure"]:
            raise HTTPException(403, "only a role with the 'configure' feature can do this")

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
        return core.onboard(uid, body.get("declared") or {}, body.get("weights"), body.get("diversity"),
                            body.get("mode"))

    @api.put("/api/users/{uid}/profile")
    def profile(uid: str, body: dict = Body(...)):
        need(uid, "page_profile")
        return core.update_profile(uid, body["declared"], "user", body.get("note"))

    @api.get("/api/users/{uid}/profile/history")
    def profile_history(uid: str):
        need(uid, "page_profile")
        return core.store.profile_history(uid)

    @api.post("/api/users/{uid}/goal")
    def goal(uid: str, body: dict = Body(...)):
        need(uid, "weight_controls")
        return core.save_goal(uid, body.get("name") or "My priorities", body["weights"], body.get("diversity", 0.3),
                              body.get("mode", "lightweight"))

    @api.get("/api/users/{uid}/goal/history")
    def goal_history(uid: str):
        need(uid, "page_profile")
        return core.store.goal_history(uid)

    @api.post("/api/users/{uid}/goal/revert")
    def goal_revert(uid: str, body: dict = Body(...)):
        need(uid, "page_profile", "weight_controls")
        return core.revert_goal(uid, int(body["version"]))

    # ---- recommendations ------------------------------------------------------------------
    @api.post("/api/users/{uid}/recommend")
    def recommend(uid: str, body: dict = Body(default={})):
        if not core.store.profile(uid):
            raise HTTPException(409, "complete onboarding first")
        acc = core.access(uid)
        # without priority controls the saved goal is always used
        ov = {k: body.get(k) for k in ("weights", "diversity", "mode")} if acc["features"]["weight_controls"] else {}
        if body.get("meal") in MEAL_CONTEXTS:
            ov["request_filters"] = [{"field": "meal", "op": "is", "value": MEAL_CONTEXTS[body["meal"]],
                                      "source": f"{body['meal']} context"}]
        res = core.recommend(uid, ov, origin="ui")
        res["pipeline_trace"] = pipeline_trace(res)
        return redact_result(res, acc)

    @api.post("/api/users/{uid}/preview")
    def preview(uid: str, body: dict = Body(...)):
        """Local preview of a settings change; nothing is stored."""
        need(uid, "weight_controls")
        wi = core.what_if(uid, body.get("weights"), body.get("diversity"), body.get("mode"))
        return {"comparison": wi["comparison"],
                "titles": {r["recipe_id"]: r["title"] for r in wi["alternative"].get("results", []) +
                           wi["base"].get("results", [])},
                "weights_applied": wi["alternative"]["config"]["weights_applied"]}

    @api.post("/api/users/{uid}/goal-options")
    def goal_options(uid: str, body: dict = Body(...)):
        """Several small goal changes with their lists, stored as one decision; nothing is saved
        until the user picks an option (POST /decisions/{id} with action=accept, answer=o1...)."""
        need(uid, "weight_controls")
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
        acc = need(uid, "feedback")
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
        if not acc["features"]["supervisor"]:
            return {**out, "review": {"status": "disabled", "decisions": [], "trace": []}}
        rv = policy.review(core, uid, tr, trigger="feedback")
        return {**out, "review": {"status": rv["status"], "decisions": rv["decisions"],
                                  "trace": tr.steps if acc["features"]["agent_trace"] else []}}

    @api.get("/api/users/{uid}/decisions")
    def decisions(uid: str, status: str = None):
        ds = core.store.decisions(uid, status=status)
        if not core.access(uid)["features"]["supervisor"]:
            ds = [d for d in ds if d["kind"] == "profile_change"]  # confirmations the user asked for in chat
        return ds

    @api.post("/api/users/{uid}/decisions/{did}")
    def resolve(uid: str, did: str, body: dict = Body(...)):
        return core.resolve_decision(uid, did, body["action"], body.get("answer"))

    @api.post("/api/users/{uid}/review")
    def review(uid: str):
        need(uid, "supervisor")
        tr = Trace()
        rv = policy.review(core, uid, tr, trigger="manual")
        return {"status": rv["status"], "decisions": rv["decisions"], "conflicts": rv["conflicts"], "trace": tr.steps}

    # ---- insight & audit ----------------------------------------------------------------------
    @api.get("/api/users/{uid}/insight")
    def insight(uid: str):
        need(uid, "page_insights")
        return core.insight(uid)

    @api.post("/api/users/{uid}/beliefs")
    def correct(uid: str, body: dict = Body(...)):
        need(uid, "page_insights")
        return core.correct_belief(uid, body["concept"], float(body["mean"]))

    @api.get("/api/users/{uid}/events")
    def events(uid: str, limit: int = 100):
        need(uid, "page_activity")
        return core.store.events(uid, limit=limit, newest_first=True)

    @api.post("/api/users/{uid}/ui-events")
    def ui_event(uid: str, body: dict = Body(...)):
        """Interface interactions (page views, panels opened) for the study log."""
        data = {k: v for k, v in body.items() if k != "kind" and isinstance(v, (str, int, float, bool))}
        return core.log_ui_event(uid, body.get("kind"), data)

    @api.get("/api/users/{uid}/export")
    def export_own(uid: str, format: str = "json"):
        need(uid, "export_own")
        return _download(export(core, [uid]), format, f"foodrec-{uid}")

    # ---- supervisor chat --------------------------------------------------------------------------
    @api.get("/api/users/{uid}/messages")
    def messages(uid: str):
        acc = need(uid, "assistant")
        return [redact_message(m, acc) for m in core.store.messages(uid)]

    @api.delete("/api/users/{uid}/messages")
    def clear_messages(uid: str):
        core.store.clear_messages(uid)
        return {"ok": True}

    @api.post("/api/users/{uid}/chat")
    def chat(uid: str, body: dict = Body(...)):
        acc = need(uid, "assistant")
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "empty message")
        q = queue.Queue()
        show_trace = acc["features"]["agent_trace"]

        def sink(item):
            if show_trace:
                q.put(item)

        def work():
            try:
                out = sup.chat(uid, text, sink=sink, think=body.get("think") if show_trace else False)
                q.put({"type": "done", **out, "message": redact_message(out["message"], acc)})
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
    def graph_stats(request: Request):
        viewer(request, "page_knowledge")
        return core.graph.stats()

    @api.get("/api/graph/search")
    def graph_search(request: Request, q: str, label: str = None):
        viewer(request, "page_knowledge")
        return core.graph.search_nodes(q, [label] if label else None)

    @api.get("/api/graph/neighborhood")
    def graph_neighborhood(request: Request, uid: str, depth: int = 1):
        viewer(request, "page_knowledge")
        return core.graph.neighborhood(uid, depth=depth)

    @api.get("/api/knowledge/search")
    def knowledge_search(request: Request, q: str, kinds: str = None, k: int = 6):
        viewer(request, "page_knowledge")
        return {"hits": core.retriever.search(q, k=k, kinds=kinds.split(",") if kinds else None),
                "retriever": core.retriever.description}

    @api.get("/api/knowledge/documents")
    def documents(request: Request):
        viewer(request, "page_knowledge")
        return core.store.documents()

    @api.post("/api/knowledge/documents")
    def add_document(request: Request, body: dict = Body(...)):
        viewer(request, "page_knowledge")
        if not body.get("title", "").strip() or not body.get("text", "").strip():
            raise HTTPException(400, "title and text are required")
        return {"doc_id": core.add_document(body["title"], body["text"]), "retriever": core.retriever.description}

    @api.delete("/api/knowledge/documents/{doc_id}")
    def delete_document(request: Request, doc_id: str):
        viewer(request, "page_knowledge")
        core.delete_document(doc_id)
        return {"ok": True}

    # ---- configuration and study administration ---------------------------------------------------
    def _shown(v):
        if isinstance(v, Path):
            try:
                return str(v.relative_to(ROOT))
            except ValueError:
                return str(v)
        return v

    def config_view():
        cfg = core.cfg
        st = core.llm.status() if hasattr(core.llm, "status") else {}
        return {
            "path": str(cfg.path) if cfg.path else None, "exists": bool(cfg.path and Path(cfg.path).exists()),
            "version": cfg.version, "raw": cfg.raw, "effective": cfg.data, "errors": cfg.errors,
            "sections": SECTIONS,
            "fields": [{**{k: v for k, v in f.items() if k != "attr"}, "running": _shown(getattr(core.s, f["attr"])),
                        "file_value": cfg.value(f["section"], f["key"]), "source": core.s.sources.get(f["attr"]),
                        "env_set": bool(f.get("env") and os.environ.get(f["env"]))} for f in FIELDS],
            "features": feature_catalog(), "criteria": criteria_catalog(),
            "registration_options": REGISTRATION, "assignment_options": ASSIGNMENT,
            "restart_pending": core.restart_pending, "admin_token": bool(os.environ.get("FOODREC_ADMIN_TOKEN")),
            "installed_models": st.get("installed") or [], "llm_status": st,
        }

    @api.get("/api/config")
    def get_config(request: Request):
        admin(request)
        return config_view()

    @api.post("/api/config/validate")
    def validate_config(request: Request, body: dict = Body(...)):
        admin(request)
        return {"errors": AppConfig(body.get("config") or {}).errors}

    @api.put("/api/config")
    def put_config(request: Request, body: dict = Body(...)):
        """Write the config file and apply what can change without a restart."""
        admin(request)
        new = core.cfg.save(body.get("config") or {})
        pending = core.apply_config(new)
        log.info("config saved to %s (%d setting(s) need a restart)", new.path, len(pending))
        return config_view()

    @api.get("/api/admin/users")
    def admin_users(request: Request):
        admin(request)
        out = []
        for u in core.store.user_rows():
            acc = core.access(u["user_id"])
            out.append({**u, "role": acc["role"], "role_source": acc["source"]})
        return {"users": out, "roles": {k: v.get("label", k) for k, v in core.cfg.roles.items()},
                "registration": core.cfg.study.get("registration"), "max_users": core.cfg.study.get("max_users"),
                "onboarded": len(core.store.onboarded_users())}

    @api.put("/api/admin/users/{uid}/role")
    def admin_set_role(request: Request, uid: str, body: dict = Body(...)):
        admin(request)
        return core.set_role(uid, body["role"])

    @api.get("/api/export")
    def export_all(request: Request, format: str = "json", users: str = None, raw: bool = True):
        admin(request)
        return _download(export(core, users.split(",") if users else None, include_raw=raw), format, "foodrec-export")

    def _download(doc, fmt, stem):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if fmt == "zip":
            return Response(to_zip(doc), media_type="application/zip",
                            headers={"Content-Disposition": f'attachment; filename="{stem}-{stamp}.zip"'})
        return Response(to_json(doc), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{stem}-{stamp}.json"'})

    return api


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Ollama call is noise
    uvicorn.run(create_app(), host=os.environ.get("FOODREC_HOST", "127.0.0.1"),
                port=int(os.environ.get("FOODREC_PORT", 8000)))


if __name__ == "__main__":
    main()

