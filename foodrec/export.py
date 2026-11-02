"""The standard export: one record per user with everything a study needs to analyse it.

    foodrec export --out study.json            (or GET /api/export, Config page -> Export)
    foodrec export --format zip --out study.zip

Per user (format "foodrec-export", version 1; see docs/configuration.md#export):
  user_id, access          role, the features it had and the criteria it could see
  preferences              current declared profile and goal, and every earlier version
  criteria                 per criterion: saved weight, weight revealed by behaviour, gap, visible?
  taste_beliefs            learned taste concepts with evidence counts
  recommendations          every list shown: settings, and per recipe all scores plus the
                           scores of the parameters that were visible to this user
  interactions             feedback with the recipe's rank and the scores visible when it was given
  decisions                supervisor questions and proposals, with the user's response
  my_dishes                what is in Saved for later, Liked and Cooked now
  conversation             chat turns with the agent steps behind each answer
  ui_events                page views and panels opened (interface interaction)
  events                   the raw, append-only event log
  log                      one chronological timeline merging all of the above
  summary                  counts, rates and durations for quick analysis
The ZIP holds export.json plus flat CSV tables of the same data, one row per item.
"""
import csv
import io
import json
import time
import zipfile
from collections import Counter

from . import CRITERIA, CRITERION_LABELS, VALUE_CRITERIA, __version__
from .access import visible_scores
from .appconfig import criteria_catalog, feature_catalog

FORMAT, FORMAT_VERSION = "foodrec-export", 1


def _iso(ts):
    return None if ts is None else time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def _recommendation(app, req):
    res = req["result"]
    acc = res.get("access") or {}
    vis = acc.get("visible_criteria", list(CRITERIA))
    cfg = res.get("config") or {}
    el = res.get("eligibility") or {}
    return {
        "request_id": req["request_id"], "ts": req["ts"], "time": _iso(req["ts"]), "origin": req["origin"],
        "profile_version": req["profile_version"], "goal_version": req["goal_version"],
        "role": acc.get("role"), "visible_criteria": vis,
        "features_visible": acc.get("features"),
        "weights_applied": cfg.get("weights_applied"), "weights_source": res.get("weights_source"),
        "diversity": cfg.get("diversity"), "mode": cfg.get("mode"), "health_goal": cfg.get("health_goal"),
        "request_filters": [f.get("label") or f for f in cfg.get("request_filters") or []],
        "eligible": el.get("eligible_count"), "excluded": el.get("excluded_count"),
        "empty": bool(res.get("empty")) and not res.get("results"),
        "results": [{"rank": r["final_rank"], "recipe_id": r["recipe_id"], "title": r["title"],
                     "utility": _r(r.get("utility")), "utility_rank": r.get("utility_rank"),
                     "scores": {k: _r(v) for k, v in (r.get("scores") or {}).items()},
                     "visible_scores": {k: _r(v) for k, v in visible_scores(r.get("scores"), vis).items()},
                     "utility_visible": (acc.get("features") or {}).get("utility", True),
                     "contributions": {k: _r(c.get("contribution") if isinstance(c, dict) else c)
                                       for k, c in (r.get("contributions") or {}).items()},
                     "missing_criteria": r.get("missing_criteria") or [],
                     "promoted_for_diversity": ((r.get("evidence") or {}).get("ranking") or {})
                     .get("diversity", {}).get("promoted", False)}
                    for r in res.get("results") or []],
    }


def _interactions(app, events, recs_by_id):
    out = []
    for e in events:
        if e["type"] != "feedback":
            continue
        p = e["payload"]
        req = recs_by_id.get(p.get("request_id"))
        row = next((r for r in (req or {}).get("results", []) if r["recipe_id"] == p["recipe_id"]), None)
        r = app.catalogue.by_id.get(p["recipe_id"])
        out.append({"event_id": e["event_id"], "ts": e["ts"], "time": _iso(e["ts"]), "type": p["type"],
                    "recipe_id": p["recipe_id"], "title": r["title"] if r else None,
                    "reason": p.get("reason"), "scope": p.get("scope", "lasting"), "request_id": p.get("request_id"),
                    "rank_in_list": row["rank"] if row else None, "role": p.get("role") or (req or {}).get("role"),
                    "scores": row["scores"] if row else None,
                    "visible_scores": row["visible_scores"] if row else None,
                    "utility": row["utility"] if row else None, "context": e.get("context") or {}})
    return out


