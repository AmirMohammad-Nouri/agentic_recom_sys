"""Application service: the single place where state changes.

Every mutation (1) is appended to the SQLite event log, (2) is applied to the user's learner
and (3) is projected into the knowledge graph. Saved goals and mandatory constraints change
ONLY through explicit user actions (saving, or accepting a supervisor proposal); the
supervisor and the learner never write them.
"""
import json
import logging
import threading
import time

from . import CRITERIA, CRITERION_LABELS
from .catalogue import Catalogue
from .compare import compare
from .constraints import SUPPORTED_ALLERGENS, SUPPORTED_DIETS, active_constraints, validate_declared
from .engine import public, recommend
from .features import ReferenceScaler
from .knowledge import make_graph, make_retriever
from .knowledge.documents import build_corpus
from .learner import Learner
from .llm import OllamaLLM
from .ranker import DEFAULT_WEIGHTS, resolve_weights
from .router import MODES
from .store import Store

log = logging.getLogger("foodrec.service")
LEARNER_EVENTS = ("profile_created", "profile_updated", "goal_saved", "proposal_accepted", "feedback",
                  "profile_correction", "question_answered")
FEEDBACK_TYPES = ("like", "save", "choose", "reject")
REJECT_REASONS = ("not_my_taste", "too_expensive", "too_slow", "not_healthy_enough", "not_sustainable",
                  "had_recently", "other")
CHOOSE_REASONS = ("taste", "price", "health", "climate", "time")


