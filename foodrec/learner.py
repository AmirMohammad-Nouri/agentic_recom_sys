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
             Before every multiplicative step each o_k is lifted to at least FLOOR (2%), so a priority
             the user set to zero can still grow when their behaviour favours it (a pure
             multiplicative update would keep it at 0 forever).
             then renormalise, and keep a little of the goal: o <- (1 - ANCHOR) * o + ANCHOR * w,
             so a run of unusual choices moves the estimate but cannot wipe a priority out. Read-outs:
                 gap_k            = o_k - w_k
                 preference match = 1 - 1/2 * sum_k |w_k - o_k|        (1 = behaviour matches goal)
                 confidence       = n / (n + 5), n = informative events since the last goal save
             The observed weights NEVER change the saved goal. The supervisor may propose a
             change (policy.py) that the user has to accept.

Temporary vs lasting (the "scope" of a signal)
             Feedback and answers carry scope "lasting" (default) or "today". A "today" signal never
             touches either model; it only adds a same-day adjustment (temporary_effects): a small
             weight nudge, a replacement weight set, or hiding a recipe until the end of the day.
             Answering "only today" also rolls the value model back to how it was at the start of
             the day; "keep my goal" keeps the saved goal AND the observed weights, and marks the current gap
             as acknowledged so the supervisor only reacts to further drift.

Recency      Choosing a recipe or rejecting it as "had it recently" stores WHEN it was eaten
             (recent[recipe_id] = ts). The engine applies a temporary repetition penalty that
             fades out; it never adds dislikes to the recipe's ingredients or cuisine.

Repeated clicks on the same button are ignored: a like/save counts once per recipe, a choice
once per recipe per list, a rejection once per recipe, list and reason.

