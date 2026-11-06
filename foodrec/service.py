"""Application service: the single place where state changes.

Every mutation (1) is appended to the SQLite event log, (2) is applied to the user's learner
and (3) is projected into the knowledge graph. Saved goals and mandatory constraints change
ONLY through explicit user actions (saving, or accepting a supervisor proposal); the
supervisor and the learner never write them.
"""
import hashlib
import json
import logging
import threading
import time
import uuid

from collections import Counter

from . import clock
from . import (CRITERION_LABELS, CRITERION_SPECS, INTERACTIONS, MODEL_SPEC_ERRORS, TASTE_SHARE,
               VALUE_CRITERIA)
from .access import snapshot
from .appconfig import FIELDS, AppConfig
from .catalogue import Catalogue, read_payload
from .compare import compare
from .constraints import SUPPORTED_ALLERGENS, SUPPORTED_DIETS, active_constraints, validate_declared
from .engine import public, recommend
from .features import DEFAULT_GOAL, GOALS, Scorer
from .knowledge import make_graph, make_retriever
from .knowledge.documents import build_corpus
from .learner import SCOPES, Learner
from .llm import make_llm
from .ranker import DEFAULT_WEIGHTS, resolve_weights
from .router import MODES
from .store import Store

log = logging.getLogger("foodrec.service")
# Lists that count as "shown to the user" for freshness (not previews or goal-option lists).
SHOWN_ORIGINS = ("ui", "assistant", "scenario", "cycle")
SESSION_S = 3600  # lists of the last hour do not count yet; older ones count once per clock hour
COLLECTIONS = {"saved": "save", "liked": "like", "cooked": "choose"}  # My dishes -> the feedback that adds to it
# Interface events the client reports (page views, panels opened); logged for studies only.
UI_EVENTS = ("page_view", "details_opened", "assistant_opened", "assistant_closed", "export_downloaded",
             "suggestion_clicked", "session_start", "tour_closed")
LEARNER_EVENTS = ("profile_created", "profile_updated", "goal_saved", "proposal_accepted", "feedback",
                  "profile_correction", "question_answered", "cycle_day_started")
FEEDBACK_TYPES = ("like", "save", "choose", "reject")
# configured feedback reasons ("interactions" in foodrec.config.json, model_spec.py)
REJECT_REASONS = tuple(r["id"] for r in INTERACTIONS["reject_reasons"])
CHOOSE_REASONS = tuple(r["id"] for r in INTERACTIONS["choose_reasons"])
# Discover's meal selector -> catalogue meal types (request-only filter, never saved)
MEAL_CONTEXTS = {"breakfast": ["breakfast"], "lunch": ["main", "salad", "soup", "side"],
                 "dinner": ["main", "soup", "salad"], "snack": ["snack"], "dessert": ["dessert"],
                 "drink": ["drink"]}


