"""Bayesian preference learner.

Two models, both deterministic functions of the event log (replayable):

TasteModel   Beta(alpha, beta) belief per food concept (cuisine, ingredient, category).
             Feeds the preference-match score P. Pseudo-counts decay toward the prior.

ChoiceModel  Gaussian belief over theta in R^5 (the user's REVEALED trade-off between the
             criteria p,h,s,c,t) updated with a Laplace approximation from
               * slate choices (conditional logit over the five items shown),
               * reason chips ("I picked it because of price"),
               * criterion-specific rejections ("too expensive"),
               * explicit statements (saving a goal, accepting a proposal),
               * answers to the supervisor's pairwise trade-off questions
                 ("when they conflict, which matters more: sustainability or taste?").
             Prior mean = BETA0 * stated weights. It estimates what the user does; it NEVER
             changes the saved goal weights. Differences are reported as discrepancies and
             the supervisor agent may PROPOSE a goal change that the user must accept.

All defaults are documented in docs/features.md. They are design choices, not yet calibrated on user data.
"""
import math

import numpy as np

from . import CRITERIA

PARAMS = {
    "taste_k": 3.0,              # pseudo-count mass added by one full-strength observation
    "taste_half_life_days": 90.0,
    "beta0": 10.0,               # scale linking simplex weights to logit-scale theta
    "prior_sd": 1.5,
    "statement_sd": 1.0,         # measurement noise when the user explicitly states weights
    "process_noise_per_day": 0.004,
    "kappa_choice": 0.7,         # likelihood tempering for a slate choice
    "kappa_reason": 1.0,
    "reason_gain": 2.0,
    "discrepancy_delta": 0.08,   # minimal weight difference worth flagging
    "n_samples": 1000,
    "seed": 12345,
}
REASON_TO_CRITERION = {"taste": "p", "price": "c", "health": "h", "climate": "s", "time": "t"}
REJECT_REASON_TO_CRITERION = {"too_expensive": "c", "too_slow": "t", "not_healthy_enough": "h",
                              "not_sustainable": "s"}
DAY = 86400.0


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def _softmax(u):
    u = u - u.max()
    e = np.exp(u)
    return e / e.sum()


# ======================================================================================
class TasteModel:
    def __init__(self, params=None):
        self.p = params or PARAMS
        self.a, self.b = {}, {}
        self.a0, self.b0 = {}, {}
        self.last = {}
        self.prov = {}

    def _ensure(self, c):
        if c not in self.a:
            self.a[c] = self.a0[c] = 1.0
            self.b[c] = self.b0[c] = 1.0
            self.prov[c] = []

    def _decay(self, c, now):
        t0 = self.last.get(c)
        if t0 is not None and now is not None and now > t0:
            f = 0.5 ** ((now - t0) / DAY / self.p["taste_half_life_days"])
            self.a[c] = self.a0[c] + (self.a[c] - self.a0[c]) * f
            self.b[c] = self.b0[c] + (self.b[c] - self.b0[c]) * f
        if now is not None:
            self.last[c] = now

    def set_prior(self, concept, alpha, beta, source, event_id=None):
        self._ensure(concept)
        self.a[concept] = self.a0[concept] = alpha
        self.b[concept] = self.b0[concept] = beta
        self._log(concept, event_id, 0.0, source)

    def mean(self, concept, now=None):
        if concept not in self.a:
            return 0.5
        a, b = self.a[concept], self.b[concept]
        if now is not None and self.last.get(concept) is not None and now > self.last[concept]:
            f = 0.5 ** ((now - self.last[concept]) / DAY / self.p["taste_half_life_days"])
            a = self.a0[concept] + (a - self.a0[concept]) * f
            b = self.b0[concept] + (b - self.b0[concept]) * f
        return a / (a + b)

    def sd(self, concept):
        a, b = self.a.get(concept, 1.0), self.b.get(concept, 1.0)
        return math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))

    def evidence(self, concept):
        if concept not in self.a:
            return 0.0
        return (self.a[concept] + self.b[concept]) - (self.a0[concept] + self.b0[concept])

    def _log(self, c, event_id, delta, kind):
        lst = self.prov.setdefault(c, [])
        lst.append({"event_id": event_id, "delta": round(delta, 4), "kind": kind})
        del lst[:-20]

    def observe(self, concept_weights, y, strength, event_id, ts, kind):
        """y in [0, 1]; concept_weights sum to 1. Returns {concept: (before, after)}."""
        changed = {}
        k = self.p["taste_k"] * strength
        for c, w in concept_weights.items():
            self._ensure(c)
            self._decay(c, ts)
            before = self.a[c] / (self.a[c] + self.b[c])
            self.a[c] += k * w * y
            self.b[c] += k * w * (1.0 - y)
            after = self.a[c] / (self.a[c] + self.b[c])
            self._log(c, event_id, after - before, kind)
            changed[c] = (before, after)
        return changed

    def override(self, concept, mean, event_id, strength=6.0):
        """User correction: replace the belief with an explicit, confident one."""
        self._ensure(concept)
        before = self.mean(concept)
        a = max(0.05, mean) * strength
        b = max(0.05, 1.0 - mean) * strength
        self.a[concept] = self.a0[concept] = a
        self.b[concept] = self.b0[concept] = b
        self._log(concept, event_id, mean - before, "user_correction")
        return {concept: (before, a / (a + b))}

    def top(self, n=8):
        rows = []
        for c in self.a:
            m = self.mean(c)
            rows.append((c, m, self.sd(c), self.evidence(c)))
        rows.sort(key=lambda r: (-abs(r[1] - 0.5) * (1 + r[3]), r[0]))
        return rows[:n]

    def to_dict(self):
        return {"a": self.a, "b": self.b, "a0": self.a0, "b0": self.b0, "last": self.last,
                "prov": self.prov}

    @classmethod
    def from_dict(cls, d, params=None):
        m = cls(params)
        m.a, m.b, m.a0, m.b0 = d["a"], d["b"], d["a0"], d["b0"]
        m.last, m.prov = d["last"], d["prov"]
        return m