def _conversation(msgs):
    return [{"message_id": m["message_id"], "ts": m["ts"], "time": _iso(m["ts"]), "role": m["role"], "text": m["text"],
             "agent_steps": [{"kind": s.get("kind"), "title": s.get("title"), "source": s.get("source"),
                              "status": s.get("status"), "detail": s.get("detail") if isinstance(s.get("detail"), str)
                              else None, "duration_ms": s.get("duration_ms")} for s in m.get("trace") or []],
             "attachments": sorted((m.get("attachments") or {}).keys())} for m in msgs]


def _decision(d):
    b = d["body"]
    return {"decision_id": d["decision_id"], "ts": d["ts"], "time": _iso(d["ts"]), "kind": d["kind"],
            "title": d["title"], "status": d["status"], "source": d["source"], "topic": b.get("topic"),
            "criterion": b.get("criterion"), "explanation": b.get("explanation"),
            "stated": b.get("stated"), "proposed_weights": b.get("proposed_weights"),
            "options": [o.get("label") or o.get("id") for o in b.get("options") or []],
            "resolved_ts": d["resolved_ts"], "resolved_time": _iso(d["resolved_ts"]), "resolution": d["resolution"]}


def _event_text(app, e):
    p = e["payload"]
    t = e["type"]
    if t == "feedback":
        title = app.catalogue.by_id.get(p.get("recipe_id"), {}).get("title", p.get("recipe_id"))
        return f"{p['type']} {title}" + (f" ({p['reason']})" if p.get("reason") else "")
    if t == "ui":
        return f"ui {p.get('kind')}" + (f" {p.get('page')}" if p.get("page") else "")
    if t in ("goal_saved", "proposal_accepted"):
        return f"{t} -> goal v{p.get('goal_version')}"
    if t in ("profile_created", "profile_updated"):
        return f"{t} -> profile v{p.get('profile_version', 1)}"
    return t


