"""User model: two small models, both pure functions of the event log (replayable).

TasteModel   For every food concept (cuisine, ingredient, food group) count how often the
             user liked or disliked dishes with it:
                 taste(concept) = (1 + likes) / (2 + likes + dislikes)
             0.5 = no opinion yet. A like counts 1 for every concept of the dish (a choice 0.8,
             a save 0.6). Onboarding likes/dislikes start a concept at 2 likes or 2 dislikes.
             A recipe's taste match P = weighted mean of its concepts' values (catalogue.concepts:
             cuisine 35%, ingredients 45% by mass, food groups 20%).

ValueModel   How much the user's behaviour actually weighs healthiness, sustainability and
             convenience ("observed weights" o, summing to 1), next to what they stated (w).
                 start                 o = w  (stated weights from onboarding)
                 chose a dish          o_k <- o_k * exp(ETA * d_k),  d_k = score_k(chosen) - mean_k(shown)
                   + gave a reason     ("health", "climate", "easy")  the named o_k also * exp(ETA * NUDGE)
                 reasoned rejection    ("not healthy enough", ...)    o_k <- o_k * exp(ETA * d_k),
                                       d_k = mean_k(shown) - score_k(rejected)
                 answered a question   winner * exp(ETA * NUDGE), loser * exp(-ETA * NUDGE)
                 saved a new goal      o <- (o + w_new) / 2
             then renormalise, and keep a little of the goal: o <- (1 - ANCHOR) * o + ANCHOR * w,
             so a run of unusual choices moves the estimate but cannot wipe a priority out. Read-outs:
                 gap_k            = o_k - w_k
                 preference match = 1 - 1/2 * sum_k |w_k - o_k|        (1 = behaviour matches goal)
                 confidence       = n / (n + 5), n = informative events since the last goal save
             The observed weights NEVER change the saved goal. The supervisor may propose a
             change (policy.py) that the user has to accept.

All constants are design choices, documented in docs/user-model.md.
"""
import math

from . import VALUE_CRITERIA

PARAMS = {
    "eta": 1.0,          # learning rate of the value model
    "nudge": 0.2,        # size of an explicit signal (reason chip, answered question)
    "anchor": 0.05,      # share of the stated goal mixed back in after every update
    "conf_k": 5,         # confidence = n / (n + conf_k)
    "declared_count": 2.0,
}
CHOOSE_REASONS = {"health": "h", "climate": "s", "easy": "t"}
REJECT_REASONS = {"not_healthy_enough": "h", "not_sustainable": "s", "too_complicated": "t"}
STRENGTH = {"like": 1.0, "save": 0.6, "choose": 0.8, "onboarding_like": 1.0}