# ======================================================================================
class ChoiceModel:
    def __init__(self, stated, params=None, ts=None):
        self.p = params or PARAMS
        w = np.array([stated[k] for k in CRITERIA], float)
        self.mu0 = self.p["beta0"] * w
        self.mu = self.mu0.copy()
        self.Sigma0 = np.eye(5) * self.p["prior_sd"] ** 2
        self.Sigma = self.Sigma0.copy()
        self.n_choices = 0
        self.n_reasons = 0
        self.n_rejections = 0
        self.n_statements = 0
        self.last_ts = ts
        self.evidence = []   # compact provenance, last 40

    # ---- numerics --------------------------------------------------------------
    def _laplace(self, mnl, binary, mu, Sigma, iters=6):
        P0 = np.linalg.inv(Sigma)
        th = mu.copy()
        for _ in range(iters):
            g = -P0 @ (th - mu)
            H = P0.copy()
            for X, c, kap in mnl:
                p = _softmax(X @ th)
                g += kap * (X[c] - p @ X)
                H += kap * (X.T @ (np.diag(p) - np.outer(p, p)) @ X)
            for a, kap in binary:
                s = _sigmoid(a @ th)
                g += kap * (1.0 - s) * a
                H += kap * s * (1.0 - s) * np.outer(a, a)
            th = th + np.linalg.solve(H, g)
        H = P0.copy()
        for X, c, kap in mnl:
            p = _softmax(X @ th)
            H += kap * (X.T @ (np.diag(p) - np.outer(p, p)) @ X)
        for a, kap in binary:
            s = _sigmoid(a @ th)
            H += kap * s * (1.0 - s) * np.outer(a, a)
        S = np.linalg.inv(H)
        return th, (S + S.T) / 2.0

    def _log(self, kind, event_id, extra=None):
        row = {"event_id": event_id, "kind": kind}
        if extra:
            row.update(extra)
        self.evidence.append(row)
        del self.evidence[:-40]

    def inflate(self, ts):
        if self.last_ts is not None and ts is not None and ts > self.last_ts:
            days = (ts - self.last_ts) / DAY
            self.Sigma = self.Sigma + np.eye(5) * self.p["process_noise_per_day"] * days
        if ts is not None:
            self.last_ts = ts

    @staticmethod
    def feature_matrix(shown):
        """shown: list of dicts criterion->score|None. Missing values contribute no
        information: they are replaced by the mean of the other items' values."""
        X = np.zeros((len(shown), 5))
        for j, k in enumerate(CRITERIA):
            vals = [s.get(k) for s in shown]
            known = [v for v in vals if v is not None]
            fill = float(np.mean(known)) if known else 0.5
            X[:, j] = [fill if v is None else v for v in vals]
        return X

    # ---- evidence types --------------------------------------------------------
    def observe_choice(self, shown, chosen_idx, event_id, ts, reason=None):
        self.inflate(ts)
        X = self.feature_matrix(shown)
        mnl = [(X, chosen_idx, self.p["kappa_choice"])]
        binary, flag = [], None
        if reason in REASON_TO_CRITERION:
            k = CRITERIA.index(REASON_TO_CRITERION[reason])
            d = X[chosen_idx, k] - X[:, k].mean()
            if d > 1e-6:
                a = np.zeros(5)
                a[k] = self.p["reason_gain"] * d
                binary.append((a, self.p["kappa_reason"]))
                self.n_reasons += 1
            else:
                flag = "reason_inconsistent_with_choice"
        self.mu, self.Sigma = self._laplace(mnl, binary, self.mu, self.Sigma)
        self.n_choices += 1
        self._log("choice", event_id, {"reason": reason, "flag": flag})
        return flag

    def observe_rejection(self, shown, rejected_idx, reason, event_id, ts):
        """Criterion-specific rejection, e.g. 'too_expensive'. Returns False if unusable."""
        self.inflate(ts)
        crit = REJECT_REASON_TO_CRITERION.get(reason)
        if crit is None:
            return False
        X = self.feature_matrix(shown)
        k = CRITERIA.index(crit)
        d = X[:, k].mean() - X[rejected_idx, k]
        if d <= 1e-6:
            self._log("rejection", event_id, {"reason": reason, "flag": "reason_inconsistent"})
            return False
        a = np.zeros(5)
        a[k] = self.p["reason_gain"] * d
        self.mu, self.Sigma = self._laplace([], [(a, self.p["kappa_reason"])], self.mu, self.Sigma)
        self.n_rejections += 1
        self._log("rejection", event_id, {"reason": reason})
        return True

    def observe_statement(self, weights, event_id, ts, sd=None):
        """Explicit statement of weights (goal saved / proposal accepted): Gaussian update."""
        self.inflate(ts)
        sd = sd or self.p["statement_sd"]
        y = self.p["beta0"] * np.array([weights[k] for k in CRITERIA], float)
        P = np.linalg.inv(self.Sigma) + np.eye(5) / sd ** 2
        S = np.linalg.inv(P)
        self.mu = S @ (np.linalg.inv(self.Sigma) @ self.mu + y / sd ** 2)
        self.Sigma = (S + S.T) / 2.0
        self.n_statements += 1
        self._log("statement", event_id, {"weights": {k: round(float(weights[k]), 3) for k in CRITERIA}})

    def observe_tradeoff(self, winner, loser, event_id, ts):
        """Pairwise answer: the user says criterion `winner` matters more than `loser`.

        Bradley-Terry style binary likelihood on theta_winner - theta_loser.
        """
        self.inflate(ts)
        a = np.zeros(5)
        a[CRITERIA.index(winner)] = self.p["reason_gain"]
        a[CRITERIA.index(loser)] = -self.p["reason_gain"]
        self.mu, self.Sigma = self._laplace([], [(a, self.p["kappa_reason"])], self.mu, self.Sigma)
        self.n_reasons += 1
        self._log("tradeoff_answer", event_id, {"winner": winner, "loser": loser})

    # ---- read-outs -------------------------------------------------------------
    def _simplex(self, th):
        pos = np.maximum(th, 0.0)
        s = pos.sum(axis=-1, keepdims=True)
        uniform = np.full_like(pos, 0.2)
        return np.where(s > 1e-9, pos / np.where(s > 1e-9, s, 1.0), uniform)

    def _samples(self, mu, Sigma):
        rng = np.random.default_rng(self.p["seed"])
        return rng.multivariate_normal(mu, Sigma, size=self.p["n_samples"])

    def inferred(self):
        """Posterior of the revealed weights on the simplex, with prior-relative confidence."""
        w = self._simplex(self._samples(self.mu, self.Sigma))
        w0 = self._simplex(self._samples(self.mu0, self.Sigma0))
        out = {}
        for j, k in enumerate(CRITERIA):
            sd, sd0 = float(w[:, j].std()), float(w0[:, j].std())
            out[k] = {"mean": float(w[:, j].mean()), "sd": sd,
                      "lo": float(np.percentile(w[:, j], 5)), "hi": float(np.percentile(w[:, j], 95)),
                      "confidence": float(min(1.0, max(0.0, 1.0 - sd / sd0))) if sd0 > 0 else 0.0}
        return out

    def discrepancy(self, stated):
        """Per criterion: P(|inferred - stated| > delta) and the signed mean difference."""
        w = self._simplex(self._samples(self.mu, self.Sigma))
        delta = self.p["discrepancy_delta"]
        res = {}
        for j, k in enumerate(CRITERIA):
            diff = w[:, j] - stated[k]
            res[k] = {"prob_beyond_delta": float(np.mean(np.abs(diff) > delta)),
                      "mean_diff": float(diff.mean()),
                      "direction": "lower" if diff.mean() < 0 else "higher"}
        return res

    def to_dict(self):
        return {"mu": self.mu.tolist(), "Sigma": self.Sigma.tolist(), "mu0": self.mu0.tolist(),
                "n_choices": self.n_choices, "n_reasons": self.n_reasons,
                "n_rejections": self.n_rejections, "n_statements": self.n_statements,
                "last_ts": self.last_ts, "evidence": self.evidence}


