"""Supervisor monitoring policy (deterministic, documented thresholds).

Implements the loop from the project idea (docs/supervisor.md):

    evidence -> preference model -> supervisor
        confident & consistent      -> recommend
        conflict detected           -> search history (knowledge graph)
            enough evidence         -> PROPOSE a goal update (user must accept)
            not enough evidence     -> ASK a targeted question
        counterfactual analysis     -> show what the proposal would change
        explain decision            -> every step goes to the trace

The policy never edits a saved goal or a mandatory constraint. Its outputs are decisions
(proposals and questions) that wait for the user.
"""
from .. import CRITERIA, CRITERION_LABELS

THRESHOLDS = {
    "conflict_prob": 0.70,       # P(|revealed - stated| > delta) needed to call a conflict
    "strong_prob": 0.90,         # ... needed to PROPOSE instead of ASK
    "min_evidence": 4,           # informative events since the last explicit statement
    "low_confidence": 0.25,      # revealed weights this uncertain are reported as "mostly stated"
    "taste_conflict_mean": 0.40, # declared liking whose belief fell below this ...
    "taste_min_evidence": 2.0,   # ... with at least this much observed evidence
    "cooldown_events": 6,        # after a dismissal, do not re-raise the same topic for N events
}
REASON_FOR = {"c": ("price", "too_expensive"), "t": ("time", "too_slow"), "h": ("health", "not_healthy_enough"),
              "s": ("climate", "not_sustainable"), "p": ("taste", "not_my_taste")}


def _pct(x):
    return f"{round(100 * x)}%"


def _open_topics(app, user_id):
    return {(d["body"].get("topic")) for d in app.store.decisions(user_id, status="open", limit=50)}


def _cooling_down(app, user_id, topic):
    """True if a question/proposal on this topic was answered, accepted or dismissed fewer
    than `cooldown_events` feedback events ago."""
    settled = [d for d in app.store.decisions(user_id, limit=200)
               if d["status"] != "open" and d["body"].get("topic") == topic and d["resolved_ts"]]
    if not settled:
        return False
    last = max(d["resolved_ts"] for d in settled)
    n_after = len(app.store.events(user_id, since=last, types=["feedback"]))
    return n_after < THRESHOLDS["cooldown_events"]


def _since_statement(evidence):
    out = []
    for row in reversed(evidence):
        if row["kind"] == "statement":
            break
        out.append(row)
    return out[::-1]


def review(app, user_id, trace, trigger="feedback"):
    """Run the monitoring loop once. Returns an assessment and any new decisions."""
    lr = app.learner(user_id)
    goal = app.store.goal(user_id)
    result = {"status": "insufficient_data", "conflicts": [], "decisions": [], "confidence": {}}
    if lr.choice is None or goal is None:
        trace.step("reason", "No preference model yet", "The user has not completed onboarding.")
        return result
    stated = goal["weights"]
    inf = lr.choice.inferred()
    disc = lr.choice.discrepancy(stated)
    recent = _since_statement(lr.choice.evidence)
    informative = [r for r in recent if r["kind"] in ("choice", "rejection", "tradeoff_answer") and not r.get("flag")]
    result["confidence"] = {k: inf[k]["confidence"] for k in CRITERIA}
    trace.step("perceive", "Compared stated goal with revealed behaviour",
               f"Saved goal v{goal['version']}; {lr.choice.n_choices} choices, {lr.choice.n_rejections} "
               f"criterion-specific rejections, {lr.choice.n_reasons} stated reasons so far; "
               f"{len(informative)} informative events since the last explicit goal statement.",
               evidence={"stated": {k: round(stated[k], 3) for k in CRITERIA},
                         "revealed_mean": {k: round(inf[k]["mean"], 3) for k in CRITERIA},
                         "confidence": {k: round(inf[k]["confidence"], 2) for k in CRITERIA},
                         "trigger": trigger}, source="learner")

    conflicts = sorted((k for k in CRITERIA if disc[k]["prob_beyond_delta"] >= THRESHOLDS["conflict_prob"]),
                       key=lambda k: -disc[k]["prob_beyond_delta"] * abs(disc[k]["mean_diff"]))
    result["conflicts"] = [{"criterion": k, "prob": disc[k]["prob_beyond_delta"], "direction": disc[k]["direction"],
                            "stated": stated[k], "revealed": inf[k]["mean"]} for k in conflicts]
    if not conflicts:
        low = [CRITERION_LABELS[k] for k in CRITERIA if inf[k]["confidence"] < THRESHOLDS["low_confidence"]]
        result["status"] = "aligned" if not low else "uncertain"
        trace.step("reason", "No conflict between goal and behaviour",
                   f"No criterion reaches the conflict threshold (P >= {_pct(THRESHOLDS['conflict_prob'])}). "
                   + (f"Revealed weights are still uncertain for {', '.join(low)}, so recommendations rest "
                      f"mainly on the saved goal." if low else "The saved goal explains the behaviour well."),
                   evidence={k: round(disc[k]["prob_beyond_delta"], 2) for k in CRITERIA})
    else:
        result["status"] = "conflict"
        k = conflicts[0]
        topic = f"criterion:{k}"
        d = disc[k]
        trace.step("reason", f"Conflict on {CRITERION_LABELS[k]}",
                   f"Saved weight {stated[k]:.2f}; behaviour suggests {inf[k]['mean']:.2f} "
                   f"(90% interval {inf[k]['lo']:.2f}-{inf[k]['hi']:.2f}). Probability the true weight differs "
                   f"by more than {app.learner(user_id).p['discrepancy_delta']:.2f}: {_pct(d['prob_beyond_delta'])}.",
                   evidence={"criterion": k, **{x: round(v, 3) if isinstance(v, float) else v for x, v in d.items()}})
        waiting = [d for d in app.store.decisions(user_id, status="open", limit=50)
                   if d["kind"] in ("goal_proposal", "tradeoff_question")]
        if waiting:
            trace.step("reason", "Already waiting for your answer",
                       f"\"{waiting[0]['title']}\" is still open. The supervisor raises one priority question "
                       f"at a time, so nothing new is asked.", evidence={"decision_id": waiting[0]["decision_id"]})
        elif _cooling_down(app, user_id, topic):
            trace.step("reason", "Topic recently dismissed",
                       f"The user dismissed this topic within the last {THRESHOLDS['cooldown_events']} events; "
                       "respecting that and not asking again yet.")
        else:
            _resolve_conflict(app, user_id, trace, k, stated, inf, disc, informative, result)

    _taste_conflicts(app, user_id, trace, lr, result)
    return result