class App:
    def __init__(self, settings, llm=None):
        self.s = settings
        self.cfg = getattr(settings, "config", None) or AppConfig({})
        self.restart_pending = []
        self.demo_views = {}  # demo mode: visitor id -> "admin" | "user"
        self._tour_done = set()
        # what the restart-type settings were at start, as the file (not overrides) gave them
        self._started = self.resolved_settings(self.cfg)
        from .study import Sessions
        self.sessions = Sessions(settings.data_dir)
        self.lock = threading.RLock()
        self.store = Store(settings.db_path)
        self.catalogue, self.catalogue_import = self._load_catalogue(settings.catalogue_path)
        self.scaler = Scorer(self.catalogue, getattr(settings, "min_nutrient_coverage", 0.6))
        self.llm = llm if llm is not None else make_llm(settings)
        self.graph, self.graph_note = make_graph(settings)
        self.retriever, self.retriever_note = make_retriever(settings, self.llm)
        self.learners = {}
        self.score_cache = {}
        self._project_graph()
        self.reindex()

    # ---- startup projections ----------------------------------------------------------
    def _load_catalogue(self, path):
        """Import the portable catalogue file into SQLite (skipped if that version is already
        stored), then load the recipes from the database. Without a file the stored copy is used."""
        status = "missing_file"
        if path.exists():
            status = self.store.import_catalogue(read_payload(path))
        payload = self.store.load_catalogue()
        if payload is None:
            raise FileNotFoundError(f"no catalogue: {path} does not exist and the database holds none. "
                                    "Build it with: python -m foodrec.ingest")
        log.info("catalogue %s (%s, %d recipes)", status, payload["meta"]["version"], len(payload["recipes"]))
        return Catalogue.from_payload(payload), status

    def _project_graph(self):
        t0 = time.perf_counter()
        self.graph.load_catalogue(self.catalogue, self.scaler)
        for ev in self.store.events():
            self.graph.record_event(ev)
        for uid in self.store.users():
            for d in reversed(self.store.decisions(uid, limit=500)):
                self.graph.record_decision(d)
        log.info("graph projected in %.0f ms (%s)", (time.perf_counter() - t0) * 1000, self.graph_note)

    def reindex(self):
        chunks = build_corpus(self.catalogue, self.s.knowledge_dir, self.store.documents())
        return self.retriever.build(chunks)

    def system(self):
        st = self.llm.status(refresh=True) if hasattr(self.llm, "status") else {"available": False}
        return {"llm": st, "graph": self.graph_note, "graph_stats": self.graph.stats(),
                "retrieval": {**self.retriever.description, "note": self.retriever_note},
                "catalogue": {"name": self.catalogue.name, "version": self.catalogue.version,
                              "recipes": len(self.catalogue), "note": self.catalogue.note,
                              "coverage": self.catalogue.coverage(), "meta": self.catalogue.meta,
                              "import": self.catalogue_import},
                "scaler": self.scaler.version, "scoring": self.scaler.describe(), "modes": MODES,
                "options": {"allergens": SUPPORTED_ALLERGENS, "diets": SUPPORTED_DIETS,
                            "cuisines": sorted({r["cuisine"] for r in self.catalogue.recipes}),
                            "meals": sorted({r.get("meal", "main") for r in self.catalogue.recipes}),
                            "meal_contexts": self._meal_options(),
                            "ingredients": self._common_ingredients(),
                            "health_goals": [{"id": k, "label": v["label"]} for k, v in GOALS.items()],
                            "reject_reasons": REJECT_REASONS, "choose_reasons": CHOOSE_REASONS,
                            "feedback_scopes": SCOPES},
                "criteria": CRITERION_LABELS, "value_criteria": VALUE_CRITERIA, "taste_share": TASTE_SHARE,
                "top_k": self.s.top_k, "hide_saved": bool(self.s.hide_saved),
                "criteria_specs": list(CRITERION_SPECS.values()), "interactions": INTERACTIONS,
                "default_weights": DEFAULT_WEIGHTS, "model_spec_errors": MODEL_SPEC_ERRORS,
                "study": {k: self.cfg.study.get(k) for k in ("name", "description", "registration")},
                "config": {"path": str(self.cfg.path) if self.cfg.path else None, "version": self.cfg.version,
                           "restart_pending": self.restart_pending}}

    def _meal_options(self):
        """The meals Discover offers: all of them, or only the cycle's when cycles are on."""
        if self._demo():
            from .appconfig import DEMO_CYCLE_MEALS
            return {m: MEAL_CONTEXTS[m] for m in DEMO_CYCLE_MEALS}
        if getattr(self.s, "cycle_enabled", False):
            meals = [m.strip() for m in str(self.s.cycle_meals).split(",") if m.strip() in MEAL_CONTEXTS]
            if meals:
                return {m: MEAL_CONTEXTS[m] for m in meals}
        return MEAL_CONTEXTS

    # ---- access roles and study registration ---------------------------------------------
    def access(self, user_id):
        """Role, features and visible criteria of a user (see appconfig.AppConfig.access). In demo
        mode the role is the view the visitor picked: the admin role or the full user role."""
        if self._demo():
            from .appconfig import ADMIN_ROLE
            link = self.store.demo_link(user_id=user_id)
            if link and link["role"] in self.cfg.roles:
                acc = self.cfg.access_for_role(link["role"], "study link")
            elif self.demo_views.get(user_id) == "admin" and ADMIN_ROLE in self.cfg.roles:
                acc = self.cfg.access_for_role(ADMIN_ROLE, "demo view")
            else:
                role = "full" if "full" in self.cfg.roles else self.cfg.data["access"].get("default_role")
                acc = self.cfg.access_for_role(role, "demo view")
        else:
            acc = self.cfg.access(user_id, self.store.assigned_role(user_id))
        acc["show_similarity"] = bool(self.setting(user_id, "show_similarity_score", True))
        return acc

    def set_demo_view(self, user_id, view):
        if view not in ("admin", "user"):
            raise ValueError("view must be admin or user")
        if view == "admin" and self.store.demo_link(user_id=user_id):
            raise PermissionError("a study link opens the app as a participant")
        self.demo_views[user_id] = view
        return self.access(user_id)

    # ---- settings per user ---------------------------------------------------------------------
    def _demo(self):
        return getattr(self.s, "server_mode", "open") == "demo"

    def _visitor_overrides(self, user_id):
        """Demo only: what this visitor set for themselves; people who came through a visitor's
        link use that visitor's settings. Nobody else is affected and the config file stays as it is."""
        if not self._demo():
            return {}
        link = self.store.demo_link(user_id=user_id)
        return self.store.visitor_settings(link["owner"] if link else user_id)

    def thresholds(self, user_id):
        from .agent.policy import thresholds
        return thresholds(self, user_id)

    def setting(self, user_id, attr, default=None):
        """A runtime setting as it applies to this user."""
        over = self._visitor_overrides(user_id)
        return over[attr] if attr in over else getattr(self.s, attr, default)

    def save_visitor_settings(self, user_id, values):
        """Demo: {"supervisor.ask_gap": 0.2, ...}; only settings marked `visitor`. A value of null
        goes back to the demo's value. Returns the visitor's settings by attribute."""
        from .appconfig import coerce, threshold_problems
        fields = {f"{f['section']}.{f['key']}": f for f in FIELDS if f.get("visitor")}
        data = self.store.visitor_settings(user_id)
        for name, value in (values or {}).items():
            f = fields.get(name)
            if f is None:
                raise ValueError(f"{name} can't be changed in the demo")
            if value is None:
                data.pop(f["attr"], None)
            else:
                data[f["attr"]] = coerce(f, value)
        errs = threshold_problems(lambda k: data.get(k, getattr(self.s, k, None)))
        if errs:
            raise ValueError("; ".join(errs))
        self.store.save_visitor_settings(user_id, data)
        return data

    # ---- access links --------------------------------------------------------------------------
    def demo_population(self, owner, population):
        """Demo: people for a visitor's study, each with a personal link and no username or password.
        Stored in the database, not the config file, and replaced when the visitor generates again."""
        from .study import generate
        parts, counts = generate({**population, "access": "link"})
        links = [{"token": p["link"], "user_id": "visitor-" + uuid.uuid4().hex[:12], "label": p["username"],
                  "role": p["role"]} for p in parts]
        self.store.replace_demo_links(owner, links)
        return {"participants": [{"username": x["label"], "link": x["token"], "role": x["role"]} for x in links],
                "counts": counts}

    # ---- meal cycles ----------------------------------------------------------------------------
    # meals x recommendations x days reactions: the user sees r dishes for one meal, reacts to each,
    # moves to the next meal, and after the last meal starts the next day.
    def _guided(self, user_id):
        """Does this user follow the guided path? The account's own setting wins; people with a link
        are guided unless the administrator chose otherwise; the rest follow cycle.enabled."""
        p = self.cfg.participant(user_id) or {}
        flow = p.get("flow") or ("guided" if p.get("link") else None)
        return flow == "guided" if flow else bool(getattr(self.s, "cycle_enabled", False))

    def cycle_config(self, user_id):
        acc = self.access(user_id)
        if self._demo():
            from .appconfig import DEMO_CYCLE_MEALS
            enabled, meals = True, list(DEMO_CYCLE_MEALS)
        else:
            enabled = self._guided(user_id)
            meals = [m.strip() for m in str(getattr(self.s, "cycle_meals", "")).split(",") if m.strip() in MEAL_CONTEXTS]
        r, n = int(self.setting(user_id, "cycle_recommendations", 5)), int(self.setting(user_id, "cycle_days", 3))
        # a role without feedback buttons could never finish a meal
        return {"enabled": enabled and bool(meals) and acc["features"]["feedback"], "meals": meals,
                "recommendations": r, "days": n, "total": len(meals) * r * n}

    def _cycle_open_step(self, user_id, round_, day, mi, meals, r):
        filt = [{"field": "meal", "op": "is", "value": MEAL_CONTEXTS[meals[mi]], "source": f"{meals[mi]} context"}]
        res = self.recommend(user_id, {"request_filters": filt, "k": r}, origin="cycle")
        self.store.add_cycle_step(user_id, round_, day, mi, meals[mi], res["request_id"])
        return res

    def cycle_state(self, user_id):
        """Where the user is in the cycle, with the list of the current meal (made once and kept, so
        reloading the page never changes it)."""
        cfg = self.cycle_config(user_id)
        if not cfg["enabled"]:
            return cfg
        meals, r = cfg["meals"], cfg["recommendations"]
        with self.lock:
            steps = self.store.cycle_steps(user_id)
            round_ = steps[-1]["round"] if steps else 1
            steps = [s for s in steps if s["round"] == round_]
            cur = steps[-1] if steps else None
            res = None
            if cur is None:
                res = self._cycle_open_step(user_id, round_, 1, 0, meals, r)
                cur = self.store.cycle_steps(user_id, round_)[-1]
            elif cur["done_at"] is not None:
                # the last step of the round was finished: the round is complete
                return {**cfg, "round": round_, "finished": True, "day": cur["day"], "meal": None, "meal_index": None,
                        "answered_total": self._cycle_answered(user_id, steps),
                        "remaining": 0, "result": None, "answered": {}, "complete": True}
            res = res or self.store.request(cur["request_id"])
        shown = {x["recipe_id"] for x in res.get("results") or []}
        answered = {a["recipe_id"]: a["type"] for a in self.store.cycle_answers(user_id, cur["request_id"])
                    if a["recipe_id"] in shown}
        return {**cfg, "round": round_, "finished": False, "day": cur["day"], "meal": cur["meal"],
                "meal_index": meals.index(cur["meal"]) if cur["meal"] in meals else cur["meal_index"],
                "answered_total": self._cycle_answered(user_id, steps), "result": res, "answered": answered,
                "remaining": len(shown - set(answered)), "complete": not (shown - set(answered))}

    def _cycle_answered(self, user_id, steps):
        ids = {s["request_id"] for s in steps}
        return sum(1 for a in self.store.cycle_answers(user_id) if a["request_id"] in ids)

    def cycle_next(self, user_id):
        """Close the current meal (every dish must have a reaction) and open the next one."""
        st = self.cycle_state(user_id)
        if not st["enabled"]:
            raise ValueError("meal cycles are off")
        if st["finished"]:
            return st
        if not st["complete"]:
            raise ValueError(f"React to the {st['remaining']} remaining dish(es) first.")
        meals, r = st["meals"], st["recommendations"]
        with self.lock:
            cur = self.store.cycle_steps(user_id, st["round"])[-1]
            if cur["done_at"] is None:
                self.store.finish_cycle_step(user_id, st["round"], cur["day"], cur["meal_index"])
                day, mi = cur["day"], cur["meal_index"] + 1
                if mi >= len(meals):
                    day, mi = day + 1, 0
                    if day <= st["days"]:
                        self._emit(user_id, "cycle_day_started", {"round": st["round"], "day": day})
                if day <= st["days"]:
                    self._cycle_open_step(user_id, st["round"], day, mi, meals, r)
        return self.cycle_state(user_id)

    def cycle_restart(self, user_id):
        """A finished cycle can be walked again; the reactions already given stay recorded."""
        st = self.cycle_state(user_id)
        if not st["enabled"]:
            raise ValueError("meal cycles are off")
        if not st["finished"]:
            raise ValueError("finish this cycle first")
        with self.lock:
            self._emit(user_id, "cycle_day_started", {"round": st["round"] + 1, "day": 1})
            self._cycle_open_step(user_id, st["round"] + 1, 1, 0, st["meals"], st["recommendations"])
        return self.cycle_state(user_id)

    def _cycle_mark(self, user_id, request_id, recipe_id, ftype):
        """A reaction to a dish of the current meal counts, even when the click is a repeat (a dish
        that is already in Liked can still appear in a list)."""
        if not request_id:
            return
        steps = self.store.cycle_steps(user_id)
        if any(s["request_id"] == request_id and s["done_at"] is None for s in steps):
            self.store.add_cycle_answer(user_id, request_id, recipe_id, ftype)

    def _is_admin(self, user_id):
        return self.access(user_id)["features"]["configure"]

    def registration(self, user_id):
        """May this user (still) complete onboarding? {"allowed": bool, "reason": str|None}."""
        if self.store.profile(user_id) or self._is_admin(user_id):
            return {"allowed": True, "reason": None}
        st = self.cfg.study
        listed = user_id in (self.cfg.data["access"].get("users") or {}) or self.cfg.participant(user_id) is not None
        name = st.get("name") or "this study"
        reg = st.get("registration", "open")
        if reg == "closed":
            return {"allowed": False, "reason": f"Registration for {name} is closed."}
        if reg == "listed_only" and not listed:
            return {"allowed": False, "reason": f"The user id '{user_id}' is not registered for {name}. "
                                                "Check the id you were given."}
        mu = st.get("max_users")
        if mu and not listed:
            n = sum(1 for u in self.store.onboarded_users() if not self._is_admin(u))
            if n >= mu:
                return {"allowed": False, "reason": f"{name[0].upper() + name[1:]} is full ({mu} participants)."}
        return {"allowed": True, "reason": None}

    def _assign_role(self, user_id):
        """Store a role for a new participant when the study assigns roles (round robin or
        seeded random). Users named in access.users and the "default" method store nothing."""
        cfg = self.cfg
        if user_id in (cfg.data["access"].get("users") or {}) or cfg.participant(user_id) or \
                self.store.assigned_role(user_id):
            return
        st = cfg.study
        method = st.get("assignment", "default")
        roles = [r for r in st.get("assign_roles") or [] if r in cfg.roles]
        if method == "round_robin" and roles:
            counts = self.store.role_counts()
            role = min(roles, key=lambda r: (counts.get(r, 0), roles.index(r)))
        elif method == "random" and roles:
            digest = hashlib.sha256(f"{st.get('seed', 7)}:{user_id}".encode()).digest()
            role = roles[int.from_bytes(digest[:4], "big") % len(roles)]
        else:
            return
        self.store.assign_role(user_id, role, method)

    def set_role(self, user_id, role):
        """Admin: store a role for a user (an entry in access.users still takes precedence)."""
        if role not in self.cfg.roles:
            raise ValueError(f"unknown role '{role}'")
        self.store.assign_role(user_id, role, "admin")
        return self.access(user_id)

    @staticmethod
    def resolved_settings(cfg):
        """{Settings attr: value} that `cfg` gives (file value, else environment/default)."""
        from .config import Settings
        from .appconfig import coerce, resolve_path
        fresh = Settings()
        out = {}
        for f in FIELDS:
            v = coerce(f, cfg.value(f["section"], f["key"]))
            out[f["attr"]] = getattr(fresh, f["attr"]) if v is None else (resolve_path(v) if f["type"] == "path" else v)
        return out

    def apply_config(self, cfg):
        """Use a newly saved config: access and study rules and "live" settings apply at once.
        Returns the changes that need a restart: restart-type settings that differ from what
        this process STARTED with (so command-line overrides never count), and the criteria /
        interactions sections, which are fixed when the package is imported."""
        import foodrec
        from .model_spec import fingerprint, normalize
        new = self.resolved_settings(cfg)
        pending = []
        for f in FIELDS:
            v, attr = new[f["attr"]], f["attr"]
            if f["apply"] == "live":
                if getattr(self.s, attr) != v:
                    setattr(self.s, attr, v)
                    self.s.sources[attr] = "config"
            elif v != self._started[attr]:
                pending.append({"setting": f"{f['section']}.{f['key']}", "label": f["label"],
                                "running": str(getattr(self.s, attr)), "saved": str(v)})
        spec, errors = normalize(cfg.raw.get("criteria"), cfg.raw.get("interactions"))
        if not errors and fingerprint(spec) != fingerprint(foodrec.MODEL_SPEC):
            pending.append({"setting": "criteria / interactions", "label": "Criteria and interaction options",
                            "running": "as at start", "saved": "changed"})
        if hasattr(self.llm, "provider") and hasattr(self.llm, "s"):  # a real client, not a test double
            if self.llm.provider != self.s.llm_provider or self.llm.model != self.s.llm_model:
                self.llm = make_llm(self.s)  # the retriever keeps embedding through its own client
            else:
                self.llm._status = None
        self.cfg = cfg
        self.s.config = cfg
        self.restart_pending = pending
        return pending

    # ---- numeric recipe fields (for configuring field criteria) ------------------------------
    def numeric_fields(self, min_share=0.3):
        """Dotted paths of numeric recipe fields present in at least `min_share` of recipes."""
        if not hasattr(self, "_num_fields"):
            from collections import Counter
            c = Counter()

            def walk(d, prefix):
                for k, v in d.items():
                    p = f"{prefix}{k}"
                    if isinstance(v, bool):
                        continue
                    if isinstance(v, (int, float)):
                        c[p] += 1
                    elif isinstance(v, dict) and p.count(".") < 2:
                        walk(v, p + ".")
            sample = self.catalogue.recipes[:: max(1, len(self.catalogue) // 2000)]
            for r in sample:
                walk(r, "")
            skip = {"source_id", "pos"}
            self._num_fields = sorted(p for p, n in c.items() if n >= min_share * len(sample) and p not in skip)
        return self._num_fields

    def field_stats(self, path):
        """Distribution of one numeric field over the catalogue (to choose best/worst anchors)."""
        from .model_spec import extract
        vals = sorted(v for v in (extract(r, path) for r in self.catalogue.recipes) if v is not None)
        if not vals:
            return {"field": path, "count": 0, "of": len(self.catalogue)}
        q = lambda p: round(vals[min(len(vals) - 1, int(p * (len(vals) - 1)))], 3)  # noqa: E731
        return {"field": path, "count": len(vals), "of": len(self.catalogue), "min": round(vals[0], 3),
                "p10": q(0.10), "p25": q(0.25), "median": q(0.5), "p75": q(0.75), "p90": q(0.90),
                "max": round(vals[-1], 3)}

    def log_ui_event(self, user_id, kind, data=None):
        if kind not in UI_EVENTS:
            raise ValueError(f"ui event must be one of {UI_EVENTS}")
        ev = self.store.append_event(user_id, "ui", {"kind": kind, **(data or {}),
                                                     "role": self.access(user_id)["role"]})
        return {"event_id": ev["event_id"]}

    def _tour_seen(self, user_id, side="user"):
        """Has this user closed the guided tour of this side (user or admin)? Kept with the user, not
        the browser, so each tour is shown once, at the first login, on whichever device that is."""
        if (user_id, side) in self._tour_done:
            return True
        seen = any(e["payload"].get("kind") == "tour_closed" and e["payload"].get("side", "user") == side
                   for e in self.store.events(user_id, types=["ui"]))
        if seen:
            self._tour_done.add((user_id, side))
        return seen

    def _common_ingredients(self, min_recipes=5):
        if not hasattr(self, "_ing_vocab"):
            c = Counter(i["name"] for r in self.catalogue.recipes for i in r["ingredients"])
            self._ing_vocab = sorted(n for n, k in c.items() if k >= min_recipes)
        return self._ing_vocab

    def search_recipes(self, q="", limit=40, ids=None, meal=None):
        if ids:
            return [self.catalogue.by_id[i] for i in ids if i in self.catalogue.by_id]
        q = (q or "").strip().lower()
        out = []
        for r in self.catalogue.recipes:
            if meal and r.get("meal") != meal:
                continue
            if not q or q in r["title"].lower() or any(q == i["name"] for i in r["ingredients"]):
                out.append(r)
                if len(out) >= limit:
                    break
        return out

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
            # Build (or replay) the learner BEFORE logging, so a fresh replay does not see the new
            # event and then apply it a second time.
            lr = self.learner(user_id) if type_ in LEARNER_EVENTS else None
            ev = self.store.append_event(user_id, type_, payload, context)
            trail = []
            if lr is not None:
                trail = lr.apply(self._learner_event(ev))
            self.graph.record_event(ev)
            return ev, trail

    # ---- profile & goal ----------------------------------------------------------------
    def state(self, user_id):
        prof = self.store.profile(user_id)
        goal = self.store.goal(user_id)
        return {"user_id": user_id, "onboarded": prof is not None,
                "access": self.access(user_id), "registration": self.registration(user_id),
                "profile": prof, "goal": goal,
                "thresholds": self.thresholds(user_id), "cycle": self.cycle_config(user_id),
                "tour_seen": self._tour_seen(user_id), "tour_seen_admin": self._tour_seen(user_id, "admin"),
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
               "max_ingredients": d.get("max_ingredients") or None,
               "health_goal": d.get("health_goal") if d.get("health_goal") in GOALS else DEFAULT_GOAL}
        validate_declared(out)
        return out

    def onboard(self, user_id, declared, weights=None, diversity=None, mode=None):
        reg = self.registration(user_id)
        if not reg["allowed"]:
            raise PermissionError(reg["reason"])
        diversity = self.s.default_diversity if diversity is None else diversity
        mode = mode or self.s.default_mode
        with self.lock:
            self._assign_role(user_id)
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
    def today_weights(self, user_id, base, now=None):
        """Saved weights with today's temporary adjustments ("only today" answers and feedback).
        Returns (weights, note) where note is None when nothing applies."""
        temp = self.learner(user_id).temporary_effects(now or clock.now())
        if not temp["weights"] and not temp["nudges"]:
            return dict(base), None
        w = dict(temp["weights"] or base)
        for k, v in temp["nudges"].items():
            w[k] = max(0.0, w.get(k, 0.0) + v)
        w, _ = resolve_weights(w, base)
        parts = [f"{CRITERION_LABELS[k].lower()} {'+' if v > 0 else ''}{round(100 * v)} pts"
                 for k, v in temp["nudges"].items()]
        note = "adjusted for today" + (f" ({', '.join(parts)})" if parts else " (weights you chose for today)")
        return w, note

    def recommend(self, user_id, overrides=None, origin="ui", save=True, use_temporary=True):
        """use_temporary=False ranks with exactly the saved (or given) weights, ignoring today's
        adjustments; counterfactual previews use it so they compare like with like."""
        overrides = overrides or {}
        now = clock.now()
        prof = self.store.profile(user_id) or {"version": 0, "declared": {}}
        goal = self.store.goal(user_id) or {"version": 0, "weights": dict(DEFAULT_WEIGHTS),
                                            "diversity": 0.3, "mode": "lightweight"}
        weights, today_note = overrides.get("weights"), None
        if not weights:
            weights = goal["weights"]
            if use_temporary:
                weights, today_note = self.today_weights(user_id, goal["weights"], now)
        cfg = {"weights": weights,
               "health_goal": prof["declared"].get("health_goal") or DEFAULT_GOAL,
               "diversity": goal["diversity"] if overrides.get("diversity") is None else overrides["diversity"],
               "mode": overrides.get("mode") or goal["mode"],
               "request_filters": overrides.get("request_filters") or [],
               "last_valid_weights": goal["weights"], "k": int(overrides.get("k") or getattr(self.s, "top_k", 5)),
               "max_missing_share": getattr(self.s, "max_missing_share", 0.5),
               "freshness": self.freshness(user_id, now)}
        with self.lock:
            res = public(recommend(self.catalogue, self.scaler, self.learner(user_id), prof["declared"], cfg,
                                   now=now, cache=self.score_cache))
        res.update({"origin": origin, "user_id": user_id, "access": snapshot(self.access(user_id)),
                    "versions": {**res["versions"], "profile": prof["version"], "goal": goal["version"]},
                    "goal_weights": goal["weights"],
                    "weights_source": f"saved goal v{goal['version']}, {today_note}" if today_note else
                    f"saved goal v{goal['version']}" if all(
                        abs(res["config"]["values_applied"][k] - goal["weights"][k]) < 1e-6 for k in VALUE_CRITERIA)
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
        base = self.recommend(user_id, origin="whatif_base", save=False, use_temporary=False)
        alt = self.recommend(user_id, {"weights": weights, "diversity": diversity, "mode": mode},
                             origin="whatif", save=False, use_temporary=False)
        if not base.get("results") or not alt.get("results"):
            return {"base": base, "alternative": alt, "comparison": None}
        return {"base": base, "alternative": alt, "comparison": compare(base, alt)}

    # ---- several small goal changes to choose from ---------------------------------------------
    def goal_option_weights(self, weights, increase, decrease=None, max_drop=0.10, steps=(0.05, 0.10, 0.15)):
        """Small, normalised weight adjustments: `increase` gains a step; with `decrease` given it
        pays at most `max_drop` of it and the third criterion pays the rest, otherwise both others
        pay in proportion. Returns a list of weight dicts (sum 1, all >= 0), smallest change first."""
        if increase not in VALUE_CRITERIA or (decrease is not None and decrease not in VALUE_CRITERIA):
            raise ValueError("criteria must be h, s or t")
        if decrease == increase:
            raise ValueError("increase and decrease must differ")
        w = {k: float(weights[k]) for k in VALUE_CRITERIA}
        others = [k for k in VALUE_CRITERIA if k != increase]
        out, seen = [], set()
        for step in steps:
            step = min(step, 1.0 - w[increase])
            if step <= 1e-9:
                continue
            if decrease is None:
                pool = sum(w[k] for k in others)
                if pool <= 0:
                    continue
                cand = dict(w)
                cand[increase] += step
                for k in others:
                    cand[k] -= step * w[k] / pool
                splits = [cand]
            else:
                third = next(k for k in others if k != decrease)
                splits = []
                for share in (0.5, 1.0):
                    drop = min(step * share, max_drop, w[decrease])
                    rest = step - drop
                    if rest > w[third] + 1e-9:
                        continue
                    cand = dict(w)
                    cand[increase] += step
                    cand[decrease] -= drop
                    cand[third] -= rest
                    splits.append(cand)
            for cand in splits:
                cand = {k: max(0.0, v) for k, v in cand.items()}
                tot = sum(cand.values())
                cand = {k: v / tot for k, v in cand.items()}
                key = tuple(round(cand[k], 4) for k in VALUE_CRITERIA)
                if key not in seen:
                    seen.add(key)
                    out.append(cand)
        return out[:4]

    def goal_options(self, user_id, increase, decrease=None, max_drop=0.10, source="assistant"):
        """Preview a few small goal changes with the lists they would produce, and store them as one
        decision. Nothing is saved until the user picks an option (resolve_decision accept)."""
        goal = self.store.goal(user_id)
        if goal is None:
            raise ValueError("complete onboarding first")
        cands = self.goal_option_weights(goal["weights"], increase, decrease, max_drop)
        base = self.recommend(user_id, origin="options_base", save=False, use_temporary=False)

        def summary(res):
            rs = res.get("results") or []
            avg = {k: (sum(r["scores"][k] for r in rs if r["scores"].get(k) is not None) /
                       max(1, sum(1 for r in rs if r["scores"].get(k) is not None))) for k in VALUE_CRITERIA}
            return {"recipes": [{"recipe_id": r["recipe_id"], "title": r["title"]} for r in rs],
                    "avg_scores": avg}

        b = summary(base)
        options = []
        for i, w in enumerate(cands, 1):
            alt = self.recommend(user_id, {"weights": w}, origin="options", save=False, use_temporary=False)
            a = summary(alt)
            cmp_ = compare(base, alt) if base.get("results") and alt.get("results") else None
            changes = [f"{CRITERION_LABELS[k]} {round(100 * goal['weights'][k])}% to {round(100 * w[k])}%"
                       for k in VALUE_CRITERIA if abs(w[k] - goal["weights"][k]) >= 0.005]
            options.append({"id": f"o{i}", "label": ", ".join(changes), "weights": w,
                            "recipes": a["recipes"], "avg_scores": a["avg_scores"],
                            "avg_change": {k: a["avg_scores"][k] - b["avg_scores"][k] for k in VALUE_CRITERIA},
                            "entered": (cmp_ or {}).get("entered", []), "left": (cmp_ or {}).get("left", [])})
        inc = CRITERION_LABELS[increase].lower()
        title = f"More {inc}" + (f", with only a small reduction in {CRITERION_LABELS[decrease].lower()}"
                                 if decrease else "") + ": pick one"
        d = self.add_decision(user_id, "goal_options", title, {
            "topic": f"options:{increase}:{decrease}", "criterion": increase, "decrease": decrease,
            "max_drop": max_drop, "stated": dict(goal["weights"]),
            "base": {"weights": dict(goal["weights"]), **b}, "options": options,
            "explanation": (f"{len(options)} small changes to your saved goal, each with the five recipes it would give. "
                            + (f"{CRITERION_LABELS[decrease]} drops by at most {round(100 * max_drop)} points. "
                               if decrease else "")
                            + "Nothing is saved until you pick one; you can also use one for today only.")}, source)
        return d

    # ---- feedback ---------------------------------------------------------------------------
    def feedback(self, user_id, request_id, recipe_id, ftype, reason=None, context=None, scope="lasting"):
        """scope "today": a temporary circumstance; it adjusts today's lists but never the models.
        A repeated click (same button, same recipe, same list) is not logged again."""
        if ftype not in FEEDBACK_TYPES:
            raise ValueError(f"feedback type must be one of {FEEDBACK_TYPES}")
        allowed = {"choose": CHOOSE_REASONS, "reject": REJECT_REASONS}.get(ftype, ())
        if reason is not None and reason not in allowed:
            raise ValueError(f"reason '{reason}' is not configured for {ftype}" +
                             (f" (use one of {', '.join(allowed)})" if allowed else ""))
        if recipe_id not in self.catalogue.by_id:
            raise ValueError(f"unknown recipe {recipe_id}")
        scope = scope or "lasting"
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}")
        pl = {"type": ftype, "recipe_id": recipe_id, "reason": reason, "request_id": request_id, "scope": scope,
              "role": self.access(user_id)["role"]}
        with self.lock:
            self._cycle_mark(user_id, request_id, recipe_id, ftype)
            if ftype in ("save", "like"):
                # duplicate = already in Saved / Liked. After removing it from that list, saving or
                # liking again puts it back (the learner still counts its taste only once).
                if recipe_id in self.dish_ids(user_id, "saved" if ftype == "save" else "liked"):
                    return {"event_id": None, "duplicate": True, "trail": []}
            elif self.learner(user_id).is_duplicate(pl):
                return {"event_id": None, "duplicate": True, "trail": []}
            req = self.store.request(request_id) if request_id else None
            pl["shown"] = [{"recipe_id": r["recipe_id"], "scores": r["scores"]}
                           for r in (req or {}).get("results", [])]
            ev, trail = self._emit(user_id, "feedback", pl, context)
        return {"event_id": ev["event_id"], "duplicate": False, "scope": scope, "trail": trail}

    # ---- freshness: what the user saw recently -------------------------------------------------
    def freshness(self, user_id, now=None):
        """Inputs for the engine's freshness step (engine.py), from the lists stored for this user.

        A list counts as seen once its session (SESSION_S) is over, so the list stays stable while
        the user works with it. Per recipe, every past session in which it was shown and got no
        choice or rejection adds fatigue_per_view x 0.5 ** (age / half-life), capped; per cuisine,
        every list place it filled adds cuisine_fatigue_per_slot the same way ("Other" excluded)."""
        s, now = self.s, now or clock.now()
        half = max(0.01, float(s.fatigue_half_life_days))
        reqs = [q for q in self.store.requests(user_id, since=now - float(s.freshness_window_days) * 86400)
                if q["origin"] in SHOWN_ORIGINS and q["result"].get("results")]
        reacted = {(e["payload"].get("request_id"), e["payload"].get("recipe_id"))
                   for e in self.store.events(user_id, since=now - float(s.freshness_window_days) * 86400,
                                              types=["feedback"])
                   if e["payload"].get("type") in ("choose", "reject")}
        views, cuisine, counted, sessions = {}, {}, set(), {}
        for q in reqs:
            if q["ts"] > now - SESSION_S:  # the current session: the user is still looking at it
                continue
            b = int(q["ts"] // SESSION_S)
            decay = 0.5 ** (max(0.0, now - q["ts"]) / 86400 / half)
            sessions[b] = [r["recipe_id"] for r in q["result"]["results"]]
            for r in q["result"]["results"]:
                rid, c = r["recipe_id"], r.get("cuisine")
                if (rid, b) in counted:
                    continue
                counted.add((rid, b))
                if c and c != "Other":
                    n, w = cuisine.get(c, (0, 0.0))
                    cuisine[c] = (n + 1, w + decay)
                if (q["request_id"], rid) in reacted:
                    continue
                n, w = views.get(rid, (0, 0.0))
                views[rid] = (n + 1, w + decay)
        exposure = {rid: {"views": n, "penalty": round(min(s.fatigue_max, s.fatigue_per_view * w), 4)}
                    for rid, (n, w) in views.items() if s.fatigue_per_view > 0}
        cuis = {c: {"slots": n, "penalty": round(min(s.cuisine_fatigue_max, s.cuisine_fatigue_per_slot * w), 4)}
                for c, (n, w) in cuisine.items() if s.cuisine_fatigue_per_slot > 0}
        history = [rid for b in sorted(sessions)[-int(s.history_lists):] for rid in sessions[b]] \
            if s.history_lists > 0 else []
        out = {"exposure": exposure, "cuisine": cuis, "history": history,
               "exclude": sorted(self.dish_ids(user_id, "saved")) if s.hide_saved else []}
        if s.discovery_slots > 0:
            since = now - float(s.discovery_days) * 86400
            shown = {r.get("cuisine") for q in reqs if q["ts"] >= since for r in q["result"]["results"]}
            out["discovery"] = {"slots": int(s.discovery_slots),
                                "cuisines": sorted({r["cuisine"] for r in self.catalogue.recipes}
                                                   - shown - {"Other"})}
        return out

    # ---- My dishes: saved, liked and cooked --------------------------------------------------
    def _dish_events(self, user_id):
        """{collection: {recipe_id: {"added": ts, "count": n, "removed": ts|None, "reasons": [...]}}}."""
        out = {k: {} for k in COLLECTIONS}
        kind = {v: k for k, v in COLLECTIONS.items()}
        for e in self.store.events(user_id, types=["feedback", "dish_removed"]):
            p = e["payload"]
            if e["type"] == "dish_removed":
                item = out.get(p.get("collection"), {}).get(p.get("recipe_id"))
                if item:
                    item["removed"] = e["ts"]
                continue
            col = kind.get(p.get("type"))
            if not col or p.get("recipe_id") not in self.catalogue.by_id:
                continue
            item = out[col].setdefault(p["recipe_id"], {"first": e["ts"], "added": e["ts"], "count": 0,
                                                        "removed": None, "reasons": []})
            item["added"] = e["ts"]
            item["count"] += 1
            if p.get("reason") and p["reason"] not in item["reasons"]:
                item["reasons"].append(p["reason"])
        for col in out.values():
            for item in col.values():
                item["visible"] = item["removed"] is None or item["added"] > item["removed"]
        return out

    def dish_ids(self, user_id, collection):
        return {rid for rid, it in self._dish_events(user_id)[collection].items() if it["visible"]}

    def my_dishes(self, user_id):
        """The dishes the user wants to find again, newest first: saved for later, liked, cooked."""
        out = {}
        for col, items in self._dish_events(user_id).items():
            rows = []
            for rid, it in items.items():
                if not it["visible"]:
                    continue
                r = self.catalogue.by_id[rid]
                rows.append({"recipe_id": rid, "title": r["title"], "cuisine": r["cuisine"], "meal": r.get("meal"),
                             "image_url": r.get("image_url"), "url": r.get("url"),
                             "n_ingredients": r.get("n_ingredients"), "kcal": r.get("kcal_per_serving"),
                             "scores": self.scaler.scores(r), "ts": it["added"], "first_ts": it["first"],
                             "count": it["count"], "why": it["reasons"]})
            out[col] = sorted(rows, key=lambda x: -x["ts"])
        return out

    def remove_dish(self, user_id, recipe_id, collection):
        """Take a dish off a My dishes list. Nothing that was learned changes."""
        if collection not in COLLECTIONS:
            raise ValueError(f"collection must be one of {', '.join(COLLECTIONS)}")
        if recipe_id not in self.dish_ids(user_id, collection):
            raise ValueError("that dish is not on this list")
        ev, _ = self._emit(user_id, "dish_removed", {"recipe_id": recipe_id, "collection": collection})
        return {"event_id": ev["event_id"]}

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
        elif action == "accept" and d["kind"] == "goal_options":
            opt = next((o for o in body["options"] if o["id"] == answer), None)
            if opt is None:
                raise ValueError("pick one of the options")
            goal = self.store.goal(user_id)
            effect = self.save_goal(user_id, goal["name"], opt["weights"], goal["diversity"],
                                    goal["mode"], "accepted_proposal", f"{d['title']} ({opt['label']})", decision_id)
            status = "accepted"
        elif action == "answer" and d["kind"] == "goal_proposal" and answer == "keep_goal":
            ev, trail = self._emit(user_id, "question_answered", {"decision_id": decision_id, "answer": answer,
                                                                  "scope": "keep_goal"})
            effect = {"event_id": ev["event_id"], "trail": trail}
            status = "dismissed"
        elif action == "answer" and d["kind"] in ("goal_proposal", "goal_options") and str(answer).startswith("today"):
            if d["kind"] == "goal_proposal":
                w = body["proposed_weights"]
            else:
                opt = next((o for o in body["options"] if o["id"] == str(answer).partition(":")[2]), None)
                if opt is None:
                    raise ValueError("unknown option for today")
                w = opt["weights"]
            ev, trail = self._emit(user_id, "question_answered", {"decision_id": decision_id, "answer": answer,
                                                                  "scope": "today", "weights": w})
            effect = {"event_id": ev["event_id"], "trail": trail, "weights_today": w}
            status = "answered"
        elif action == "accept" and d["kind"] == "profile_change":
            effect = self.update_profile(user_id, body["proposed_declared"], "accepted_proposal", d["title"])
            status = "accepted"
        elif action == "answer" and d["kind"] in ("tradeoff_question", "taste_question"):
            opt = next((o for o in body["options"] if o["id"] == answer), None)
            if opt is None:
                raise ValueError("unknown answer option")
            ev, trail = self._emit(user_id, "question_answered", {"decision_id": decision_id, "answer": answer,
                                                                  "winner": opt.get("winner"),
                                                                  "loser": opt.get("loser"),
                                                                  "scope": opt.get("scope", "lasting"),
                                                                  "nudge": opt.get("nudge")})
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
        """Stated vs observed weights per criterion, preference match, taste beliefs."""
        lr = self.learner(user_id)
        goal = self.store.goal(user_id)
        prof = self.store.profile(user_id)
        out = {"stated": None, "observed": None, "criteria": [], "preference_match": None, "confidence": None,
               "counts": None, "taste": [], "evidence": [],
               "health_goal": (prof or {}).get("declared", {}).get("health_goal", DEFAULT_GOAL),
               "taste_share": TASTE_SHARE}
        if lr.values is not None and goal:
            vm = lr.values
            stated = {k: goal["weights"][k] for k in VALUE_CRITERIA}
            out.update({"stated": stated, "observed": dict(vm.observed), "preference_match": vm.match(stated),
                        "confidence": vm.confidence(), "events_since_goal": vm.n, "counts": dict(vm.counts)})
            for k in VALUE_CRITERIA:
                out["criteria"].append({"criterion": k, "label": CRITERION_LABELS[k], "stated": stated[k],
                                        "observed": vm.observed[k], "gap": vm.observed[k] - stated[k],
                                        "closeness": 1.0 - abs(vm.observed[k] - stated[k])})
            logged = {e["event_id"]: e for e in self.store.events(user_id)}
            for e in vm.evidence[-20:][::-1]:
                rid = logged.get(e.get("event_id"), {}).get("payload", {}).get("recipe_id")
                out["evidence"].append({**e, "recipe": self.catalogue.by_id[rid]["title"]
                                        if rid in self.catalogue.by_id else None,
                                        "ts": logged.get(e.get("event_id"), {}).get("ts")})
        for c, m, n in lr.taste.top(12):
            prov = lr.taste.prov.get(c, [])
            out["taste"].append({"concept": c, "mean": m, "evidence": n, **lr.taste.counts(c),
                                 "declared": any(p["kind"] == "declared" for p in prov),
                                 "corrected": any(p["kind"] == "user_correction" for p in prov),
                                 "provenance": prov[-6:][::-1]})
        return out
