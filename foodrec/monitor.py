"""What the administrator's Monitor page shows: the populations, the people in them, and each
person's interactions and logs.

The same shapes come from two sources: the database (`overview`, `user_detail`), and on a demo
server also made-up sample populations (`_sample`), because a public demo has no real study to
look at. Sample records are never stored, are marked `sample`, and carry no real person's data.
Passwords and personal links are never part of these records.
"""
import random
from collections import Counter

from . import CRITERIA, CRITERION_LABELS, VALUE_CRITERIA, clock
from .export import _iso, export_user
from .study import population_name

OTHER_USERS = "Other users"
YOUR_LINKS = "Your links"
REACTIONS = ("like", "save", "choose", "reject")


def _role_label(app, role):
    return (app.cfg.roles.get(role) or {}).get("label", role)


def _state(row):
    """invited (has not come yet), started, in progress, finished, or deactivated."""
    if row["status"] != "active":
        return "deactivated"
    if not row["joined"]:
        return "invited"
    if row["cycle"] and row["cycle"]["finished"]:
        return "finished"
    if not row["onboarded"] or not (row["reactions_total"] or row["chat_turns"]):
        return "started"
    return "in_progress"


def _summary(name, description, sample, rows):
    """The numbers of one population, for the comparison table."""
    started = [r for r in rows if r["joined"]]
    last = [r["last_active"] for r in rows if r["last_active"]]
    total = sum(r["reactions_total"] for r in rows)
    kinds = Counter()
    for r in rows:
        kinds.update(r["reactions"])
    return {"name": name, "description": description, "sample": sample, "size": len(rows),
            "started": len(started), "onboarded": sum(1 for r in rows if r["onboarded"]),
            "finished": sum(1 for r in rows if r["state"] == "finished"),
            "deactivated": sum(1 for r in rows if r["state"] == "deactivated"),
            "reactions_total": total, "reactions": dict(kinds),
            "reactions_per_person": round(total / len(started), 1) if started else 0,
            "chat_turns": sum(r["chat_turns"] for r in rows),
            "decisions_open": sum(r["decisions_open"] for r in rows),
            "roles": dict(Counter(r["role_label"] for r in rows)),
            "last_active": max(last) if last else None, "people": rows}


# ---- real people ----------------------------------------------------------------------------------
def _people(app, owner):
    """Who is monitored: {user_id: person}. In the demo: the links this visitor created. Otherwise:
    the participants, and anyone else who has used the app, except administrators."""
    if owner is not None:
        return {x["user_id"]: {"label": x["label"], "population": YOUR_LINKS, "status": x["status"]}
                for x in app.store.demo_links(owner)}
    out = {p["username"]: {"label": p["username"], "population": population_name(p),
                           "status": "inactive" if p.get("active") is False else "active"}
           for p in app.cfg.study.get("participants") or []}
    for uid in app.store.users():
        if uid not in out and not app.access(uid)["features"]["configure"]:
            out[uid] = {"label": uid, "population": OTHER_USERS, "status": "active"}
    return out


def _cycle(app, uid):
    cfg = app.cycle_config(uid)
    if not cfg["enabled"]:
        return None
    steps = app.store.cycle_steps(uid)
    round_ = steps[-1]["round"] if steps else 1
    steps = [s for s in steps if s["round"] == round_]
    return {"answered": app._cycle_answered(uid, steps), "total": cfg["total"], "round": round_,
            "finished": bool(steps and steps[-1]["done_at"] is not None)}


def _row(app, uid, person, activity, users):
    acc = app.access(uid)
    act = activity.get(uid) or {}
    known = users.get(uid) or {}
    reactions = act.get("reactions") or {}
    last = [t for t in (known.get("last_active"), act.get("last")) if t]
    row = {"user_id": uid, "label": person["label"], "population": person["population"], "sample": False,
           "role": acc["role"], "role_label": acc.get("label") or _role_label(app, acc["role"]),
           "status": person["status"], "joined": uid in users, "onboarded": bool(known.get("onboarded")),
           "reactions": reactions, "reactions_total": sum(reactions.values()),
           "chat_turns": act.get("chat_turns", 0), "lists": act.get("lists", 0),
           "decisions_open": act.get("decisions_open", 0), "cycle": _cycle(app, uid),
           "last_active": max(last) if last else None}
    return {**row, "state": _state(row)}