# ======================================================================================
class TasteModel:
    def __init__(self, params=None):
        self.p = params or PARAMS
        self.likes, self.dislikes = {}, {}      # observed (weighted) counts
        self.l0, self.d0 = {}, {}               # starting counts (declared or corrected)
        self.prov = {}

    @property
    def a(self):  # concepts with a belief (kept for callers that iterate concepts)
        return {c: None for c in set(self.likes) | set(self.l0)}

    def _ensure(self, c):
        for d in (self.likes, self.dislikes, self.l0, self.d0):
            d.setdefault(c, 0.0)
        self.prov.setdefault(c, [])

    def mean(self, concept, now=None):
        if concept not in self.likes:
            return 0.5
        l = self.l0[concept] + self.likes[concept]
        d = self.d0[concept] + self.dislikes[concept]
        return (1.0 + l) / (2.0 + l + d)

    def evidence(self, concept):
        return self.likes.get(concept, 0.0) + self.dislikes.get(concept, 0.0)

    def counts(self, concept):
        return {"likes": round(self.l0.get(concept, 0) + self.likes.get(concept, 0), 2),
                "dislikes": round(self.d0.get(concept, 0) + self.dislikes.get(concept, 0), 2)}

    def _log(self, c, event_id, delta, kind):
        lst = self.prov.setdefault(c, [])
        lst.append({"event_id": event_id, "delta": round(delta, 4), "kind": kind})
        del lst[:-20]

    def declare(self, concept, liked, event_id):
        self._ensure(concept)
        before = self.mean(concept)
        self.l0[concept], self.d0[concept] = (self.p["declared_count"], 0.0) if liked else (0.0, self.p["declared_count"])
        self._log(concept, event_id, self.mean(concept) - before, "declared")

    def observe(self, concept_weights, liked, strength, event_id, kind):
        """Every concept of the dish (its cuisine, ingredients, food groups) gets `strength` of a
        like or dislike: 1 for a like or a taste rejection, 0.8 for a choice, 0.6 for a save."""
        changed = {}
        for c in concept_weights:
            self._ensure(c)
            before = self.mean(c)
            (self.likes if liked else self.dislikes)[c] += strength
            after = self.mean(c)
            self._log(c, event_id, after - before, kind)
            changed[c] = (before, after)
        return changed

    def override(self, concept, mean, event_id, total=6.0):
        """User correction: replace the belief with an explicit one worth `total` observations."""
        self._ensure(concept)
        before = self.mean(concept)
        mean = min(0.95, max(0.05, float(mean)))
        n = total + 2.0
        self.l0[concept] = mean * n - 1.0
        self.d0[concept] = n - 2.0 - self.l0[concept]
        self.likes[concept] = self.dislikes[concept] = 0.0
        self._log(concept, event_id, self.mean(concept) - before, "user_correction")
        return {concept: (before, self.mean(concept))}

    def top(self, n=8):
        rows = [(c, self.mean(c), self.evidence(c)) for c in self.a]
        rows.sort(key=lambda r: (-abs(r[1] - 0.5) * (1 + r[2]), r[0]))
        return rows[:n]

    def to_dict(self):
        return {"likes": self.likes, "dislikes": self.dislikes, "l0": self.l0, "d0": self.d0, "prov": self.prov}


