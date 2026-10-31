"""Supervisor monitoring policy (deterministic, documented thresholds).

After every piece of feedback the supervisor compares the user's STATED weights (saved
goal) with the OBSERVED weights from the value model (learner.py):

    gap_k = observed_k - stated_k
    small gaps                                  -> keep recommending, nothing to ask
    gap >= ASK_GAP with >= ASK_MIN events       -> ASK a trade-off question
    gap >= PROPOSE_GAP with >= PROPOSE_MIN      -> PROPOSE moving the weight halfway
                                                   (shows a what-if first; the user must accept)

The policy never edits a saved goal or a mandatory constraint. Its outputs are decisions
(proposals and questions) that wait for the user. Every question and proposal lets the user
say how far the answer should reach:
    "update my preference"   lasting: recorded as evidence (question) or saved (proposal)
    "only today"             today's lists are adjusted; the day's drift is rolled back
    "keep my goal"           the drift since the goal was saved is forgotten
"""
from .. import INTERACTIONS, VALUE_CRITERIA, CRITERION_LABELS

THRESHOLDS = {
    "ask_gap": 0.10,             # |observed - stated| that is worth a question ...
    "ask_min_events": 3,         # ... after at least this many informative events
    "propose_gap": 0.15,         # |observed - stated| that is worth a proposal ...
    "propose_min_events": 6,     # ... after at least this many informative events
    "taste_conflict_mean": 0.40, # declared liking whose taste fell below this ...
    "taste_min_evidence": 2.0,   # ... with at least this much observed evidence
    "cooldown_events": 6,        # after a dismissal, do not re-raise the same topic for N events
}
# criterion -> all choose / reject reason ids about it (from the configured interactions)
REASON_FOR = {k: (tuple(r["id"] for r in INTERACTIONS["choose_reasons"] if r["criterion"] == k),
                  tuple(r["id"] for r in INTERACTIONS["reject_reasons"] if r["criterion"] == k))
              for k in VALUE_CRITERIA}


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


def review(app, user_id, trace, trigger="feedback"):
    """Run the monitoring loop once. Returns an assessment and any new decisions."""
    lr = app.learner(user_id)
    goal = app.store.goal(user_id)
    result = {"status": "insufficient_data", "conflicts": [], "decisions": [], "confidence": None}
    if lr.values is None or goal is None:
        trace.step("reason", "No user model yet", "The user has not completed onboarding.")
        return result
    vm = lr.values
    stated = {k: goal["weights"][k] for k in VALUE_CRITERIA}
    gap = vm.gap(stated)
    # After "Keep my goal" the gap the user accepted is not raised again: decisions use the drift
    # beyond it, counted in events since then. Without one, drift == gap and n_new == vm.n.
    drift, n_new = vm.drift(stated), vm.events_since_ack()
    match = vm.match(stated)
    result["confidence"] = vm.confidence()
    result["preference_match"] = match
    trace.step("perceive", "Compared your goal with your behaviour",
               f"Saved goal v{goal['version']}; {vm.n} informative event(s) since it was saved "
               f"({vm.counts['choices']} choices, {vm.counts['rejections']} reasoned rejections, "
               f"{vm.counts['reasons']} reasons, {vm.counts['answers']} answers in total). "
               f"Preference match {_pct(match)}.",
               evidence={"stated": {k: round(stated[k], 3) for k in VALUE_CRITERIA},
                         "observed": {k: round(vm.observed[k], 3) for k in VALUE_CRITERIA},
                         "gap": {k: round(gap[k], 3) for k in VALUE_CRITERIA},
                         "preference_match": round(match, 3), "confidence": round(vm.confidence(), 2),
                         "acknowledged_gap": {k: round(v, 3) for k, v in vm.ack["gap"].items()} if vm.ack else None,
                         "trigger": trigger}, source="learner")
    if vm.ack:
        trace.step("reason", "You chose to keep your goal",
                   f"The gap you saw then is accepted and not raised again; only drift beyond it counts "
                   f"({n_new} event(s) since).", evidence={x: round(drift[x], 3) for x in VALUE_CRITERIA})

    k = max(VALUE_CRITERIA, key=lambda x: abs(drift[x]))
    big = abs(drift[k]) >= THRESHOLDS["ask_gap"] and n_new >= THRESHOLDS["ask_min_events"]
    if not big:
        result["status"] = "aligned" if n_new >= THRESHOLDS["ask_min_events"] else "uncertain"
        trace.step("reason", "Goal and behaviour agree" if result["status"] == "aligned" else "Still learning",
                   f"Largest {'new drift' if vm.ack else 'gap'} is {CRITERION_LABELS[k]} ({drift[k]:+.2f}); "
                   f"the supervisor acts only when it reaches {THRESHOLDS['ask_gap']:.2f} after "
                   f"{THRESHOLDS['ask_min_events']} or more events.",
                   evidence={x: round(drift[x], 3) for x in VALUE_CRITERIA})
    else:
        result["status"] = "conflict"
        result["conflicts"] = [{"criterion": x, "gap": gap[x], "stated": stated[x], "observed": vm.observed[x],
                                "direction": "higher" if gap[x] > 0 else "lower"}
                               for x in sorted(VALUE_CRITERIA, key=lambda x: -abs(drift[x]))
                               if abs(drift[x]) >= THRESHOLDS["ask_gap"]]
        topic = f"criterion:{k}"
        trace.step("reason", f"Gap on {CRITERION_LABELS[k]}",
                   f"Saved weight {stated[k]:.2f}; your choices behave like {vm.observed[k]:.2f} "
                   f"(gap {gap[k]:+.2f}, {vm.n} events).", evidence={"criterion": k, "gap": round(gap[k], 3)})
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
            _resolve_conflict(app, user_id, trace, k, stated, vm, drift, result, n_new)

    _taste_conflicts(app, user_id, trace, lr, result)
    return result


