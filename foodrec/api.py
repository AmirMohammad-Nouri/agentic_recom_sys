"""HTTP API and web UI.

Run:  python -m foodrec            (or: uvicorn foodrec.api:app)
The chat endpoint streams newline-delimited JSON: one object per trace step while the
supervisor works, then a final {"type": "done"} object with the stored message.
"""
import hmac
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import unquote

import foodrec

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import CRITERION_LABELS
from .access import redact_attachments, redact_message, redact_recipe, redact_result
from .agent import Supervisor
from .agent import policy
from .agent.trace import Trace
from .appconfig import (ASSIGNMENT, FIELDS, REGISTRATION, ROOT, SECTIONS, AppConfig, criteria_catalog,
                        feature_catalog, secure)
from .config import load_settings
from .export import export, to_json, to_zip
from .model_spec import KINDS
from .study import LINK_SESSION_SECONDS, admin_link_token, check_password, credential_fp, generate, validate_study

BOOT_ID = uuid.uuid4().hex[:12]  # changes on every (re)start; the Config page waits for a new one
from .features import breakdown, health_grade
from .service import MEAL_CONTEXTS, App

WEB = Path(__file__).parent / "web"
log = logging.getLogger("foodrec.api")


class _RevalidatedStatic(StaticFiles):
    """Static files the browser must revalidate (ETag, 304) on every load.

    The ES modules import each other by unversioned URLs, so a heuristically cached old
    module next to a new one breaks the whole import graph and the UI never starts.
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


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
        {"kind": "reason", "title": "Similarity score", "source": "engine",
         "detail": "Score = " + " + ".join(f"{cfg['weights_applied'][k]:.2f}*{CRITERION_LABELS[k]}" for k in cfg["weights_applied"])
                   + f" (taste share {round(100 * cfg['taste_share'])}%). {cfg['weights_message']}"},
        {"kind": "tool", "title": f"Diversity rerank (d = {cfg['diversity']:.2f})", "source": "engine",
         "detail": f"{sum(1 for r in res['results'] if r['evidence']['ranking']['diversity']['promoted'])} recipe(s) promoted "
                   f"for variety; similarity rank and final rank are both recorded.",
         "duration_ms": st.get("rerank", {}).get("wall_ms")},
        {"kind": "answer", "title": "Evidence records built", "source": "engine",
         "detail": f"One evidence record per recipe; reasons are templates filled from it. Total {tel['total_wall_ms']:.1f} ms."},
    ]
    return steps


def create_app(settings=None, llm=None):
    settings = settings or load_settings()
    core = App(settings, llm=llm)
    sup = Supervisor(core)
    api = FastAPI(title="FoodRec", version=foodrec.__version__)
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

    # ---- who is asking: depends on server.mode -------------------------------------------
    #   local  everyone signs in (the administrator account or a participant account)
    #   demo   no sign-in; each browser is a visitor ("visitor-<12 hex>") who picks the admin or
    #          the user view; the Config page is read-only and nothing can be administered
    #   open   no sign-in; the browser names the user (X-User). Development and tests only.
    VISITOR_RE = re.compile(r"^visitor-[0-9a-f]{12}$")

    def mode():
        return getattr(core.s, "server_mode", "open")

    def login_required():
        return mode() == "local"

    def session_user(request):
        auth = request.headers.get("authorization") or ""
        return core.sessions.user(auth[7:], core.cfg) if auth.lower().startswith("bearer ") else None

    def who(request):
        """The user making the request."""
        if mode() == "demo":
            uid = request.headers.get("x-user") or ""
            return uid if VISITOR_RE.match(uid) else None
        return session_user(request) or (None if login_required() else request.headers.get("x-user"))

    def admin_token_ok(request):
        if mode() == "demo":
            return False  # nothing can be administered on a demo server
        tok = request.headers.get("x-admin-token")
        if not tok:
            return False
        env = os.environ.get("FOODREC_ADMIN_TOKEN")
        link = admin_link_token(core.s.data_dir)
        return bool(env and hmac.compare_digest(tok, env)) or hmac.compare_digest(tok, link)

    def viewer(request, *features):
        """For endpoints that are not user-scoped: check the role of the requesting user."""
        uid = who(request)
        return need(uid, *features) if uid else None

    def admin(request, write=False):
        """The Config page and study administration: the admin token (FOODREC_ADMIN_TOKEN, or the
        link printed by `foodrec config open`) or a signed-in user whose role has 'configure'. In
        demo mode the admin view may look (read-only) and nothing may be changed (`write`)."""
        if mode() == "demo":
            uid = who(request)
            if not uid or not core.access(uid)["features"]["configure"]:
                raise HTTPException(403, "switch to the admin view (account menu) to see the Config page")
            if write:
                raise HTTPException(403, "this is a demo: settings can't be changed here. Clone the project "
                                         "to configure your own copy.")
            return uid
        if admin_token_ok(request):
            return None
        uid = session_user(request) or (None if (login_required() or os.environ.get("FOODREC_ADMIN_TOKEN"))
                                        else request.headers.get("x-user"))
        if not uid:
            raise HTTPException(401, "sign in as the administrator, or open the link from `foodrec config open`")
        if not core.access(uid)["features"]["configure"]:
            raise HTTPException(403, "only the administrator can do this")
        return uid

    # ---- simple attempt limits (sign-in everywhere; visitors and chat in the demo) ------------
    limits = {}

    def over_limit(key, n, seconds):
        now = time.time()
        hits = [t for t in limits.get(key, []) if t > now - seconds]
        if len(hits) >= n:
            limits[key] = hits
            return True
        limits[key] = hits + [now]
        return False

    # Every user-scoped call must come from that user (or an admin); other data endpoints need a
    # signed-in user (local) or a visitor id (demo).
    OPEN_API = ("/api/system", "/api/login", "/api/join", "/api/session", "/api/logout")

    @api.middleware("http")
    async def _require_login(request: Request, call_next):
        path = request.url.path
        guarded = path.startswith("/api/") and not path.startswith(OPEN_API) and \
            not path.startswith(("/api/config", "/api/admin", "/api/export"))
        if mode() == "demo" and path == "/api/session":
            guarded = False
        m = re.match(r"^/api/users/([^/]+)/", path)
        if guarded and login_required() and not admin_token_ok(request):
            uid = session_user(request)
            if not uid:
                return JSONResponse({"error": "please sign in", "login_required": True}, status_code=401)
            if m and unquote(m.group(1)) != uid and not core.access(uid)["features"]["configure"]:
                return JSONResponse({"error": "you can only use your own account"}, status_code=403)
        if guarded and mode() == "demo":
            uid = who(request)
            if not uid or (m and unquote(m.group(1)) != uid):
                return JSONResponse({"error": "demo visitors can only use their own visitor id"}, status_code=403)
            link = core.store.demo_link(user_id=uid)
            if link and link["status"] != "active":
                return JSONResponse({"error": "this link has been deactivated"}, status_code=403)
            ip = request.client.host if request.client else "?"
            if path.endswith("/onboard") and over_limit(("onboard", ip), 20, 3600):
                return JSONResponse({"error": "too many new visitors from this address; try again later"}, status_code=429)
            if path.endswith("/chat") and over_limit(("chat", uid), 30, 3600):
                return JSONResponse({"error": "the demo allows 30 assistant messages per hour"}, status_code=429)
        return await call_next(request)

    # ---- sign-in ------------------------------------------------------------------------------
    @api.post("/api/login")
    def login(request: Request, body: dict = Body(...)):
        if mode() == "demo":
            raise HTTPException(400, "the demo has no sign-in")
        user, pw = (body.get("username") or "").strip(), body.get("password") or ""
        ip = request.client.host if request.client else "?"
        if over_limit(("login", ip), 10, 300):
            raise HTTPException(429, "too many sign-in attempts; wait a few minutes")
        if not check_password(core.cfg, user, pw):
            raise HTTPException(401, "unknown username or wrong password")
        core.store.ensure_user(user)
        return {"token": core.sessions.issue(user, credential_fp(core.cfg, user)), "user_id": user,
                "access": core.access(user)}

    @api.post("/api/join")
    def join(request: Request, body: dict = Body(...)):
        """Open a personal link (/join/<token>): the link itself is the way in."""
        token = body.get("token") or ""
        ip = request.client.host if request.client else "?"
        if over_limit(("join", ip), 60, 300):
            raise HTTPException(429, "too many attempts; wait a few minutes")
        if mode() == "demo":
            link = core.store.demo_link(token=token) if token else None
            if not link or link["status"] == "removed":
                raise HTTPException(404, "this link is not valid (any more)")
            if link["status"] != "active":
                raise HTTPException(403, "this link has been deactivated")
            core.store.ensure_user(link["user_id"])
            return {"user_id": link["user_id"], "mode": "demo"}
        p = core.cfg.participant_by_link(token)
        if not p:
            raise HTTPException(404, "this link is not valid (any more)")
        if p.get("active") is False:
            raise HTTPException(403, "this link has been deactivated")
        core.store.ensure_user(p["username"])
        return {"token": core.sessions.issue(p["username"], credential_fp(core.cfg, p["username"]), ttl=LINK_SESSION_SECONDS),
                "user_id": p["username"], "mode": mode()}

    @api.get("/join/{token}", include_in_schema=False)
    def join_page(token: str):
        """The app; it reads the token from the address and signs the person in."""
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @api.get("/api/session")
    def session(request: Request):
        uid = who(request) if mode() == "demo" else session_user(request)
        return {"user_id": uid, "mode": mode(), "login_required": login_required(),
                "view": core.demo_views.get(uid, "user") if mode() == "demo" else None}

    @api.post("/api/demo/view")
    def demo_view(request: Request, body: dict = Body(...)):
        """Demo only: see the app as the administrator or as a user."""
        if mode() != "demo":
            raise HTTPException(404, "only on a demo server")
        uid = who(request)
        if not uid:
            raise HTTPException(400, "no visitor id")
        return core.set_demo_view(uid, body.get("view"))

    # ---- web ----------------------------------------------------------------------------
    api.mount("/static", _RevalidatedStatic(directory=WEB), name="static")

    @api.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    @api.get("/guide", include_in_schema=False)
    def guide_page():
        """The complete guide (concepts and every equation): docs/guide/index.html."""
        path = ROOT / "docs" / "guide" / "index.html"
        if not path.exists():
            raise HTTPException(404, "the guide is not installed (docs/guide/index.html)")
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    @api.get("/config", include_in_schema=False)
    def config_page():
        """The Config page, separate from the app (open it with `foodrec config open`)."""
        return FileResponse(WEB / "config.html", headers={"Cache-Control": "no-cache"})

    # ---- system -------------------------------------------------------------------------
    @api.get("/api/system")
    def system():
        return {**core.system(), "skills": sup.skills.catalog(), "thresholds": {k: getattr(core.s, k, d) for k, d in policy.THRESHOLDS.items()},
                "boot_id": BOOT_ID, "pid": os.getpid(), "login_required": login_required(), "mode": mode(),
                "restarting": getattr(core, "restarting", False)}

    def _brief(r):
        sc = core.scaler.scores(r)
        return {"recipe_id": r["id"], "title": r["title"], "cuisine": r["cuisine"], "meal": r.get("meal"),
                "image_url": r.get("image_url"), "n_ingredients": r.get("n_ingredients"),
                "kcal": r.get("kcal_per_serving"), "co2e_kg_per_kg": (r.get("footprint") or {}).get("kg_co2e_per_kg"),
                "health_grade": health_grade(sc.get("h")), "scores": sc,
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

    # ---- meal cycles ------------------------------------------------------------------------------
    def _cycle_view(uid, st):
        if st.get("result"):
            st = {**st, "result": redact_result(st["result"], core.access(uid))}
        return st

    @api.get("/api/users/{uid}/cycle")
    def cycle(uid: str):
        if not core.store.profile(uid):
            raise HTTPException(409, "complete onboarding first")
        return _cycle_view(uid, core.cycle_state(uid))

    @api.post("/api/users/{uid}/cycle/next")
    def cycle_next(uid: str):
        return _cycle_view(uid, core.cycle_next(uid))

    @api.post("/api/users/{uid}/cycle/restart")
    def cycle_restart(uid: str):
        return _cycle_view(uid, core.cycle_restart(uid))

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

    @api.get("/api/users/{uid}/dishes")
    def dishes(uid: str):
        """My dishes: saved for later, liked and cooked (each shown as the role allows)."""
        acc = need(uid, "page_dishes")
        return {col: [redact_recipe(r, acc) for r in rows] for col, rows in core.my_dishes(uid).items()}

    @api.post("/api/users/{uid}/dishes/remove")
    def remove_dish(uid: str, body: dict = Body(...)):
        need(uid, "page_dishes")
        return core.remove_dish(uid, body.get("recipe_id"), body.get("collection"))

    @api.post("/api/users/{uid}/ui-events")
    def ui_event(uid: str, body: dict = Body(...)):
        """Interface interactions (page views, panels opened) for the study log."""
        data = {k: v for k, v in body.items() if k != "kind" and isinstance(v, (str, int, float, bool))}
        return core.log_ui_event(uid, body.get("kind"), data)

    @api.get("/api/users/{uid}/export")
    def export_own(uid: str, format: str = "json"):
        need(uid, "export_own")
        return _download(export(core, [uid]), format, export_stem([uid]))

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

    def _no_hash(d):
        """The config without the administrator's password hash (it is kept on save, see put_config)."""
        d = json.loads(json.dumps(d))
        adm = (d.get("access") or {}).get("admin")
        if isinstance(adm, dict):
            adm["password_set"] = bool(adm.pop("password_hash", None))
            adm.pop("password", None)
        return d

    def config_view(request):
        cfg = core.cfg
        st = core.llm.status() if hasattr(core.llm, "status") else {}
        demo = mode() == "demo"
        return {
            "path": str(cfg.path) if cfg.path else None, "exists": bool(cfg.path and Path(cfg.path).exists()),
            "version": cfg.version, "raw": _no_hash(cfg.raw), "effective": _no_hash(cfg.data), "errors": cfg.errors,
            "mode": mode(), "read_only": demo,
            "visitor_settings": core.store.visitor_settings(who(request)) if demo and who(request) else None,
            "demo_links": [{"username": x["label"], "link": x["token"], "role": x["role"], "active": x["status"] == "active"}
                           for x in core.store.demo_links(who(request))] if demo and who(request) else None,
            "sections": SECTIONS,
            "fields": [{**{k: v for k, v in f.items() if k != "attr"}, "attr_name": f["attr"], "running": _shown(getattr(core.s, f["attr"])),
                        "file_value": cfg.value(f["section"], f["key"]), "source": core.s.sources.get(f["attr"]),
                        "env_set": bool(f.get("env") and os.environ.get(f["env"]))} for f in FIELDS],
            "features": feature_catalog(), "criteria": criteria_catalog(),
            "registration_options": REGISTRATION, "assignment_options": ASSIGNMENT,
            "restart_pending": core.restart_pending, "admin_token": bool(os.environ.get("FOODREC_ADMIN_TOKEN")),
            "installed_models": st.get("installed") or [], "llm_status": st,
            "auto_reload": bool(core.s.auto_reload and getattr(core, "watcher", None)),
            "config_problem": getattr(core, "config_problem", None),
            "model_spec": {"running": foodrec.MODEL_SPEC, "errors": foodrec.MODEL_SPEC_ERRORS,
                           "source": foodrec.MODEL_SPEC_SOURCE},
            "field_kinds": list(KINDS), "boot_id": BOOT_ID, "numeric_fields": core.numeric_fields(),
            # which key variables are set (never their values)
            "api_keys": {} if demo else {**{n: True for n in os.environ if n.endswith(("_API_KEY", "_TOKEN"))},
                                         **{n: bool(os.environ.get(n)) for n in
                                            {core.s.llm_api_key_env or "", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"} if n}},
        }

    @api.get("/api/config")
    def get_config(request: Request):
        admin(request)
        return config_view(request)

    @api.post("/api/config/validate")
    def validate_config(request: Request, body: dict = Body(...)):
        admin(request)
        return {"errors": AppConfig(body.get("config") or {}).errors}

    @api.put("/api/config")
    def put_config(request: Request, body: dict = Body(...)):
        """Write the config file and apply what can change without a restart."""
        admin(request, write=True)
        data = body.get("config") or {}
        # the page never receives the password hash: keep it unless a new password was typed or the
        # administrator was renamed
        adm, cur = (data.get("access") or {}).get("admin"), core.cfg.admin_account()
        if isinstance(adm, dict) and cur and not adm.get("password") and adm.get("username") == cur.get("username"):
            adm["password_hash"] = cur.get("password_hash")
        if isinstance(adm, dict):
            adm.pop("password_set", None)
        new = core.cfg.save(data)
        pending = core.apply_config(new)
        log.info("config saved to %s (%d setting(s) need a restart)", new.path, len(pending))
        w = getattr(core, "watcher", None)
        restarting = bool(pending and core.s.auto_reload and w)
        if restarting:
            w.check()  # the watcher would see the new file within a second anyway
        return {**config_view(request), "restarting": restarting}

    @api.post("/api/config/secret")
    def set_secret(request: Request, body: dict = Body(...)):
        """Store an API key in .env (never in the config file) and use it at once.
        {"name": "OPENAI_API_KEY", "value": "sk-..."}; value null removes it."""
        admin(request, write=True)
        from . import config as cfgmod
        out = cfgmod.set_dotenv(body.get("name"), body.get("value"), cfgmod.DOTENV)
        if hasattr(core.llm, "_status"):
            core.llm._status = None
        if hasattr(core.llm, "_client"):
            core.llm._client = None  # the Claude client caches its key
        log.info("%s %s in %s", out["name"], "updated" if out["set"] else "removed", out["file"])
        return {**out, "llm_status": core.llm.status(refresh=True) if hasattr(core.llm, "status") else {}}

    @api.get("/api/config/field-stats")
    def field_stats(request: Request, field: str):
        admin(request)
        return core.field_stats(field)

    @api.post("/api/config/population")
    def generate_population(request: Request, body: dict = Body(...)):
        """Participants for a population spec (nothing is saved; the page saves the result). In the
        demo: personal links only, stored for this visitor (see App.demo_population)."""
        me = admin(request)
        pop = body.get("population") or {}
        if mode() == "demo":
            pop = {**pop, "access": "link"}
        errs = validate_study({"population": pop}, core.cfg.roles)
        if errs:
            raise HTTPException(400, "; ".join(errs))
        if mode() == "demo":
            if int(pop.get("size") or 0) > 50:
                raise HTTPException(400, "the demo creates at most 50 links at a time")
            if over_limit(("population", me), 20, 3600):
                raise HTTPException(429, "the demo allows 20 generated populations per hour")
            return core.demo_population(me, pop)
        parts, counts = generate(pop, body.get("participants"))
        return {"participants": parts, "counts": counts}

    @api.post("/api/demo/links")
    def demo_link_action(request: Request, body: dict = Body(...)):
        """Demo: deactivate, activate or remove one of this visitor's links."""
        if mode() != "demo":
            raise HTTPException(404, "only on a demo server")
        me = admin(request)
        status = {"deactivate": "inactive", "activate": "active", "remove": "removed"}.get(body.get("action"))
        if not status:
            raise HTTPException(400, "action must be deactivate, activate or remove")
        if not core.store.set_demo_link_status(me, body.get("token") or "", status):
            raise HTTPException(404, "unknown link")
        return {"demo_links": [{"username": x["label"], "link": x["token"], "role": x["role"], "active": x["status"] == "active"}
                               for x in core.store.demo_links(me)]}

    @api.put("/api/demo/settings")
    def demo_settings(request: Request, body: dict = Body(...)):
        """Demo: settings a visitor tries for themselves; other visitors and the file are untouched."""
        if mode() != "demo":
            raise HTTPException(404, "only on a demo server")
        me = admin(request)
        return {"visitor_settings": core.save_visitor_settings(me, body.get("settings") or {})}

    @api.post("/api/config/restart")
    def restart_now(request: Request):
        admin(request, write=True)
        w = getattr(core, "watcher", None)
        if not w:
            raise HTTPException(409, "the app was not started with `foodrec serve`, so it cannot restart itself")
        core.restarting = True
        threading.Timer(0.5, w.restart).start()
        return {"restarting": True}

    @api.get("/api/admin/users")
    def admin_users(request: Request):
        me = admin(request)
        out = []
        for u in core.store.user_rows():
            if mode() == "demo" and u["user_id"] != me:
                continue  # demo visitors never see each other
            acc = core.access(u["user_id"])
            out.append({**u, "role": acc["role"], "role_source": acc["source"]})
        return {"users": out, "roles": {k: v.get("label", k) for k, v in core.cfg.roles.items()},
                "registration": core.cfg.study.get("registration"), "max_users": core.cfg.study.get("max_users"),
                "onboarded": len(core.store.onboarded_users())}

    @api.put("/api/admin/users/{uid}/role")
    def admin_set_role(request: Request, uid: str, body: dict = Body(...)):
        admin(request, write=True)
        return core.set_role(uid, body["role"])

    @api.get("/api/export")
    def export_all(request: Request, format: str = "json", users: str = None, raw: bool = True):
        me = admin(request)
        ids = users.split(",") if users else None
        if mode() == "demo":
            ids = [me]  # a demo visitor exports only their own data
        doc = export(core, ids, include_raw=raw)
        return _download(doc, format, export_stem([u["user_id"] for u in doc["users"]] if ids else None))

    def export_stem(user_ids):
        """File name of an export: the user's name (foodrec-p001), a few names joined
        (foodrec-p001-p002-p003), the count for many (foodrec-12-users), or foodrec-all-users."""
        if not user_ids:
            return "foodrec-all-users"
        safe = [re.sub(r"[^A-Za-z0-9_.-]+", "_", u)[:40] for u in user_ids]
        return "foodrec-" + ("-".join(safe) if len(safe) <= 3 else f"{len(safe)}-users")

    def _download(doc, fmt, stem):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if fmt == "zip":
            return Response(to_zip(doc), media_type="application/zip",
                            headers={"Content-Disposition": f'attachment; filename="{stem}-{stamp}.zip"'})
        return Response(to_json(doc), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{stem}-{stamp}.json"'})

    return api


def main(host=None, port=None, open_page=None):
    """Run the web app. Watches the config file (server.auto_reload) and reloads on changes.
    `open_page`: a path to open in the browser once the server is up (foodrec config open)."""
    import uvicorn

    from .reload import ConfigWatcher
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Ollama call is noise
    cfg = AppConfig.load()
    if cfg.path and secure(cfg.raw)[1]:
        cfg = cfg.save(cfg.raw)
        log.info("administrator password stored as a hash in %s", cfg.path)
    settings = load_settings(config=cfg)
    if settings.server_mode == "local" and not cfg.admin_ready():
        sys.stderr.write(ADMIN_PASSWORD_HELP.format(path=cfg.path or "foodrec.config.json"))
        raise SystemExit(2)
    app = create_app(settings)
    core = app.state.core
    if settings.config.path:
        core.watcher = ConfigWatcher(core, settings.config.path)
        core.watcher.start()
        log.info("watching %s for changes (auto reload %s)", settings.config.path,
                 "on" if settings.auto_reload else "off")
    host, port = host or settings.host, int(port or settings.port)
    if open_page:
        threading.Thread(target=_open_when_ready, args=(_browser_host(host), port, open_page), daemon=True).start()
    log.info("Config page: http://%s:%d/config", _browser_host(host), port)
    uvicorn.run(app, host=host, port=port)


ADMIN_PASSWORD_HELP = """
FoodRec needs an administrator password before it starts (server.mode is "local").
Set it once, either with
    foodrec admin password
or by writing it in {path}:
    "access": {{ "admin": {{ "username": "admin", "password": "choose-a-password" }} }}
The app replaces the password with a hash when it starts. Then sign in with that username.
"""


def _browser_host(host):
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def _open_when_ready(host, port, page, timeout=120):
    """Open the browser once the server answers (loading a large catalogue can take a while)."""
    import webbrowser

    import httpx
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(f"http://{host}:{port}/api/system", timeout=2.0).status_code == 200:
                print(f"Opening http://{host}:{port}{page.split('#')[0]}", flush=True)
                webbrowser.open(f"http://{host}:{port}{page}")
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)


if __name__ == "__main__":
    main()