def _real_rows(app, owner):
    people = _people(app, owner)
    activity = app.store.activity_counts()
    users = {u["user_id"]: u for u in app.store.user_rows()}
    return people, [_row(app, uid, p, activity, users) for uid, p in people.items()]


def overview(app, owner=None):
    """Every population with its people. `owner`: the demo visitor who is looking (None elsewhere)."""
    demo = owner is not None
    pops = list(_sample(app)["populations"]) if demo else []
    _, rows = _real_rows(app, owner)
    groups = {}
    for r in rows:
        groups.setdefault(r["population"], []).append(r)
    ordered = sorted(groups, key=lambda n: (n == OTHER_USERS, n.lower()))
    real = [_summary(n, "People who came through the links you created." if n == YOUR_LINKS else
                     ("People who are not in a population." if n == OTHER_USERS else ""), False, groups[n])
            for n in ordered]
    return {"has_sample": demo, "populations": real + pops}


def user_detail(app, uid, owner=None):
    """One person: their row, plus every interaction and the full log, newest first."""
    if owner is not None and uid in _sample(app)["details"]:
        return _sample(app)["details"][uid]
    people, rows = _real_rows(app, owner)
    if uid not in people:
        return None
    row = next(r for r in rows if r["user_id"] == uid)
    if not app.store.user_created(uid):   # invited, has not come yet: nothing to read
        return {**row, "goal": None, "summary": {}, "criteria": [], "interactions": [], "log": [],
                "conversation": [], "decisions": []}
    doc = export_user(app, uid, include_raw=False)
    goal = (doc["preferences"] or {}).get("goal")
    return {**row, "goal": {k: goal.get(k) for k in ("name", "version", "weights", "diversity")} if goal else None,
            "summary": {k: doc["summary"].get(k) for k in ("lists_shown", "chat_turns", "active_minutes",
                                                           "first_activity", "mean_rank_chosen")},
            "criteria": [{k: c.get(k) for k in ("criterion", "label", "saved_weight", "observed_weight", "gap")}
                         for c in doc["criteria"]],
            "interactions": [{k: i.get(k) for k in ("ts", "type", "title", "reason", "rank_in_list", "scope")}
                             for i in reversed(doc["interactions"])],
            "log": [{k: x.get(k) for k in ("ts", "kind", "type", "text")} for x in reversed(doc["log"])],
            "conversation": [{k: m.get(k) for k in ("ts", "role", "text")} for m in doc["conversation"]],
            "decisions": [{k: d.get(k) for k in ("ts", "kind", "title", "status", "explanation")}
                          for d in reversed(doc["decisions"])]}


# ---- sample data (demo only) -----------------------------------------------------------------------
SAMPLE_POPULATIONS = (
    {"name": "Pilot A: full transparency", "prefix": "a", "size": 8, "roles": ["full"],
     "mix": {"like": .45, "save": .2, "choose": .2, "reject": .15},
     "description": "Sample data. Everyone sees scores, explanations and the assistant."},
    {"name": "Pilot B: baseline list", "prefix": "b", "size": 8, "roles": ["baseline"],
     "mix": {"like": .3, "save": .1, "choose": .15, "reject": .45},
     "description": "Sample data. A plain list of dishes, with no assistant, scores or explanations."},
    {"name": "Main study: mixed roles", "prefix": "m", "size": 12, "roles": ["full", "baseline"],
     "mix": {"like": .38, "save": .15, "choose": .17, "reject": .3},
     "description": "Sample data. Half the people have each role, in random order."},
)
# how far each person got, in order: the first person always has a complete record to look at
SAMPLE_STAGES = ("finished", "finished", "in_progress", "finished", "started", "in_progress", "invited", "deactivated")
SAMPLE_MEALS = {"breakfast": 8, "lunch": 12.5, "dinner": 18.5}
SAMPLE_REASONS = {"reject": ("too_long", "dont_like_ingredient", "not_healthy", None),
                  "choose": ("looks_tasty", "quick", "healthy", None)}