def export_user(app, user_id, include_raw=True):
    store = app.store
    acc = app.access(user_id)
    prof, goal = store.profile(user_id), store.goal(user_id)
    events = store.events(user_id)
    recs = [_recommendation(app, q) for q in store.requests(user_id)]
    recs_by_id = {r["request_id"]: r for r in recs}
    decisions = [_decision(d) for d in reversed(store.decisions(user_id, limit=100000))]
    msgs = _conversation(store.messages(user_id, limit=None))
    interactions = _interactions(app, events, recs_by_id)
    dishes = app.my_dishes(user_id) if hasattr(app, "my_dishes") else {}
    ui = [{"event_id": e["event_id"], "ts": e["ts"], "time": _iso(e["ts"]), **e["payload"]}
          for e in events if e["type"] == "ui"]

    ins = app.insight(user_id) if prof else {}
    criteria = []
    for k in CRITERIA:
        c = next((x for x in ins.get("criteria") or [] if x["criterion"] == k), {})
        criteria.append({"criterion": k, "label": CRITERION_LABELS[k], "visible": k in acc["visible_criteria"],
                         "saved_weight": _r((goal or {}).get("weights", {}).get(k)) if k in VALUE_CRITERIA else None,
                         "observed_weight": _r(c.get("observed")), "gap": _r(c.get("gap")),
                         "mean_score_shown": _r(_mean([r["scores"].get(k) for q in recs for r in q["results"]])),
                         "mean_score_chosen": _r(_mean([i["scores"].get(k) for i in interactions
                                                        if i["type"] == "choose" and i["scores"]]))})

    log = ([{"ts": e["ts"], "kind": "event", "type": e["type"], "text": _event_text(app, e), "ref": e["event_id"]}
            for e in events] +
           [{"ts": q["ts"], "kind": "recommendation", "type": q["origin"],
             "text": "shown: " + ", ".join(f"#{r['rank']} {r['title']}" for r in q["results"]), "ref": q["request_id"]}
            for q in recs] +
           [{"ts": d["ts"], "kind": "decision", "type": d["kind"], "text": f"{d['title']} [{d['status']}]",
             "ref": d["decision_id"]} for d in decisions] +
           [{"ts": m["ts"], "kind": "message", "type": m["role"], "text": m["text"][:300], "ref": m["message_id"]}
            for m in msgs])
    log.sort(key=lambda x: x["ts"])
    for x in log:
        x["time"] = _iso(x["ts"])

    fb = Counter(i["type"] for i in interactions)
    resolved = [d for d in decisions if d["status"] != "open"]
    proposals = [d for d in decisions if d["kind"] in ("goal_proposal", "goal_options", "profile_change")]
    ts_all = [x["ts"] for x in log]
    chosen = [i for i in interactions if i["type"] == "choose"]
    summary = {
        "first_activity": _iso(min(ts_all)) if ts_all else None, "last_activity": _iso(max(ts_all)) if ts_all else None,
        "active_minutes": _r((max(ts_all) - min(ts_all)) / 60, 1) if ts_all else 0,
        "lists_shown": len(recs), "recipes_shown": sum(len(q["results"]) for q in recs),
        "feedback": dict(fb), "feedback_total": sum(fb.values()),
        "chat_turns": sum(1 for m in msgs if m["role"] == "user"),
        "decisions": dict(Counter(d["status"] for d in decisions)),
        "proposal_acceptance_rate": _r(sum(1 for d in proposals if d["status"] == "accepted") /
                                       max(1, sum(1 for d in proposals if d["status"] != "open")), 3)
        if proposals else None,
        "questions_answered": sum(1 for d in resolved if d["status"] == "answered"),
        "mean_rank_chosen": _r(_mean([i["rank_in_list"] for i in chosen]), 2),
        "mean_utility_chosen": _r(_mean([i["utility"] for i in chosen])),
        "profile_versions": (prof or {}).get("version", 0), "goal_versions": (goal or {}).get("version", 0),
        "preference_match": _r(ins.get("preference_match")), "model_confidence": _r(ins.get("confidence")),
        "page_views": dict(Counter(u.get("page") for u in ui if u.get("kind") == "page_view")),
        "my_dishes": {k: len(v) for k, v in dishes.items()},
    }
    out = {
        "user_id": user_id, "created": _iso(store.user_created(user_id)), "onboarded": prof is not None,
        "meal_cycle": {"steps": [{**s, "time": _iso(s["created_at"])} for s in store.cycle_steps(user_id)],
                       "reactions": store.cycle_answers(user_id)},
        "access": {k: acc[k] for k in ("role", "label", "source", "features", "visible_criteria", "blocked_skills")},
        "preferences": {
            "declared": (prof or {}).get("declared"), "profile_version": (prof or {}).get("version"),
            "goal": {k: (goal or {}).get(k) for k in ("version", "name", "weights", "diversity", "mode", "source")}
            if goal else None,
            "profile_history": [{**p, "time": _iso(p["created_at"])} for p in reversed(store.profile_history(user_id))],
            "goal_history": [{**g, "time": _iso(g["created_at"])} for g in reversed(store.goal_history(user_id))],
        },
        "criteria": criteria,
        "taste_beliefs": [{"concept": t["concept"], "mean": _r(t["mean"]), "evidence": _r(t["evidence"], 2),
                           "declared": t["declared"], "corrected": t["corrected"]} for t in ins.get("taste") or []],
        "my_dishes": {k: [{"recipe_id": d["recipe_id"], "title": d["title"], "time": _iso(d["ts"]),
                           "count": d["count"]} for d in v] for k, v in dishes.items()},
        "recommendations": recs, "interactions": interactions, "decisions": decisions,
        "conversation": msgs, "ui_events": ui, "log": log, "summary": summary,
    }
    if include_raw:
        out["events"] = [{**e, "time": _iso(e["ts"])} for e in events]
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def export(app, user_ids=None, include_raw=True, extra=None):
    """The whole export document for `user_ids` (default: every user in the database)."""
    ids = list(user_ids) if user_ids else app.store.users()
    llm = app.llm.status() if hasattr(app.llm, "status") else {}
    doc = {
        "format": FORMAT, "format_version": FORMAT_VERSION, "generated_at": _iso(time.time()),
        "app": {"version": __version__, "config_file": str(app.cfg.path) if app.cfg.path else None,
                "config_version": app.cfg.version,
                "catalogue": {"name": app.catalogue.name, "version": app.catalogue.version,
                              "recipes": len(app.catalogue)},
                "llm": {"model": getattr(app.llm, "model", None), "enabled": app.s.llm_enabled,
                        "available": llm.get("available")},
                "study": app.cfg.sanitized()["study"], "config": app.cfg.sanitized()},
        "criteria": criteria_catalog(), "features": feature_catalog(),
        "users": [export_user(app, u, include_raw) for u in ids],
    }
    if extra:
        doc.update(extra)
    return doc