class App:
    def __init__(self, settings, llm=None):
        self.s = settings
        self.lock = threading.RLock()
        self.catalogue = Catalogue.load(settings.catalogue_path)
        self.ingredients = json.loads(settings.ingredients_path.read_text())
        self.scaler = ReferenceScaler.fit(self.catalogue)
        self.store = Store(settings.db_path)
        self.llm = llm if llm is not None else OllamaLLM(settings)
        self.graph, self.graph_note = make_graph(settings)
        self.retriever, self.retriever_note = make_retriever(settings, self.llm)
        self.learners = {}
        self.score_cache = {}
        self._project_graph()
        self.reindex()

    # ---- startup projections ----------------------------------------------------------
    def _project_graph(self):
        t0 = time.perf_counter()
        self.graph.load_catalogue(self.catalogue, self.ingredients, self.scaler)
        for ev in self.store.events():
            self.graph.record_event(ev)
        for uid in self.store.users():
            for d in reversed(self.store.decisions(uid, limit=500)):
                self.graph.record_decision(d)
        log.info("graph projected in %.0f ms (%s)", (time.perf_counter() - t0) * 1000, self.graph_note)

    def reindex(self):
        chunks = build_corpus(self.catalogue, self.ingredients, self.s.knowledge_dir, self.store.documents())
        return self.retriever.build(chunks)

    def system(self):
        st = self.llm.status(refresh=True) if hasattr(self.llm, "status") else {"available": False}
        return {"llm": st, "graph": self.graph_note, "graph_stats": self.graph.stats(),
                "retrieval": {**self.retriever.description, "note": self.retriever_note},
                "catalogue": {"name": self.catalogue.name, "version": self.catalogue.version,
                              "recipes": len(self.catalogue), "note": self.catalogue.note,
                              "coverage": self.catalogue.coverage()},
                "scaler": self.scaler.version, "modes": MODES,
                "options": {"allergens": SUPPORTED_ALLERGENS, "diets": SUPPORTED_DIETS,
                            "cuisines": sorted({r["cuisine"] for r in self.catalogue.recipes}),
                            "ingredients": sorted({i["name"] for r in self.catalogue.recipes
                                                   for i in r["ingredients"]}),
                            "reject_reasons": REJECT_REASONS, "choose_reasons": CHOOSE_REASONS},
                "criteria": CRITERION_LABELS}

    # ---- learner -------------------------------------------------------------------------
    def learner(self, user_id):
        with self.lock:
            if user_id not in self.learners:
                lr = Learner(self.catalogue)
                for ev in self.store.events(user_id, types=LEARNER_EVENTS):
                    lr.apply(self._learner_event(ev))
                self.learners[user_id] = lr
            return self.learners[user_id]

    @staticmethod
    def _learner_event(ev):
        return {"event_id": ev["event_id"], "type": ev["type"], "ts": ev["ts"], "payload": ev["payload"]}

    def _emit(self, user_id, type_, payload, context=None):
        """Append to the log, update learner and graph. Returns (event, update trail)."""
        with self.lock:
            ev = self.store.append_event(user_id, type_, payload, context)
            trail = []
            if type_ in LEARNER_EVENTS:
                trail = self.learner(user_id).apply(self._learner_event(ev))
            self.graph.record_event(ev)
            return ev, trail

    # ---- profile & goal ----------------------------------------------------------------
    def state(self, user_id):
        prof = self.store.profile(user_id)
        goal = self.store.goal(user_id)
        return {"user_id": user_id, "onboarded": prof is not None,
                "profile": prof, "goal": goal,
                "constraints": [{"code": c, "label": l} for c, l in active_constraints(prof["declared"])] if prof else [],
                "open_decisions": self.store.decisions(user_id, status="open", limit=20)}

    @staticmethod
    def _clean_declared(d):
        out = {"allergens": sorted(set(d.get("allergens") or [])), "diets": sorted(set(d.get("diets") or [])),
               "disliked_ingredients": [{"name": x["name"].strip().lower(), "strict": bool(x.get("strict"))}
                                        for x in d.get("disliked_ingredients") or [] if x.get("name", "").strip()],
               "liked_ingredients": sorted({x.strip().lower() for x in d.get("liked_ingredients") or [] if x.strip()}),
               "preferred_cuisines": sorted(set(d.get("preferred_cuisines") or [])),
               "liked_recipes": list(d.get("liked_recipes") or []),
               "max_prep_minutes": d.get("max_prep_minutes") or None,
               "max_cost_eur": d.get("max_cost_eur") or None}
        validate_declared(out)
        return out

    def onboard(self, user_id, declared, weights=None, diversity=0.3, mode="lightweight"):
        with self.lock:
            declared = self._clean_declared(declared)
            w, _ = resolve_weights(weights or DEFAULT_WEIGHTS)
            pv = self.store.save_profile(user_id, declared, "onboarding")
            gv = self.store.save_goal(user_id, "Starting priorities", w, diversity, mode, "onboarding")
            self.learners.pop(user_id, None)
            self._emit(user_id, "profile_created", {"declared": declared, "weights": w,
                                                    "profile_version": pv, "goal_version": gv})
            return self.state(user_id)

    def update_profile(self, user_id, declared, source="user", note=None):
        with self.lock:
            declared = self._clean_declared(declared)
            pv = self.store.save_profile(user_id, declared, source, note)
            ev, trail = self._emit(user_id, "profile_updated", {"declared": declared, "profile_version": pv,
                                                                "source": source, "note": note})
            return {"profile_version": pv, "event_id": ev["event_id"], "trail": trail}

    def save_goal(self, user_id, name, weights, diversity, mode, source="user", note=None, decision_id=None):
        with self.lock:
            w, msg = resolve_weights(weights, (self.store.goal(user_id) or {}).get("weights"))
            if not 0 <= float(diversity) <= 1:
                raise ValueError("diversity must be between 0 and 1")
            if mode not in MODES:
                raise ValueError(f"unknown mode '{mode}'")
            gv = self.store.save_goal(user_id, name, w, float(diversity), mode, source, note)
            typ = "proposal_accepted" if source == "accepted_proposal" else "goal_saved"
            ev, trail = self._emit(user_id, typ, {"weights": w, "goal_version": gv, "name": name,
                                                  "decision_id": decision_id})
            return {"goal_version": gv, "weights_applied": w, "message": msg, "trail": trail}

    def revert_goal(self, user_id, version):
        old = next((g for g in self.store.goal_history(user_id) if g["version"] == version), None)
        if old is None:
            raise ValueError(f"goal version {version} not found")
        return self.save_goal(user_id, old["name"], old["weights"], old["diversity"], old["mode"],
                              "revert", f"restored version {version}")

    def correct_belief(self, user_id, concept, mean):
        ev, trail = self._emit(user_id, "profile_correction",
                               {"kind": "taste_override", "concept": concept, "mean": float(mean)})
        return {"event_id": ev["event_id"], "trail": trail}

    # ---- recommendations -----------------------------------------------------------------
    def recommend(self, user_id, overrides=None, origin="ui", save=True):
        overrides = overrides or {}
        prof = self.store.profile(user_id) or {"version": 0, "declared": {}}
        goal = self.store.goal(user_id) or {"version": 0, "weights": dict(DEFAULT_WEIGHTS),
                                            "diversity": 0.3, "mode": "lightweight"}
        cfg = {"weights": overrides.get("weights") or goal["weights"],
               "diversity": goal["diversity"] if overrides.get("diversity") is None else overrides["diversity"],
               "mode": overrides.get("mode") or goal["mode"],
               "request_filters": overrides.get("request_filters") or [],
               "last_valid_weights": goal["weights"], "k": 5}
        with self.lock:
            res = public(recommend(self.catalogue, self.scaler, self.learner(user_id), prof["declared"], cfg,
                                   now=time.time(), cache=self.score_cache))
        res.update({"origin": origin, "user_id": user_id,
                    "versions": {**res["versions"], "profile": prof["version"], "goal": goal["version"]},
                    "goal_weights": goal["weights"],
                    "weights_source": f"saved goal v{goal['version']}" if all(
                        abs(res["config"]["weights_applied"][k] - goal["weights"][k]) < 1e-6 for k in CRITERIA)
                    else "adjusted for this request"})
        if save:
            prev = self.store.last_list(user_id)
            self.store.save_request(user_id, origin, prof["version"], goal["version"], res)
            self.graph.record_request(user_id, res)
            res["previous_request_id"] = prev["request_id"] if prev else None
            if prev and prev.get("results") and res.get("results"):
                res["comparison"] = compare(prev, res)
        return res

    def compare_requests(self, before_id, after_id):
        a, b = self.store.request(before_id), self.store.request(after_id)
        if not a or not b:
            raise ValueError("unknown request id")
        return compare(a, b)

    def what_if(self, user_id, weights=None, diversity=None, mode=None):
        """Counterfactual: rank with changed settings WITHOUT saving anything, and compare
        with the same ranking under the current goal (both computed now, same learner state)."""
        base = self.recommend(user_id, origin="whatif_base", save=False)
        alt = self.recommend(user_id, {"weights": weights, "diversity": diversity, "mode": mode},
                             origin="whatif", save=False)
        if not base.get("results") or not alt.get("results"):
            return {"base": base, "alternative": alt, "comparison": None}
        return {"base": base, "alternative": alt, "comparison": compare(base, alt)}

    # ---- feedback ---------------------------------------------------------------------------
    def feedback(self, user_id, request_id, recipe_id, ftype, reason=None, context=None):
        if ftype not in FEEDBACK_TYPES:
            raise ValueError(f"feedback type must be one of {FEEDBACK_TYPES}")
        if recipe_id not in self.catalogue.by_id:
            raise ValueError(f"unknown recipe {recipe_id}")
        req = self.store.request(request_id) if request_id else None
        shown = [{"recipe_id": r["recipe_id"], "scores": r["scores"]} for r in (req or {}).get("results", [])]
        ev, trail = self._emit(user_id, "feedback", {"type": ftype, "recipe_id": recipe_id, "reason": reason,
                                                     "request_id": request_id, "shown": shown}, context)
        return {"event_id": ev["event_id"], "trail": trail}

    # ---- decisions ----------------------------------------------------------------------------
    def add_decision(self, user_id, kind, title, body, source):
        d = self.store.add_decision(user_id, kind, title, body, source)
        self.graph.record_decision(d)
        return d

    def resolve_decision(self, user_id, decision_id, action, answer=None):
        """action: accept | dismiss | answer. Only here can a proposal become a saved goal or a
        confirmed profile change, and only because the user clicked it."""
        d = self.store.decision(decision_id)
        if d is None or d["user_id"] != user_id:
            raise ValueError("unknown decision")
        if d["status"] != "open":
            raise ValueError(f"decision already {d['status']}")
        body, effect = d["body"], {}
        if action == "dismiss":
            ev, _ = self._emit(user_id, "proposal_dismissed", {"decision_id": decision_id, "kind": d["kind"],
                                                               "topic": body.get("topic"),
                                                               "criterion": body.get("criterion")})
            effect = {"event_id": ev["event_id"]}
            status = "dismissed"
        elif action == "accept" and d["kind"] == "goal_proposal":
            goal = self.store.goal(user_id)
            effect = self.save_goal(user_id, goal["name"], body["proposed_weights"], goal["diversity"],
                                    goal["mode"], "accepted_proposal", d["title"], decision_id)
            status = "accepted"
        elif action == "accept" and d["kind"] == "profile_change":
            effect = self.update_profile(user_id, body["proposed_declared"], "accepted_proposal", d["title"])
            status = "accepted"
        elif action == "answer" and d["kind"] in ("tradeoff_question", "taste_question"):
            opt = next((o for o in body["options"] if o["id"] == answer), None)
            if opt is None:
                raise ValueError("unknown answer option")
            ev, trail = self._emit(user_id, "question_answered", {"decision_id": decision_id, "answer": answer,
                                                                  "winner": opt.get("winner"),
                                                                  "loser": opt.get("loser")})
            effect = {"event_id": ev["event_id"], "trail": trail}
            if opt.get("profile_change"):
                prof = self.store.profile(user_id)
                declared = json.loads(json.dumps(prof["declared"]))
                ch = opt["profile_change"]
                if ch["op"] == "remove_cuisine":
                    declared["preferred_cuisines"] = [c for c in declared.get("preferred_cuisines", [])
                                                      if c != ch["value"]]
                if ch["op"] == "remove_liked_ingredient":
                    declared["liked_ingredients"] = [c for c in declared.get("liked_ingredients", [])
                                                     if c != ch["value"]]
                effect["profile"] = self.update_profile(user_id, declared, "answered_question", d["title"])
            status = "answered"
        else:
            raise ValueError(f"action '{action}' does not apply to a {d['kind']}")
        out = self.store.resolve_decision(decision_id, status, {"action": action, "answer": answer})
        self.graph.update_decision_status(decision_id, status)
        return {"decision": out, "effect": effect}

    # ---- knowledge ----------------------------------------------------------------------------
    def add_document(self, title, text):
        doc_id = self.store.add_document(title.strip(), text.strip())
        self.reindex()
        return doc_id

    def delete_document(self, doc_id):
        self.store.delete_document(doc_id)
        self.reindex()

    # ---- insight: what the system believes and why ---------------------------------------
    def insight(self, user_id):
        lr = self.learner(user_id)
        goal = self.store.goal(user_id)
        out = {"stated": goal["weights"] if goal else None, "criteria": [], "taste": [],
               "counts": None}
        if lr.choice is not None and goal:
            inf = lr.choice.inferred()
            disc = lr.choice.discrepancy(goal["weights"])
            out["counts"] = {"choices": lr.choice.n_choices, "reasons": lr.choice.n_reasons,
                             "rejections": lr.choice.n_rejections, "statements": lr.choice.n_statements}
            for k in CRITERIA:
                out["criteria"].append({
                    "criterion": k, "label": CRITERION_LABELS[k], "stated": goal["weights"][k],
                    "inferred": inf[k]["mean"], "lo": inf[k]["lo"], "hi": inf[k]["hi"],
                    "confidence": inf[k]["confidence"], "prob_differs": disc[k]["prob_beyond_delta"],
                    "direction": disc[k]["direction"]})
            logged = {e["event_id"]: e for e in self.store.events(user_id)}
            rows = []
            for e in lr.choice.evidence[-20:][::-1]:
                rid = logged.get(e.get("event_id"), {}).get("payload", {}).get("recipe_id")
                rows.append({**e, "recipe": self.catalogue.by_id[rid]["title"] if rid in self.catalogue.by_id else None,
                             "ts": logged.get(e.get("event_id"), {}).get("ts")})
            out["evidence"] = rows
        for c, m, sd, n in lr.taste.top(12):
            prov = lr.taste.prov.get(c, [])
            out["taste"].append({"concept": c, "mean": m, "sd": sd, "evidence": n,
                                 "declared": any(p["kind"] == "declared" for p in prov),
                                 "corrected": any(p["kind"] == "user_correction" for p in prov),
                                 "provenance": prov[-6:][::-1]})
        return out