SAMPLE_CHAT = (
    ("Why is the first dish ranked above the second one?",
     "Both fit your rules. The first scores higher on healthiness, which you weigh most, and has a similar taste match."),
    ("Something easy with lentils for dinner?",
     "Here are five lentil dishes with few ingredients, ranked by your values."),
    ("Compare #1 and #3.",
     "#1 is healthier, #3 has a smaller carbon footprint. The difference in similarity score comes mostly from healthiness."),
)
SAMPLE_DECISIONS = (
    ("goal_proposal", "Give sustainability more weight in your goal?", "accepted",
     "You chose lower-footprint dishes more often than your saved goal says."),
    ("tradeoff_question", "Is convenience more important to you than you said?", "answered",
     "Dishes with few ingredients were picked in 6 of the last 8 reactions."),
    ("taste_question", "Do you still want mushrooms as a preference?", "open",
     "You rejected three dishes with mushrooms, although mushrooms are in your preferences."),
)


def _titles(app):
    """Real dish titles from the catalogue, so a sample record reads like a real one."""
    from .service import MEAL_CONTEXTS
    out = {}
    for meal in SAMPLE_MEALS:
        kinds = MEAL_CONTEXTS[meal]
        out[meal] = [r["title"] for r in app.catalogue.recipes[:3000] if r.get("meal") in kinds][:80] or \
            [r["title"] for r in app.catalogue.recipes[:80]] or [f"Sample {meal} dish {i}" for i in range(1, 21)]
    return out