def _history_evidence(app, user_id, trace, k):
    choose_reasons, reject_reasons = REASON_FOR[k]
    s = trace.start("tool", "Searching interaction history in the knowledge graph",
                    "graph.timeline(user, types=[choose, reject, like], limit=40)", source="graph")
    rows = app.graph.timeline(user_id, types=["choose", "reject", "like", "save"], limit=40)
    support = [r for r in rows if r.get("reason") in choose_reasons + reject_reasons]
    trace.finish(s, f"{len(rows)} recent interactions; {len(support)} name {CRITERION_LABELS[k].lower()} "
                    f"explicitly as the reason.",
                 evidence=[{"event_id": r["event_id"], "type": r["type"], "reason": r.get("reason"),
                            "recipe": r.get("title")} for r in support[:8]])
    return rows, support


def _resolve_conflict(app, user_id, trace, k, stated, vm, gap, result, n_events=None):
    """`gap`: the drift to act on (beyond an acknowledged gap); `n_events`: events behind it."""
    n_events = vm.n if n_events is None else n_events
    rows, support = _history_evidence(app, user_id, trace, k)
    strong = abs(gap[k]) >= THRESHOLDS["propose_gap"] and n_events >= THRESHOLDS["propose_min_events"]
    trace.step("decide", "Enough evidence to propose a change?" + (" Yes" if strong else " No"),
               f"Rule: propose only if the gap is at least {THRESHOLDS['propose_gap']:.2f} after "
               f"{THRESHOLDS['propose_min_events']} events. Here gap = {abs(gap[k]):.2f}, events = {n_events}. "
               + ("Preparing a proposal; the saved goal stays unchanged until you accept it."
                  if strong else "Asking a targeted question instead of guessing."),
               source="policy", data={"options": ["propose", "ask"], "chosen": "propose" if strong else "ask"})
    evidence_events = [r["event_id"] for r in vm.evidence if r.get("event_id") and not r.get("flag")
                       and r["kind"] != "goal_saved"][-12:] + [r["event_id"] for r in support]
    evidence_events = list(dict.fromkeys(evidence_events))
    if strong:
        proposed = dict(stated)
        proposed[k] = (stated[k] + vm.observed[k]) / 2.0
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
        direction = "more" if gap[k] > 0 else "less"
        d = app.add_decision(user_id, "goal_proposal",
                             f"Give {CRITERION_LABELS[k].lower()} {direction} weight?", {
                                 "topic": f"criterion:{k}", "criterion": k,
                                 "explanation": (f"Your saved goal gives {CRITERION_LABELS[k].lower()} "
                                                 f"{stated[k]:.2f}, but your choices behave like "
                                                 f"{vm.observed[k]:.2f} ({vm.n} events). Accepting "
                                                 f"moves it halfway, to {proposed[k]:.2f}. \"Only today\" uses "
                                                 f"it for today's lists without saving or learning; \"Keep my "
                                                 f"goal\" keeps your goal and what was learned; I only ask "
                                                 f"again if your choices drift further."),
                                 "stated": stated, "observed": dict(vm.observed),
                                 "proposed_weights": proposed, "gap": gap[k],
                                 "evidence_events": evidence_events, "counterfactual": cf,
                                 "recipes": (cmp_["entered"] + cmp_["left"])[:6]}, "policy")
    else:
        others = [x for x in VALUE_CRITERIA if x != k]
        opposite = [x for x in others if (gap[x] > 0) != (gap[k] > 0)]
        j = max(opposite or others, key=lambda x: (abs(gap[x]), stated[x]))
        hi, lo = (k, j) if gap[k] > 0 else (j, k)
        q = (f"When {CRITERION_LABELS[hi].lower()} and {CRITERION_LABELS[lo].lower()} pull in different "
             f"directions, which usually wins for you?")
        d = app.add_decision(user_id, "tradeoff_question", q, {
            "topic": f"criterion:{k}", "criterion": k,
            "explanation": (f"Your recent choices lean {'more' if gap[k] > 0 else 'less'} towards "
                            f"{CRITERION_LABELS[k].lower()} than your saved goal, but there is not yet enough "
                            f"evidence to suggest a change. Naming a winner updates what I have learned about "
                            f"you; \"Only today\" adjusts today's lists without learning anything; \"Keep my "
                            f"goal\" keeps your goal and what was learned, and I only ask again if your choices "
                            f"drift further. None of them edits your saved goal."),
            "options": [{"id": hi, "label": f"{CRITERION_LABELS[hi]} usually wins", "winner": hi, "loser": lo,
                         "scope": "lasting"},
                        {"id": lo, "label": f"{CRITERION_LABELS[lo]} usually wins", "winner": lo, "loser": hi,
                         "scope": "lasting"},
                        {"id": "today", "label": "Only today", "scope": "today",
                         "nudge": {k: 0.10 if gap[k] > 0 else -0.10}},
                        {"id": "keep_goal", "label": "Keep my goal", "scope": "keep_goal"}],
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
        declared = lr.taste.l0.get(c, 0) > 0
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