def _history_evidence(app, user_id, trace, k):
    choose_reason, reject_reason = REASON_FOR[k]
    s = trace.start("tool", "Searching interaction history in the knowledge graph",
                    "graph.timeline(user, types=[choose, reject, like], limit=40)", source="graph")
    rows = app.graph.timeline(user_id, types=["choose", "reject", "like", "save"], limit=40)
    support = [r for r in rows if r.get("reason") in (choose_reason, reject_reason)]
    trace.finish(s, f"{len(rows)} recent interactions; {len(support)} name {CRITERION_LABELS[k].lower()} "
                    f"explicitly as the reason.",
                 evidence=[{"event_id": r["event_id"], "type": r["type"], "reason": r.get("reason"),
                            "recipe": r.get("title")} for r in support[:8]])
    return rows, support


def _resolve_conflict(app, user_id, trace, k, stated, inf, disc, informative, result):
    rows, support = _history_evidence(app, user_id, trace, k)
    n_inf = len(informative)
    strong = disc[k]["prob_beyond_delta"] >= THRESHOLDS["strong_prob"] and n_inf >= THRESHOLDS["min_evidence"]
    trace.step("decide", "Enough evidence to propose a change?" + (" Yes" if strong else " No"),
               f"Rule: propose only if P >= {_pct(THRESHOLDS['strong_prob'])} AND at least "
               f"{THRESHOLDS['min_evidence']} informative events since the last statement. "
               f"Here P = {_pct(disc[k]['prob_beyond_delta'])}, events = {n_inf}. "
               + ("Preparing a proposal; the saved goal stays unchanged until you accept it."
                  if strong else "Asking a targeted question instead of guessing."),
               source="policy", data={"options": ["propose", "ask"], "chosen": "propose" if strong else "ask"})
    evidence_events = [r["event_id"] for r in informative if r.get("event_id")] + [r["event_id"] for r in support]
    evidence_events = list(dict.fromkeys(evidence_events))
    if strong:
        proposed = dict(stated)
        proposed[k] = (stated[k] + inf[k]["mean"]) / 2.0
        tot = sum(proposed.values())
        proposed = {x: v / tot for x, v in proposed.items()}
        s = trace.start("tool", "Counterfactual: what would the proposal change?",
                        "engine.what_if(weights=proposed) vs the current goal, nothing saved", source="engine")
        wi = app.what_if(user_id, weights=proposed)
        cmp_ = wi["comparison"] or {"entered": [], "left": [], "moved": []}
        titles = {r["recipe_id"]: r["title"] for r in (wi["alternative"].get("results") or []) +
                  (wi["base"].get("results") or [])}
        cf = {"entered": [titles.get(x, x) for x in cmp_["entered"]], "left": [titles.get(x, x) for x in cmp_["left"]],
              "moved": len(cmp_.get("moved", []))}
        trace.finish(s, f"{len(cf['entered'])} recipe(s) would enter the top five, {len(cf['left'])} would leave.",
                     evidence=cf)
        direction = "more" if inf[k]["mean"] > stated[k] else "less"
        d = app.add_decision(user_id, "goal_proposal",
                             f"Give {CRITERION_LABELS[k].lower()} {direction} weight?", {
                                 "topic": f"criterion:{k}", "criterion": k,
                                 "explanation": (f"Your saved goal gives {CRITERION_LABELS[k].lower()} "
                                                 f"{stated[k]:.2f}, but your choices behave like "
                                                 f"{inf[k]['mean']:.2f} ({_pct(disc[k]['prob_beyond_delta'])} "
                                                 f"likely to differ, {n_inf} supporting events). Accepting "
                                                 f"moves it halfway, to {proposed[k]:.2f}."),
                                 "stated": stated, "revealed": {x: inf[x]["mean"] for x in CRITERIA},
                                 "proposed_weights": proposed, "probability": disc[k]["prob_beyond_delta"],
                                 "evidence_events": evidence_events, "counterfactual": cf,
                                 "recipes": (cmp_["entered"] + cmp_["left"])[:6]}, "policy")
    else:
        others = [x for x in CRITERIA if x != k]
        opposite = [x for x in others if disc[x]["direction"] != disc[k]["direction"]]
        j = max(opposite or others, key=lambda x: (abs(disc[x]["mean_diff"]), stated[x]))
        hi, lo = (k, j) if disc[k]["direction"] == "higher" else (j, k)
        q = (f"When {CRITERION_LABELS[hi].lower()} and {CRITERION_LABELS[lo].lower()} pull in different "
             f"directions, which usually wins for you?")
        d = app.add_decision(user_id, "tradeoff_question", q, {
            "topic": f"criterion:{k}", "criterion": k,
            "explanation": (f"Your recent choices lean {disc[k]['direction']} on "
                            f"{CRITERION_LABELS[k].lower()} than your saved goal, but there is not yet enough "
                            f"evidence to suggest a change. Your answer is recorded as evidence; it does not "
                            f"edit your goal."),
            "options": [{"id": hi, "label": CRITERION_LABELS[hi], "winner": hi, "loser": lo},
                        {"id": lo, "label": CRITERION_LABELS[lo], "winner": lo, "loser": hi},
                        {"id": "depends", "label": "It depends on the meal"}],
            "evidence_events": evidence_events, "recipes": [r["recipe_id"] for r in support if r.get("recipe_id")][:6]},
            "policy")
    trace.step("decide", "Raised: " + d["title"], d["body"]["explanation"],
               evidence={"decision_id": d["decision_id"], "evidence_events": len(evidence_events)},
               data={"decision": d})
    result["decisions"].append(d)