def _sample_person(app, pop, index, stage, role, titles, now):
    rng = random.Random(f"{pop['prefix']}{index}")
    label = f"{pop['prefix']}{index:02d}"
    uid = f"sample-{label}"
    assistant = role == "full"
    row = {"user_id": uid, "label": label, "population": pop["name"], "sample": True, "role": role,
           "role_label": _role_label(app, role), "status": "inactive" if stage == "deactivated" else "active",
           "joined": stage != "invited", "onboarded": stage != "invited"}
    interactions, log, conversation, decisions = [], [], [], []
    meals_done = {"finished": 9, "in_progress": rng.randint(2, 6), "deactivated": rng.randint(1, 3)}.get(stage, 0)
    start = now - 86400 * 4 - rng.randint(0, 40000)
    if stage == "invited":
        start = None
    else:
        log += [{"ts": start, "kind": "event", "type": "profile_created", "text": "profile_created -> profile v1"},
                {"ts": start + 40, "kind": "event", "type": "goal_saved", "text": "goal_saved -> goal v1"},
                {"ts": start + 60, "kind": "event", "type": "ui", "text": "ui session_start"}]
    meal_names = list(SAMPLE_MEALS)
    for step in range(meals_done):
        day, meal = step // 3, meal_names[step % 3]
        ts = start + 3600 + day * 86400 * .9 + (step % 3) * 5400 + rng.randint(0, 1200)
        shown = rng.sample(titles[meal], min(5, len(titles[meal])))
        log.append({"ts": ts, "kind": "recommendation", "type": "cycle",
                    "text": "shown: " + ", ".join(f"#{i} {t}" for i, t in enumerate(shown, 1))})
        for rank, title in enumerate(shown, 1):
            ts += rng.randint(15, 140)
            kind = rng.choices(list(pop["mix"]), list(pop["mix"].values()))[0]
            reason = rng.choice(SAMPLE_REASONS[kind]) if kind in SAMPLE_REASONS else None
            interactions.append({"ts": ts, "type": kind, "title": title, "reason": reason, "rank_in_list": rank,
                                 "scope": "lasting"})
            log.append({"ts": ts, "kind": "event", "type": "feedback",
                        "text": f"{kind} {title}" + (f" ({reason})" if reason else "")})
        if assistant and step % 3 == 1 and rng.random() < .75:
            q, a = rng.choice(SAMPLE_CHAT)
            conversation += [{"ts": ts + 30, "role": "user", "text": q}, {"ts": ts + 36, "role": "assistant", "text": a}]
            log += [{"ts": ts + 30, "kind": "message", "type": "user", "text": q},
                    {"ts": ts + 36, "kind": "message", "type": "assistant", "text": a}]
        if assistant and step in (3, 7):
            kind, title, status, why = SAMPLE_DECISIONS[(index + step) % len(SAMPLE_DECISIONS)]
            decisions.append({"ts": ts + 60, "kind": kind, "title": title, "status": status, "explanation": why})
            log.append({"ts": ts + 60, "kind": "decision", "type": kind, "text": f"{title} [{status}]"})
    log.sort(key=lambda x: x["ts"])
    counts = Counter(i["type"] for i in interactions)
    weights = {k: round(rng.uniform(.2, .45), 2) for k in VALUE_CRITERIA}
    scale = sum(weights.values()) or 1
    weights = {k: round(v / scale, 2) for k, v in weights.items()}
    row.update(reactions=dict(counts), reactions_total=len(interactions), lists=meals_done,
               chat_turns=sum(1 for m in conversation if m["role"] == "user"),
               decisions_open=sum(1 for d in decisions if d["status"] == "open"),
               cycle={"answered": len(interactions), "total": 45, "round": 1, "finished": stage == "finished"},
               last_active=log[-1]["ts"] if log else None)
    row["state"] = _state(row)
    detail = {**row, "goal": {"name": "My priorities", "version": 1, "weights": weights, "diversity": .3} if start else None,
              "summary": {"lists_shown": meals_done, "chat_turns": row["chat_turns"],
                          "active_minutes": round((log[-1]["ts"] - log[0]["ts"]) / 60, 1) if len(log) > 1 else 0,
                          "first_activity": _iso(start), "mean_rank_chosen": None},
              "criteria": [{"criterion": k, "label": CRITERION_LABELS[k], "saved_weight": weights.get(k),
                            "observed_weight": round(max(0, weights[k] + rng.uniform(-.12, .12)), 2) if interactions and k in weights else None,
                            "gap": None} for k in CRITERIA] if start else [],
              "interactions": interactions[::-1], "log": log[::-1], "conversation": conversation,
              "decisions": decisions[::-1]}
    for c in detail["criteria"]:
        if c["saved_weight"] is not None and c["observed_weight"] is not None:
            c["gap"] = round(c["observed_weight"] - c["saved_weight"], 2)
    return row, detail


def _sample(app):
    """Made-up populations for the demo: the same every time, and shifted to stay recent. Built once an hour."""
    hour = int(clock.now() // 3600)
    cached = getattr(app, "_monitor_sample", None)
    if cached and cached[0] == hour:
        return cached[1]
    now, titles = hour * 3600.0, _titles(app)
    populations, details = [], {}
    for pop in SAMPLE_POPULATIONS:
        roles = [r for r in pop["roles"] if r in app.cfg.roles] or [app.cfg.data["access"].get("default_role")]
        rows = []
        for i in range(1, pop["size"] + 1):
            stage = SAMPLE_STAGES[(i - 1) % len(SAMPLE_STAGES)]
            row, detail = _sample_person(app, pop, i, stage, roles[(i - 1) % len(roles)], titles, now)
            rows.append(row)
            details[row["user_id"]] = detail
        populations.append(_summary(pop["name"], pop["description"], True, rows))
    data = {"populations": populations, "details": details}
    app._monitor_sample = (hour, data)
    return data