# ---- flat CSV tables ----------------------------------------------------------------------------
def tables(doc):
    """{name: (header, rows)} flat tables of an export document."""
    feats = [f["id"] for f in doc["features"]]
    t = {k: [] for k in ("users", "recommendations", "interactions", "decisions", "messages", "ui_events", "log",
                         "criteria", "events")}
    for u in doc["users"]:
        uid, acc, s = u["user_id"], u["access"], u["summary"]
        g = (u["preferences"].get("goal") or {}).get("weights") or {}
        crit = {c["criterion"]: c for c in u["criteria"]}
        t["users"].append({
            "user_id": uid, "role": acc["role"], "role_source": acc["source"], "onboarded": u["onboarded"],
            "visible_criteria": " ".join(acc["visible_criteria"]),
            **{f"feature_{f}": acc["features"].get(f) for f in feats},
            **{f"saved_{k}": g.get(k) for k in VALUE_CRITERIA},
            **{f"observed_{k}": crit[k]["observed_weight"] for k in VALUE_CRITERIA},
            "health_goal": (u["preferences"].get("declared") or {}).get("health_goal"),
            "allergens": " ".join((u["preferences"].get("declared") or {}).get("allergens") or []),
            "diets": " ".join((u["preferences"].get("declared") or {}).get("diets") or []),
            **{k: s[k] for k in ("lists_shown", "feedback_total", "chat_turns", "questions_answered",
                                 "proposal_acceptance_rate", "mean_rank_chosen", "mean_utility_chosen",
                                 "preference_match", "model_confidence", "profile_versions", "goal_versions",
                                 "active_minutes", "first_activity", "last_activity")},
            **{f"n_{k}": s["feedback"].get(k, 0) for k in ("like", "save", "choose", "reject")}})
        for c in u["criteria"]:
            t["criteria"].append({"user_id": uid, **c})
        for q in u["recommendations"]:
            for r in q["results"]:
                t["recommendations"].append({
                    "user_id": uid, "request_id": q["request_id"], "time": q["time"], "origin": q["origin"],
                    "role": q["role"], "goal_version": q["goal_version"], "profile_version": q["profile_version"],
                    "diversity": q["diversity"], "mode": q["mode"],
                    "filters": "; ".join(map(str, q["request_filters"])),
                    **{f"weight_{k}": (q["weights_applied"] or {}).get(k) for k in CRITERIA},
                    "rank": r["rank"], "recipe_id": r["recipe_id"], "title": r["title"],
                    "utility": r["utility"], "utility_rank": r["utility_rank"], "utility_visible": r["utility_visible"],
                    **{f"score_{k}": r["scores"].get(k) for k in CRITERIA},
                    **{f"visible_{k}": k in (q["visible_criteria"] or []) for k in CRITERIA},
                    "promoted_for_diversity": r["promoted_for_diversity"]})
        for i in u["interactions"]:
            t["interactions"].append({
                "user_id": uid, **{k: i[k] for k in ("event_id", "time", "type", "recipe_id", "title", "reason", "scope",
                                                     "request_id", "rank_in_list", "role", "utility")},
                **{f"score_{k}": (i["scores"] or {}).get(k) for k in CRITERIA},
                "visible": " ".join((i["visible_scores"] or {}).keys()),
                "meal": i["context"].get("meal"), "place": i["context"].get("place")})
        for d in u["decisions"]:
            t["decisions"].append({"user_id": uid, **{k: d[k] for k in ("decision_id", "time", "kind", "title", "status",
                                                                         "source", "criterion", "resolved_time")},
                                   "action": (d["resolution"] or {}).get("action"),
                                   "answer": (d["resolution"] or {}).get("answer")})
        for m in u["conversation"]:
            t["messages"].append({"user_id": uid, "message_id": m["message_id"], "time": m["time"], "role": m["role"],
                                  "text": m["text"], "agent_steps": " > ".join(x["title"] for x in m["agent_steps"]),
                                  "attachments": " ".join(m["attachments"])})
        for e in u["ui_events"]:
            t["ui_events"].append({"user_id": uid, "event_id": e["event_id"], "time": e["time"], "kind": e.get("kind"),
                                   "page": e.get("page"), "recipe_id": e.get("recipe_id"), "role": e.get("role")})
        for x in u["log"]:
            t["log"].append({"user_id": uid, "time": x["time"], "kind": x["kind"], "type": x["type"],
                             "text": x["text"], "ref": x["ref"]})
        for e in u.get("events") or []:
            t["events"].append({"user_id": uid, "seq": e["seq"], "event_id": e["event_id"], "time": e["time"],
                                "type": e["type"], "payload": json.dumps(e["payload"], default=float),
                                "context": json.dumps(e["context"])})
    return t


def to_zip(doc):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("export.json", json.dumps(doc, indent=1, default=float, ensure_ascii=False))
        for name, rows in tables(doc).items():
            s = io.StringIO()
            if rows:
                w = csv.DictWriter(s, fieldnames=list(rows[0].keys()), extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            z.writestr(f"{name}.csv", s.getvalue())
    return buf.getvalue()


def to_json(doc):
    return json.dumps(doc, indent=1, default=float, ensure_ascii=False)