All constants are design choices, documented in docs/user-model.md.
"""
import math
import time

from . import INTERACTIONS, VALUE_CRITERIA
from .ranker import conform_weights

# Learning parameters and interaction options come from the "interactions" section of
# foodrec.config.json (model_spec.py); the defaults are the values below.
PARAMS = {
    "eta": 1.0,          # learning rate of the value model
    "nudge": 0.2,        # size of an explicit signal (reason chip, answered question)
    "anchor": 0.05,      # share of the stated goal mixed back in after every update
    "floor": 0.02,       # minimum observed weight before a multiplicative step (lets zero weights grow)
    "today_nudge": 0.10, # weight added to a criterion for the rest of the day by a "today" signal
    "conf_k": 5,         # confidence = n / (n + conf_k)
    "declared_count": 2.0,
    **INTERACTIONS["learning"],
}
PARAMS["conf_k"] = int(PARAMS["conf_k"])
# reason id -> criterion it is about (reasons without a criterion are about taste)
CHOOSE_REASONS = {r["id"]: r["criterion"] for r in INTERACTIONS["choose_reasons"] if r["criterion"]}
REJECT_REASONS = {r["id"]: r["criterion"] for r in INTERACTIONS["reject_reasons"] if r["criterion"]}
REASON_WEIGHT = {(k, r["id"]): r["weight"] for k in ("choose", "reject") for r in INTERACTIONS[f"{k}_reasons"]}
TASTE_BONUS = {r["id"]: r["taste_bonus"] for r in INTERACTIONS["choose_reasons"]}
TASTE_STRENGTH = {r["id"]: r["taste_strength"] for r in INTERACTIONS["reject_reasons"]}
RECENCY_REASONS = {r["id"] for r in INTERACTIONS["reject_reasons"] if r.get("effect") == "recency"}
STRENGTH = {**INTERACTIONS["feedback_strength"], "onboarding_like": 1.0}
SCOPES = ("lasting", "today")


def day_key(ts):
    """Local calendar day of a timestamp; "today" signals expire when it changes."""
    return time.strftime("%Y-%m-%d", time.localtime(ts or 0.0))


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
        self.stated = conform_weights(stated)
        self.observed = dict(self.stated)
        self.n = 0                  # informative events since the last goal save
        # "Keep my goal": the gap the user saw and accepted, and the event count at that moment.
        # The observed weights are kept; the supervisor only reacts to drift beyond this.
        self.ack = None
        self.counts = {"choices": 0, "reasons": 0, "rejections": 0, "answers": 0, "goal_saves": 0}
        self.evidence = []          # last 40 updates, newest last
        self.ts = None              # timestamp of the event being applied (set by Learner)
        self.day_start = {}         # day -> (observed, n) before the first update of that day

    def _apply(self, factors, kind, event_id, extra=None):
        before = dict(self.observed)
        if self.ts is not None:
            d = day_key(self.ts)
            if d not in self.day_start:
                self.day_start[d] = (dict(self.observed), self.n)
                for old in sorted(self.day_start)[:-7]:
                    del self.day_start[old]
        floor = self.p.get("floor", 0.0)
        o = {k: max(self.observed[k], floor) * math.exp(self.p["eta"] * factors.get(k, 0.0))
             for k in VALUE_CRITERIA}
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
            d = {**d, k: d[k] + self.p["nudge"] * REASON_WEIGHT.get(("choose", reason), 1.0)}
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
        self._apply({k: d * REASON_WEIGHT.get(("reject", reason), 1.0)}, "rejection", event_id,
                    {"reason": reason, "criterion": k})
        return True

    def observe_answer(self, winner, loser, event_id):
        self.counts["answers"] += 1
        self._apply({winner: self.p["nudge"], loser: -self.p["nudge"]}, "tradeoff_answer", event_id,
                    {"winner": winner, "loser": loser})

    def observe_goal(self, weights, event_id):
        before = dict(self.observed)
        self.stated = conform_weights(weights)
        self.observed = {k: (self.observed[k] + self.stated[k]) / 2 for k in VALUE_CRITERIA}
        self.ack = None  # a new goal is a new baseline
        self.n = 0
        self.day_start = {}
        self.counts["goal_saves"] += 1
        self._log("goal_saved", event_id, before, {"weights": {k: round(self.stated[k], 3) for k in VALUE_CRITERIA}})

    def rollback_day(self, ts, event_id):
        """Undo every update made on the day of `ts` ("that was only today")."""
        snap = self.day_start.pop(day_key(ts), None)
        if snap is None:
            return False
        before = dict(self.observed)
        self.observed, self.n = dict(snap[0]), snap[1]
        if self.ack and self.ack["n"] > self.n:
            self.ack["n"] = self.n
        self._log("rolled_back_today", event_id, before)
        return True

    def keep_goal(self, event_id):
        """"Keep my goal": the saved goal stays and NOTHING learned is discarded. The current gap
        is recorded as acknowledged, so the supervisor does not raise it again; it only reacts
        when the choices drift further from here (see drift() and events_since_ack())."""
        self.ack = {"gap": self.gap(), "n": self.n}
        self._log("kept_goal", event_id, dict(self.observed), {"acknowledged_gap": {k: round(v, 3)
                                                                                 for k, v in self.ack["gap"].items()}})

    def drift(self, stated=None):
        """Gap beyond what the user acknowledged with "Keep my goal" (= gap() without one).

        Only movement FURTHER from the saved goal counts. Per criterion, with g the current gap
        and a the accepted one:
            g on the same side as a   ->  only the part beyond a: sign(g) * max(0, |g| - |a|)
                                           (moving back toward the goal gives 0)
            g on the other side       ->  g (a new drift in the opposite direction)
        """
        g = self.gap(stated)
        if not self.ack:
            return g
        out = {}
        for k in VALUE_CRITERIA:
            a = self.ack["gap"].get(k, 0.0)
            if abs(a) < 1e-9 or (g[k] > 0) != (a > 0):
                out[k] = g[k]
            else:
                out[k] = math.copysign(max(0.0, abs(g[k]) - abs(a)), g[k])
        return out

    def events_since_ack(self):
        return self.n - (self.ack["n"] if self.ack else 0)

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
                "match": self.match(), "confidence": self.confidence(), "evidence": self.evidence,
                "acknowledged": self.ack}


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
        self.recent = {}        # recipe_id -> ts it was last eaten (choice or "had it recently")
        self.temporary = []     # same-day adjustments from "today" signals, see temporary_effects
        self._seen = set()      # feedback keys already applied (repeated clicks)

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
        self._ts = ev.get("ts") or 0.0
        if self.values is not None:
            self.values.ts = self._ts
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
        elif typ == "question_answered" and pl.get("scope") == "today":
            if self.values:
                self.values.rollback_day(self._ts, eid)
            if pl.get("weights"):
                self.temporary.append({"ts": self._ts, "type": "weights", "weights": dict(pl["weights"]),
                                       "event_id": eid})
            for k, v in (pl.get("nudge") or {}).items():
                if k in VALUE_CRITERIA:
                    self.temporary.append({"ts": self._ts, "type": "nudge", "criterion": k, "amount": float(v),
                                           "event_id": eid})
            why = "you said this was only for today (no lasting change)"
        elif typ == "question_answered" and pl.get("scope") == "keep_goal":
            if self.values:
                self.values.keep_goal(eid)
            why = "you chose to keep your goal (what was learned is kept)"
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
        if self.values is not None:
            self.values.ts = None
        return self._trail(before, self._snapshot(), eid, why)

    # ---- repeated clicks, recency and same-day adjustments -------------------------------
    @staticmethod
    def feedback_key(pl):
        """Identity of a click; None when it may legitimately repeat (no list to tie it to)."""
        typ, rid, req = pl.get("type"), pl.get("recipe_id"), pl.get("request_id")
        if typ in ("like", "save"):
            return (typ, rid)
        if not req:
            return None
        if typ == "choose":
            return (typ, req, rid)
        return (typ, req, rid, pl.get("reason"))

    def is_duplicate(self, pl):
        key = self.feedback_key(pl)
        return key is not None and key in self._seen

    def temporary_effects(self, now):
        """Adjustments from "today" signals that are still valid at `now` (same local day)."""
        today = day_key(now)
        out = {"weights": None, "nudges": {}, "hidden": [], "events": []}
        for t in self.temporary:
            if day_key(t["ts"]) != today:
                continue
            out["events"].append(t["event_id"])
            if t["type"] == "weights":
                out["weights"] = dict(t["weights"])
            elif t["type"] == "nudge":
                out["nudges"][t["criterion"]] = out["nudges"].get(t["criterion"], 0.0) + t["amount"]
            elif t["type"] == "hide" and t["recipe_id"] not in out["hidden"]:
                out["hidden"].append(t["recipe_id"])
        return out

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
        key = self.feedback_key(pl)
        if key is not None and key in self._seen:
            return "repeated click on " + recipe["title"] + " (ignored)"
        if key is not None:
            self._seen.add(key)
        if typ == "choose" or (typ == "reject" and reason in RECENCY_REASONS):
            self.recent[rid] = self._ts
        if typ == "reject" and reason in RECENCY_REASONS:
            return f"you had {recipe['title']} recently (temporary repetition penalty, no dislike)"
        if pl.get("scope") == "today" and typ in ("choose", "reject"):
            return self._today_feedback(typ, rid, reason, recipe, eid)
        conc = self.cat.concepts(recipe)
        shown = pl.get("shown") or []
        ids = [s["recipe_id"] for s in shown]
        idx = ids.index(rid) if rid in ids else None
        scores = [s["scores"] for s in shown]
        if typ in ("like", "save", "choose"):
            s = STRENGTH[typ] + (TASTE_BONUS.get(reason, 0.0) if typ == "choose" else 0.0)
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
            strength = TASTE_STRENGTH.get(reason, 0.5)
            self.taste.observe(conc, False, strength, eid, "reject")
            self.history.append((rid, 0.0, strength))
            return "you rejected " + recipe["title"]
        return typ

    def _today_feedback(self, typ, rid, reason, recipe, eid):
        """A choice or rejection that only applies today: no lasting update to either model."""
        k = CHOOSE_REASONS.get(reason) if typ == "choose" else REJECT_REASONS.get(reason)
        if k is not None:
            self.temporary.append({"ts": self._ts, "type": "nudge", "criterion": k,
                                   "amount": self.p.get("today_nudge", 0.10), "event_id": eid})
        if typ == "reject":
            self.temporary.append({"ts": self._ts, "type": "hide", "recipe_id": rid, "event_id": eid})
        del self.temporary[:-50]
        verb = "chose" if typ == "choose" else "rejected"
        return f"you {verb} {recipe['title']} for today only (no lasting change)"

    def state_dict(self):
        return {"taste": self.taste.to_dict(), "values": self.values.to_dict() if self.values else None,
                "history": self.history, "n_events": self.n_events, "recent": self.recent,
                "temporary": self.temporary}