# ======================================================================================
class ValueModel:
    def __init__(self, stated, params=None):
        self.p = params or PARAMS
        self.stated = {k: float(stated[k]) for k in VALUE_CRITERIA}
        self.observed = dict(self.stated)
        self.n = 0                  # informative events since the last goal save
        self.counts = {"choices": 0, "reasons": 0, "rejections": 0, "answers": 0, "goal_saves": 0}
        self.evidence = []          # last 40 updates, newest last

    def _apply(self, factors, kind, event_id, extra=None):
        before = dict(self.observed)
        o = {k: self.observed[k] * math.exp(self.p["eta"] * factors.get(k, 0.0)) for k in VALUE_CRITERIA}
        tot = sum(o.values())
        a = self.p["anchor"]
        self.observed = {k: (1 - a) * v / tot + a * self.stated[k] for k, v in o.items()}
        self.n += 1
        self._log(kind, event_id, before, extra)

    def _log(self, kind, event_id, before, extra=None):
        row = {"event_id": event_id, "kind": kind,
               "before": {k: round(before[k], 4) for k in VALUE_CRITERIA},
               "after": {k: round(self.observed[k], 4) for k in VALUE_CRITERIA}}
        row.update(extra or {})
        self.evidence.append(row)
        del self.evidence[:-40]

    @staticmethod
    def _diff(shown, idx, sign=1.0):
        """sign * (score of item idx - mean score of all shown items), per criterion."""
        out = {}
        for k in VALUE_CRITERIA:
            vals = [s.get(k) for s in shown]
            known = [v for v in vals if v is not None]
            if vals[idx] is None or len(known) < 2:
                continue
            out[k] = sign * (vals[idx] - sum(known) / len(known))
        return out

    def observe_choice(self, shown, idx, event_id, reason=None):
        d = self._diff(shown, idx)
        self.counts["choices"] += 1
        extra = {"diff": {k: round(v, 3) for k, v in d.items()}, "reason": reason}
        k, flag = CHOOSE_REASONS.get(reason), None
        if k is not None and d.get(k, 0.0) > 0:
            self.counts["reasons"] += 1
            d = {**d, k: d[k] + self.p["nudge"]}
        elif k is not None:
            flag = extra["flag"] = "reason_inconsistent_with_choice"
        self._apply(d, "choice", event_id, extra)
        return flag

    def observe_rejection(self, shown, idx, reason, event_id):
        k = REJECT_REASONS.get(reason)
        if k is None:
            return False
        d = self._diff(shown, idx, sign=-1.0).get(k)
        if d is None or d <= 0:
            self._log("rejection", event_id, dict(self.observed), {"reason": reason, "flag": "reason_inconsistent"})
            return False
        self.counts["rejections"] += 1
        self._apply({k: d}, "rejection", event_id, {"reason": reason, "criterion": k})
        return True

    def observe_answer(self, winner, loser, event_id):
        self.counts["answers"] += 1
        self._apply({winner: self.p["nudge"], loser: -self.p["nudge"]}, "tradeoff_answer", event_id,
                    {"winner": winner, "loser": loser})

    def observe_goal(self, weights, event_id):
        before = dict(self.observed)
        self.stated = {k: float(weights[k]) for k in VALUE_CRITERIA}
        self.observed = {k: (self.observed[k] + self.stated[k]) / 2 for k in VALUE_CRITERIA}
        self.n = 0
        self.counts["goal_saves"] += 1
        self._log("goal_saved", event_id, before, {"weights": {k: round(self.stated[k], 3) for k in VALUE_CRITERIA}})

    # ---- read-outs ---------------------------------------------------------------------
    def gap(self, stated=None):
        w = stated or self.stated
        return {k: self.observed[k] - w[k] for k in VALUE_CRITERIA}

    def match(self, stated=None):
        w = stated or self.stated
        return 1.0 - 0.5 * sum(abs(w[k] - self.observed[k]) for k in VALUE_CRITERIA)

    def confidence(self):
        return self.n / (self.n + self.p["conf_k"])

    def to_dict(self):
        return {"stated": self.stated, "observed": self.observed, "n": self.n, "counts": self.counts,
                "match": self.match(), "confidence": self.confidence(), "evidence": self.evidence}