# ======================================================================================
class Learner:
    """Applies events to both models. Pure function of the event sequence."""

    def __init__(self, catalogue, params=None):
        self.cat = catalogue
        self.p = dict(PARAMS if params is None else params)
        self.taste = TasteModel(self.p)
        self.choice = None
        self.stated = None
        self.history = []       # (recipe_id, y, strength) for the extended preference scorer
        self.n_events = 0

    # ---- helpers ---------------------------------------------------------------
    def _snapshot(self):
        t = {c: self.taste.mean(c) for c in self.taste.a}
        inf = self.choice.inferred() if self.choice else None
        return t, ({k: v["mean"] for k, v in inf.items()} if inf else None)

    def _trail(self, before, after, event_id, why):
        rows = []
        bt, bi = before
        at, ai = after
        for c, v in at.items():
            b = bt.get(c, 0.5)
            if abs(v - b) >= 0.01:
                rows.append({"component": "taste", "key": c, "before": round(b, 3),
                             "after": round(v, 3), "event_id": event_id, "why": why})
        if bi and ai:
            for k in CRITERIA:
                if abs(ai[k] - bi[k]) >= 0.01:
                    rows.append({"component": "value", "key": k, "before": round(bi[k], 3),
                                 "after": round(ai[k], 3), "event_id": event_id, "why": why})
        return rows

    # ---- event handling --------------------------------------------------------
    def apply(self, ev):
        """ev: {"event_id","type","ts","payload"}. Returns a list of update-trail rows."""
        typ, ts, eid, pl = ev["type"], ev.get("ts"), ev["event_id"], ev["payload"]
        before = self._snapshot()
        why = typ
        self.n_events += 1

        if typ == "profile_created":
            self._seed(pl, eid, ts)
            why = "onboarding"
        elif typ in ("goal_saved", "proposal_accepted"):
            self.stated = dict(pl["weights"])
            if self.choice is None:
                self.choice = ChoiceModel(self.stated, self.p, ts)
            else:
                self.choice.observe_statement(self.stated, eid, ts)
            why = "you saved a goal" if typ == "goal_saved" else "you accepted a proposal"
        elif typ == "feedback":
            why = self._feedback(pl, eid, ts)
        elif typ == "profile_updated":
            self._update_priors(pl["declared"], eid)
            why = "you updated your profile"
        elif typ == "question_answered":
            why = self._answer(pl, eid, ts)
        elif typ == "profile_correction":
            why = self._correction(pl, eid, ts)
        return self._trail(before, self._snapshot(), eid, why)

    def _seed(self, pl, eid, ts):
        d = pl["declared"]
        for c in d.get("preferred_cuisines", []):
            self.taste.set_prior(f"cuisine:{c}", 3.5, 1.5, "declared", eid)
        for ing in d.get("liked_ingredients", []):
            self.taste.set_prior(f"ingredient:{ing}", 3.5, 1.5, "declared", eid)
        for ing in d.get("disliked_ingredients", []):
            if not ing.get("strict"):
                self.taste.set_prior(f"ingredient:{ing['name']}", 1.0, 3.0, "declared", eid)
        for rid in d.get("liked_recipes", []):
            if rid in self.cat.by_id:
                self.taste.observe(self.cat.concepts(self.cat.get(rid)), 1.0, 1.0, eid, ts,
                                   "onboarding_like")
                self.history.append((rid, 1.0, 1.0))
        self.stated = dict(pl["weights"])
        self.choice = ChoiceModel(self.stated, self.p, ts)

    def _update_priors(self, d, eid):
        """A profile edit adds priors only for concepts that have no belief yet, so learned
        evidence is never wiped by re-saving the profile."""
        for c in d.get("preferred_cuisines", []):
            if f"cuisine:{c}" not in self.taste.a:
                self.taste.set_prior(f"cuisine:{c}", 3.5, 1.5, "declared", eid)
        for ing in d.get("liked_ingredients", []):
            if f"ingredient:{ing}" not in self.taste.a:
                self.taste.set_prior(f"ingredient:{ing}", 3.5, 1.5, "declared", eid)
        for ing in d.get("disliked_ingredients", []):
            if not ing.get("strict") and f"ingredient:{ing['name']}" not in self.taste.a:
                self.taste.set_prior(f"ingredient:{ing['name']}", 1.0, 3.0, "declared", eid)

    def _answer(self, pl, eid, ts):
        w, l = pl.get("winner"), pl.get("loser")
        if self.choice is None or w not in CRITERIA or l not in CRITERIA or w == l:
            return "answer recorded (no model update)"
        self.choice.observe_tradeoff(w, l, eid, ts)
        return f"you said {w} matters more than {l}"

    def _feedback(self, pl, eid, ts):
        typ, rid, reason = pl["type"], pl["recipe_id"], pl.get("reason")
        recipe = self.cat.get(rid)
        conc = self.cat.concepts(recipe)
        shown = pl.get("shown") or []
        ids = [s["recipe_id"] for s in shown]
        idx = ids.index(rid) if rid in ids else None
        scores = [s["scores"] for s in shown]
        if typ == "like":
            self.taste.observe(conc, 1.0, 1.0, eid, ts, "like")
            self.history.append((rid, 1.0, 1.0))
            return "you liked " + recipe["title"]
        if typ == "save":
            self.taste.observe(conc, 1.0, 0.6, eid, ts, "save")
            self.history.append((rid, 1.0, 0.6))
            return "you saved " + recipe["title"]
        if typ == "choose":
            self.taste.observe(conc, 1.0, 0.8, eid, ts, "choose")
            self.history.append((rid, 1.0, 0.8))
            if idx is not None and len(shown) > 1 and self.choice is not None:
                self.choice.observe_choice(scores, idx, eid, ts, reason)
            return "you chose " + recipe["title"]
        if typ == "reject":
            if reason in REJECT_REASON_TO_CRITERION:
                if idx is not None and self.choice is not None:
                    self.choice.observe_rejection(scores, idx, reason, eid, ts)
                return f"you rejected {recipe['title']} ({reason.replace('_', ' ')})"
            strength = 1.0 if reason == "not_my_taste" else 0.5
            self.taste.observe(conc, 0.0, strength, eid, ts, "reject")
            self.history.append((rid, 0.0, strength))
            return "you rejected " + recipe["title"]
        return typ

    def _correction(self, pl, eid, ts):
        kind = pl["kind"]
        if kind == "taste_override":
            self.taste.override(pl["concept"], pl["mean"], eid)
            return "you corrected " + pl["concept"]
        return "profile correction"

    # ---- read-outs used by the engine and the agent ----------------------------
    def preference_means(self):
        return self.taste

    def state_dict(self):
        return {"taste": self.taste.to_dict(),
                "choice": self.choice.to_dict() if self.choice else None,
                "stated": self.stated, "history": self.history, "n_events": self.n_events}