def _taste_conflicts(app, user_id, trace, lr, result):
    open_topics = _open_topics(app, user_id)
    for c in sorted(lr.taste.a):
        m, n = lr.taste.mean(c), lr.taste.evidence(c)
        kind, _, name = c.partition(":")
        if kind not in ("cuisine", "ingredient"):
            continue
        declared = any(p["kind"] == "declared" for p in lr.taste.prov.get(c, []))
        if not declared or m >= THRESHOLDS["taste_conflict_mean"] or n < THRESHOLDS["taste_min_evidence"]:
            continue
        topic = f"concept:{c}"
        if topic in open_topics or _cooling_down(app, user_id, topic):
            continue
        rows = app.graph.concept_evidence(user_id, c, limit=20)
        rejects = [r for r in rows if r.get("type") == "reject"]
        trace.step("reason", f"Declared taste contradicted: {name}",
                   f"You declared {name} as a liking, but the belief has fallen to {m:.2f} after "
                   f"{len(rejects)} rejection(s) of dishes with it.",
                   evidence=[{"event_id": r["event_id"], "recipe": r.get("title")} for r in rejects[:6]],
                   source="graph")
        op = "remove_cuisine" if kind == "cuisine" else "remove_liked_ingredient"
        d = app.add_decision(user_id, "taste_question", f"Do you still want {name} as a preference?", {
            "topic": topic, "concept": c,
            "explanation": (f"You listed {name} in your profile, but recently rejected {len(rejects)} dish(es) "
                            f"containing it. Keeping it is fine; the system only asks."),
            "options": [{"id": "keep", "label": "Keep it"},
                        {"id": "remove", "label": "Remove from my profile",
                         "profile_change": {"op": op, "value": name}}],
            "evidence_events": [r["event_id"] for r in rejects], "recipes": [r["recipe_id"] for r in rejects][:6]},
            "policy")
        result["decisions"].append(d)
        trace.step("decide", "Raised: " + d["title"], d["body"]["explanation"], data={"decision": d})
        break