# ======================================================================================
class Learner:
    """Applies events to both models. Pure function of the event sequence."""

    def __init__(self, catalogue, params=None):
        self.cat = catalogue
        self.p = dict(PARAMS if params is None else params)
        self.taste = TasteModel(self.p)
        self.values = None
        self.history = []       # (recipe_id, y, strength) for the extended taste scorer
        self.n_events = 0

    @property
    def stated(self):
        return self.values.stated if self.values else None

    # ---- helpers ---------------------------------------------------------------------
    def _snapshot(self):
        return ({c: self.taste.mean(c) for c in self.taste.a},
                dict(self.values.observed) if self.values else None)

    @staticmethod
    def _trail(before, after, event_id, why):
        rows = []
        (bt, bo), (at, ao) = before, after
        for c, v in at.items():
            b = bt.get(c, 0.5)
            if abs(v - b) >= 0.01:
                rows.append({"component": "taste", "key": c, "before": round(b, 3),
                             "after": round(v, 3), "event_id": event_id, "why": why})
        if bo and ao:
            for k in VALUE_CRITERIA:
                if abs(ao[k] - bo[k]) >= 0.005:
                    rows.append({"component": "value", "key": k, "before": round(bo[k], 3),
                                 "after": round(ao[k], 3), "event_id": event_id, "why": why})
        return rows

    # ---- event handling --------------------------------------------------------------
    def apply(self, ev):
        """ev: {"event_id","type","ts","payload"}. Returns a list of update-trail rows."""
        typ, eid, pl = ev["type"], ev["event_id"], ev["payload"]
        before = self._snapshot()
        why = typ
        self.n_events += 1
        if typ == "profile_created":
            self._declare(pl["declared"], eid, only_new=False)
            for rid in pl["declared"].get("liked_recipes", []):
                if rid in self.cat.by_id:
                    self.taste.observe(self.cat.concepts(self.cat.get(rid)), True, 1.0, eid, "onboarding_like")
                    self.history.append((rid, 1.0, 1.0))
            self.values = ValueModel(pl["weights"], self.p)
            why = "onboarding"
        elif typ in ("goal_saved", "proposal_accepted"):
            if self.values is None:
                self.values = ValueModel(pl["weights"], self.p)
            else:
                self.values.observe_goal(pl["weights"], eid)
            why = "you saved a goal" if typ == "goal_saved" else "you accepted a proposal"
        elif typ == "feedback":
            why = self._feedback(pl, eid)
        elif typ == "profile_updated":
            self._declare(pl["declared"], eid, only_new=True)
            why = "you updated your profile"
        elif typ == "question_answered":
            w, l = pl.get("winner"), pl.get("loser")
            if self.values and w in VALUE_CRITERIA and l in VALUE_CRITERIA and w != l:
                self.values.observe_answer(w, l, eid)
                why = f"you said {w} matters more than {l}"
            else:
                why = "answer recorded (no model update)"
        elif typ == "profile_correction" and pl.get("kind") == "taste_override":
            self.taste.override(pl["concept"], pl["mean"], eid)
            why = "you corrected " + pl["concept"]
        return self._trail(before, self._snapshot(), eid, why)

    def _declare(self, d, eid, only_new):
        """Declared likes/dislikes. A profile edit only adds concepts that have no belief yet,
        so learned evidence is never wiped by re-saving the profile."""
        items = [(f"cuisine:{c}", True) for c in d.get("preferred_cuisines", [])]
        items += [(f"ingredient:{i}", True) for i in d.get("liked_ingredients", [])]
        items += [(f"ingredient:{i['name']}", False) for i in d.get("disliked_ingredients", []) if not i.get("strict")]
        for concept, liked in items:
            if not only_new or concept not in self.taste.likes:
                self.taste.declare(concept, liked, eid)

    def _feedback(self, pl, eid):
        typ, rid, reason = pl["type"], pl["recipe_id"], pl.get("reason")
        recipe = self.cat.get(rid)
        conc = self.cat.concepts(recipe)
        shown = pl.get("shown") or []
        ids = [s["recipe_id"] for s in shown]
        idx = ids.index(rid) if rid in ids else None
        scores = [s["scores"] for s in shown]
        if typ in ("like", "save", "choose"):
            s = STRENGTH[typ] + (0.2 if typ == "choose" and reason == "taste" else 0.0)
            self.taste.observe(conc, True, s, eid, typ)
            self.history.append((rid, 1.0, s))
            if typ == "choose" and idx is not None and len(shown) > 1 and self.values:
                self.values.observe_choice(scores, idx, eid, reason)
            return {"like": "you liked ", "save": "you saved ", "choose": "you chose "}[typ] + recipe["title"]
        if typ == "reject":
            if reason in REJECT_REASONS:
                if idx is not None and self.values:
                    self.values.observe_rejection(scores, idx, reason, eid)
                return f"you rejected {recipe['title']} ({reason.replace('_', ' ')})"
            strength = 1.0 if reason == "not_my_taste" else 0.5
            self.taste.observe(conc, False, strength, eid, "reject")
            self.history.append((rid, 0.0, strength))
            return "you rejected " + recipe["title"]
        return typ

    def state_dict(self):
        return {"taste": self.taste.to_dict(), "values": self.values.to_dict() if self.values else None,
                "history": self.history, "n_events": self.n_events}
