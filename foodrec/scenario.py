"""Run scripted scenarios against the app, without the browser.

    foodrec scenario run scenarios/sara.json
    foodrec scenario run scenarios/*.json --set llm.model=qwen3:4b --out out/scenarios
    foodrec scenario run scenarios/sara.json --matrix llm.model=qwen3.5:2b-q4_K_M,qwen3:4b

A scenario is a JSON file:
    {"name": "...", "description": "...", "user": "sara",
     "config": {...},            # merged over the config file (e.g. "llm": {"enabled": false})
     "steps": [{"action": "onboard", ...}, {"action": "recommend"}, {"action": "expect", ...}]}

Each run starts from an empty database in a fresh folder (unless --data-dir is given), with the
in-process graph and vector index, so runs are repeatable and never touch the live data.
The language model is whatever the config says; without Ollama the app degrades to rules.
Actions are listed in ACTIONS below and documented in docs/configuration.md#scenarios.

Output (per scenario and variant): report.json (every step with its result and timing, every
assertion), export.json (the standard export of the users in the run) and summary.md.
The exit code is 1 when a step failed or an assertion did not hold.
"""
import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from .appconfig import AppConfig, merge, set_path, validate
from .export import export, to_json

ACTIONS = ("onboard", "recommend", "feedback", "chat", "review", "resolve", "update_profile", "save_goal",
           "revert_goal", "what_if", "goal_options", "correct_belief", "add_document", "insight", "set_user",
           "set_role", "expect", "repeat", "note", "ui_event")


class ScenarioError(Exception):
    pass


def load(path):
    path = Path(path)
    try:
        sc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ScenarioError(f"{path}: not valid JSON ({e})") from e
    sc.setdefault("name", path.stem)
    problems = check(sc)
    if problems:
        raise ScenarioError(f"{path}: " + "; ".join(problems))
    return sc


def check(sc):
    """Static checks: known actions, required fields. Returns a list of problems."""
    out = []

    def walk(steps, where):
        if not isinstance(steps, list):
            out.append(f"{where}: steps must be a list")
            return
        for i, st in enumerate(steps, 1):
            a = st.get("action") if isinstance(st, dict) else None
            if a not in ACTIONS:
                out.append(f"{where} step {i}: unknown action {a!r} (use one of {', '.join(ACTIONS)})")
                continue
            need = {"feedback": ("recipe", "type"), "chat": ("text",), "resolve": ("do",), "expect": ("that",),
                    "repeat": ("times", "steps"), "set_user": ("user",), "set_role": ("role",),
                    "add_document": ("title", "text"), "correct_belief": ("concept", "mean"),
                    "goal_options": ("increase",), "revert_goal": ("version",), "ui_event": ("kind",)}.get(a, ())
            out.extend(f"{where} step {i} ({a}): missing '{k}'" for k in need if k not in st)
            if a == "repeat":
                walk(st.get("steps"), f"{where} step {i}")
    walk(sc.get("steps"), "")
    if sc.get("config"):
        out.extend(f"config: {e}" for e in validate(merge(AppConfig({}).data, sc["config"])))
    return out


# ---- reading values for `expect` -------------------------------------------------------------------
_TOKEN = re.compile(r"[^.\[\]]+|\[\d+\]")


def lookup(obj, path):
    """Dotted path with indexes: "result.results[0].title", "result.results.length", "open_decisions.0.kind"."""
    cur = obj
    for tok in _TOKEN.findall(path):
        key = tok.strip("[]")
        if key in ("length", "count") and isinstance(cur, (list, dict, str)):
            cur = len(cur)
        elif isinstance(cur, list):
            try:
                cur = cur[int(key)]
            except (ValueError, IndexError):
                return _MISSING
        elif isinstance(cur, dict):
            if key not in cur:
                return _MISSING
            cur = cur[key]
        else:
            return _MISSING
    return cur


class _Missing:
    def __repr__(self):
        return "<missing>"


_MISSING = _Missing()
OPS = {
    "equals": lambda v, x: v == x, "not_equals": lambda v, x: v != x,
    "gt": lambda v, x: v is not None and v > x, "gte": lambda v, x: v is not None and v >= x,
    "lt": lambda v, x: v is not None and v < x, "lte": lambda v, x: v is not None and v <= x,
    "contains": lambda v, x: x in v if isinstance(v, (list, str, dict)) else False,
    "not_contains": lambda v, x: x not in v if isinstance(v, (list, str, dict)) else True,
    "in": lambda v, x: v in x, "exists": lambda v, x: (v is not _MISSING) == bool(x),
    "matches": lambda v, x: isinstance(v, str) and re.search(x, v, re.I) is not None,
}


# ---- the runner -------------------------------------------------------------------------------------
class Runner:
    def __init__(self, scenario, overrides=None, data_dir=None, keep_services=False, llm=None, log=print):
        self.sc = scenario
        self.log = log
        from .config import load_settings
        from .agent import Supervisor
        from .service import App
        # "base_config": "defaults" ignores foodrec.config.json, so a scenario is self-contained
        base = AppConfig({}) if scenario.get("base_config") == "defaults" else AppConfig.load()
        data = merge(base.raw, scenario.get("config") or {})
        for k, v in (overrides or {}).items():
            set_path(data, k, v)
        self.cfg = AppConfig(data, base.path)
        if self.cfg.errors:
            raise ScenarioError("config: " + "; ".join(self.cfg.errors))
        self.tmp = None
        if data_dir is None:
            self.tmp = tempfile.mkdtemp(prefix="foodrec-scenario-")
            data_dir = self.tmp
            # Reuse the configured data folder's embedding caches: without them every fresh run
            # would embed the whole catalogue again (minutes with Ollama on the CPU).
            for f in load_settings(config=self.cfg).data_dir.glob("embed_cache_*.npz"):
                shutil.copy2(f, Path(data_dir) / f.name)
        extra = {} if keep_services else {"qdrant": "memory", "neo4j_uri": ""}
        self.settings = load_settings(config=self.cfg, data_dir=Path(data_dir), **extra)
        self.app = App(self.settings, llm=llm)
        self.sup = Supervisor(self.app)
        self.user = scenario.get("user", "scenario_user")
        self.users = []
        self.ctx = {}           # what `expect` reads
        self.steps = []
        self.assertions = []

    def close(self):
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ----
    def _use(self, uid):
        if uid not in self.users:
            self.users.append(uid)
        return uid

    def _last_list(self, uid):
        return self.app.store.last_list(uid)

    def _recipe(self, uid, ref):
        """'#2' (rank in the last list), 'best:t' / 'worst:h' (the recipe in the last list that
        scores highest / lowest on a criterion), a recipe id, or a title (exact, then substring)."""
        ref = str(ref).strip()
        cat = self.app.catalogue
        m = re.fullmatch(r"(best|worst):([phst])", ref)
        if m:
            last = self._last_list(uid)
            rows = [r for r in (last or {}).get("results") or [] if r["scores"].get(m.group(2)) is not None]
            if not rows:
                raise ScenarioError(f"{ref}: no list with {m.group(2)} scores shown yet")
            pick = (max if m.group(1) == "best" else min)(rows, key=lambda r: r["scores"][m.group(2)])
            return pick["recipe_id"], last["request_id"]
        m = re.fullmatch(r"#(\d+)", ref)
        if m:
            last = self._last_list(uid)
            rows = (last or {}).get("results") or []
            n = int(m.group(1))
            if not 1 <= n <= len(rows):
                raise ScenarioError(f"{ref}: the last list has {len(rows)} recipes")
            return rows[n - 1]["recipe_id"], last["request_id"]
        if ref in cat.by_id:
            return ref, self._request_with(uid, ref)
        low = ref.lower()
        hit = next((r for r in cat.recipes if r["title"].lower() == low), None) or \
            next((r for r in cat.recipes if low in r["title"].lower()), None)
        if hit is None:
            raise ScenarioError(f"no recipe matches {ref!r}")
        return hit["id"], self._request_with(uid, hit["id"])

    def _request_with(self, uid, rid):
        last = self._last_list(uid)
        return last["request_id"] if last and any(r["recipe_id"] == rid for r in last["results"]) else None

    def _decision(self, uid, sel):
        """'latest' (newest open), 'latest:<kind>', or a decision id."""
        sel = sel or "latest"
        if sel.startswith("dec_"):
            return sel
        kind = sel.partition(":")[2] or None
        open_ = self.app.store.decisions(uid, status="open", kind=kind)
        if not open_:
            raise ScenarioError(f"no open decision{' of kind ' + kind if kind else ''}")
        return open_[0]["decision_id"]

    # ---- actions ----
    def run(self):
        t0 = time.perf_counter()
        self.log(f"▶ {self.sc['name']}" + (f" — {self.sc.get('description')}" if self.sc.get("description") else ""))
        self._run_steps(self.sc.get("steps") or [], prefix="")
        ok = all(s["status"] in ("ok", "skipped") for s in self.steps) and all(a["passed"] for a in self.assertions)
        return {"scenario": self.sc["name"], "description": self.sc.get("description"), "passed": ok,
                "elapsed_s": round(time.perf_counter() - t0, 2),
                "llm": {"model": self.app.llm.model, "enabled": self.settings.llm_enabled,
                        "available": self.app.llm.status().get("available")},
                "users": self.users, "steps": self.steps, "assertions": self.assertions,
                "assertions_passed": sum(a["passed"] for a in self.assertions),
                "assertions_total": len(self.assertions)}

    def _run_steps(self, steps, prefix):
        for i, st in enumerate(steps, 1):
            n = f"{prefix}{i}"
            if st["action"] == "repeat":
                for k in range(int(st["times"])):
                    self._run_steps(st["steps"], f"{n}.{k + 1}.")
                continue
            for k in range(int(st.get("times", 1))):
                self._step(f"{n}" + (f"x{k + 1}" if st.get("times", 1) > 1 else ""), st)

    def _step(self, n, st):
        a = st["action"]
        uid = self._use(st.get("user") or self.user) if a not in ("set_user", "note") else None
        t0 = time.perf_counter()
        rec = {"step": n, "action": a, "user": uid, "label": st.get("label")}
        try:
            if a != "expect":
                self.ctx.pop("error", None)  # `expect` may check the previous step's error
            out, summary = getattr(self, f"_a_{a}")(uid, st)
            rec.update({"status": "ok", "summary": summary, "output": out})
        except Exception as e:  # noqa: BLE001 - a failing step is reported, not raised
            # "allow_error": true marks a step that is expected to be refused (e.g. a closed study)
            rec.update({"status": "ok" if st.get("allow_error") else "error", "error": f"{type(e).__name__}: {e}",
                        "summary": ("refused as expected: " if st.get("allow_error") else "") + f"{type(e).__name__}: {e}",
                        "output": None})
            self.ctx["error"] = rec["error"]
        rec["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        self.steps.append(rec)
        mark = {"ok": "✓", "error": "✗"}.get(rec["status"], "·")
        if a != "expect":
            self.log(f"  {mark} {n:>6} {a:<15} {' '.join(str(rec['summary']).split())[:160]}")

    def _a_note(self, uid, st):
        return None, st.get("text", "")

    def _a_set_user(self, uid, st):
        self.user = st["user"]
        self._use(self.user)
        if st.get("role"):
            self.app.set_role(self.user, st["role"])
        return None, f"now acting as {self.user}" + (f" ({st['role']})" if st.get("role") else "")

    def _a_set_role(self, uid, st):
        acc = self.app.set_role(uid, st["role"])
        self.ctx["access"] = acc
        return acc, f"{uid} -> role {acc['role']} (effective; source {acc['source']})"

    def _a_onboard(self, uid, st):
        state = self.app.onboard(uid, st.get("declared") or {}, st.get("weights"), st.get("diversity"), st.get("mode"))
        self.ctx["state"] = state
        return {"profile_version": state["profile"]["version"], "goal": state["goal"]["weights"],
                "role": state["access"]["role"]}, \
            f"profile v{state['profile']['version']}, role {state['access']['role']}, " \
            f"{len(state['constraints'])} mandatory rule(s)"

    def _a_recommend(self, uid, st):
        from .service import MEAL_CONTEXTS
        ov = {k: st.get(k) for k in ("weights", "diversity", "mode")}
        if st.get("meal") in MEAL_CONTEXTS:
            ov["request_filters"] = [{"field": "meal", "op": "is", "value": MEAL_CONTEXTS[st["meal"]],
                                      "source": f"{st['meal']} context"}]
        res = self.app.recommend(uid, ov, origin="scenario")
        self.ctx["result"] = res
        rows = res.get("results") or []
        out = {"request_id": res["request_id"], "eligible": res["eligibility"]["eligible_count"],
               "results": [{"rank": r["final_rank"], "recipe_id": r["recipe_id"], "title": r["title"],
                            "utility": round(r["utility"], 4),
                            "scores": {k: (None if v is None else round(v, 4)) for k, v in r["scores"].items()}}
                           for r in rows],
               "comparison": {k: res["comparison"][k] for k in ("entered", "left")} if res.get("comparison") else None}
        return out, (", ".join(f"#{r['rank']} {r['title']}" for r in out["results"]) or "no eligible recipe")

    def _a_feedback(self, uid, st):
        rid, req = self._recipe(uid, st["recipe"])
        out = self.app.feedback(uid, st.get("request_id", req), rid, st["type"], st.get("reason"),
                                st.get("context") or {}, st.get("scope") or "lasting")
        title = self.app.catalogue.by_id[rid]["title"]
        res = {"recipe_id": rid, "title": title, **{k: out.get(k) for k in ("event_id", "duplicate", "scope")}}
        if st.get("review", True) and not out.get("duplicate") and self.app.access(uid)["features"]["supervisor"]:
            from .agent import policy
            from .agent.trace import Trace
            tr = Trace()
            rv = policy.review(self.app, uid, tr, trigger="feedback")
            res["review"] = {"status": rv["status"], "decisions": [d["title"] for d in rv["decisions"]],
                             "trace": [s["title"] for s in tr.steps]}
            self.ctx["review"] = res["review"]
        self.ctx["feedback"] = res
        tail = f"; supervisor: {res['review']['status']}" if res.get("review") else ""
        tail += f", raised: {'; '.join(res['review']['decisions'])}" if res.get("review", {}).get("decisions") else ""
        return res, f"{st['type']} {title}" + (f" ({st['reason']})" if st.get("reason") else "") + \
            (" [duplicate]" if out.get("duplicate") else "") + tail

    def _a_chat(self, uid, st):
        out = self.sup.chat(uid, st["text"], think=st.get("think"))
        m = out["message"]
        att = m.get("attachments") or {}
        res = {"text": m["text"], "method": out["method"], "elapsed_ms": out["elapsed_ms"],
               "trace": [{"kind": s["kind"], "title": s["title"], "source": s.get("source"), "status": s.get("status")}
                         for s in m["trace"]],
               "skills": [s["title"] for s in m["trace"] if s["kind"] == "tool"],
               "attachments": sorted(att), "decisions": [d["title"] for d in att.get("decisions") or []]}
        if att.get("result"):
            res["results"] = [{"rank": r["final_rank"], "title": r["title"]} for r in att["result"].get("results") or []]
            self.ctx["result"] = att["result"]
        self.ctx["chat"] = res
        return res, f"[{out['method']}, {', '.join(res['skills']) or 'no skill'}] {m['text'][:120]}"

    def _a_review(self, uid, st):
        from .agent import policy
        from .agent.trace import Trace
        tr = Trace()
        rv = policy.review(self.app, uid, tr, trigger="manual")
        res = {"status": rv["status"], "decisions": [{"kind": d["kind"], "title": d["title"]} for d in rv["decisions"]],
               "trace": [s["title"] for s in tr.steps]}
        self.ctx["review"] = res
        return res, f"{rv['status']}" + (f": {'; '.join(d['title'] for d in res['decisions'])}" if res["decisions"] else "")

    def _a_resolve(self, uid, st):
        did = self._decision(uid, st.get("decision"))
        out = self.app.resolve_decision(uid, did, st["do"], st.get("answer"))
        res = {"decision_id": did, "title": out["decision"]["title"], "status": out["decision"]["status"],
               "effect": {k: v for k, v in (out["effect"] or {}).items() if k != "trail"}}
        self.ctx["resolved"] = res
        return res, f"{st['do']}{' ' + str(st['answer']) if st.get('answer') else ''}: {res['title']} -> {res['status']}"

    def _a_update_profile(self, uid, st):
        prof = self.app.store.profile(uid)
        declared = dict((prof or {}).get("declared") or {})
        if st.get("declared") is not None:
            declared = st["declared"]
        for k, v in (st.get("patch") or {}).items():
            declared[k] = v
        out = self.app.update_profile(uid, declared, st.get("source", "user"), st.get("note"))
        return {"profile_version": out["profile_version"]}, f"profile v{out['profile_version']}"

    def _a_save_goal(self, uid, st):
        g = self.app.store.goal(uid) or {}
        out = self.app.save_goal(uid, st.get("name", "Scenario goal"), st["weights"],
                                 st.get("diversity", g.get("diversity", 0.3)), st.get("mode", g.get("mode", "lightweight")))
        return {k: out[k] for k in ("goal_version", "weights_applied")}, f"goal v{out['goal_version']}"

    def _a_revert_goal(self, uid, st):
        out = self.app.revert_goal(uid, int(st["version"]))
        return {"goal_version": out["goal_version"]}, f"restored v{st['version']} as v{out['goal_version']}"

    def _a_what_if(self, uid, st):
        wi = self.app.what_if(uid, st.get("weights"), st.get("diversity"), st.get("mode"))
        c = wi["comparison"] or {}
        res = {"entered": c.get("entered", []), "left": c.get("left", []), "moved": len(c.get("moved", []))}
        self.ctx["what_if"] = res
        return res, f"{len(res['entered'])} in, {len(res['left'])} out, {res['moved']} moved (nothing saved)"

    def _a_goal_options(self, uid, st):
        d = self.app.goal_options(uid, st["increase"], st.get("decrease"), float(st.get("max_drop", 0.10)),
                                  source="scenario")
        res = {"decision_id": d["decision_id"], "options": [{"id": o["id"], "label": o["label"]}
                                                            for o in d["body"]["options"]]}
        return res, f"{len(res['options'])} option(s): " + "; ".join(o["label"] for o in res["options"])

    def _a_correct_belief(self, uid, st):
        out = self.app.correct_belief(uid, st["concept"], float(st["mean"]))
        return {"event_id": out["event_id"]}, f"{st['concept']} -> {st['mean']}"

    def _a_add_document(self, uid, st):
        doc = self.app.add_document(st["title"], st["text"])
        return {"doc_id": doc}, f"added {st['title']!r}"

    def _a_ui_event(self, uid, st):
        out = self.app.log_ui_event(uid, st["kind"], {k: v for k, v in st.items() if k not in ("action", "kind")})
        return out, st["kind"]

    def _a_insight(self, uid, st):
        ins = self.app.insight(uid)
        res = {"preference_match": ins["preference_match"], "stated": ins["stated"], "observed": ins["observed"],
               "taste": [{"concept": t["concept"], "mean": round(t["mean"], 3)} for t in ins["taste"][:8]]}
        self.ctx["insight"] = res
        obs = ", ".join(f"{k} {v:.2f}" for k, v in (ins["observed"] or {}).items())
        return res, f"observed: {obs or 'n/a'}; match {ins['preference_match'] or 0:.2f}"

    def _a_expect(self, uid, st):
        ctx = {**self.ctx, "state": self.app.state(uid), "user": uid,
               "open_decisions": self.app.store.decisions(uid, status="open"),
               "last_step": self.steps[-1]["output"] if self.steps else None}
        v = lookup(ctx, st["that"])
        checks = [(op, st[op]) for op in OPS if op in st] or [("exists", True)]
        ok = all(OPS[op](v, x) if (v is not _MISSING or op == "exists") else False for op, x in checks)
        desc = st.get("label") or f"{st['that']} " + " and ".join(f"{op} {x!r}" for op, x in checks)
        self.assertions.append({"step": len(self.steps) + 1, "description": desc, "passed": ok,
                                "actual": None if v is _MISSING else v})
        self.log(f"  {'✓' if ok else '✗'} expect {desc}" + ("" if ok else f" (actual: {v!r})"))
        return {"passed": ok, "actual": None if v is _MISSING else v}, ("passed" if ok else f"FAILED: actual {v!r}")


# ---- running files ---------------------------------------------------------------------------------
def _slug(s):
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", s).strip("_")[:80] or "run"


def variants(matrix):
    """--matrix llm.model=a,b --matrix llm.enabled=true,false -> list of {key: value} dicts."""
    out = [{}]
    for m in matrix or []:
        key, _, vals = m.partition("=")
        out = [{**o, key: v} for o in out for v in vals.split(",") if v != ""]
    return out


def run_file(path, out_dir, overrides=None, matrix=None, data_dir=None, keep_services=False, llm=None, log=print):
    sc = load(path)
    reports = []
    for var in variants(matrix):
        ov = {**(overrides or {}), **var}
        name = _slug(sc["name"] + ("__" + "__".join(f"{k}={v}" for k, v in var.items()) if var else ""))
        if var:
            log(f"\n=== variant {', '.join(f'{k}={v}' for k, v in var.items())}")
        runner = Runner(sc, ov, data_dir, keep_services, llm, log)
        try:
            rep = runner.run()
            rep.update({"file": str(path), "variant": var, "overrides": ov,
                        "config_version": runner.cfg.version})
            doc = export(runner.app, runner.users, extra={"scenario": {"name": sc["name"], "file": str(path),
                                                                      "variant": var, "passed": rep["passed"]}})
        finally:
            runner.close()
        if out_dir:
            d = Path(out_dir) / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "report.json").write_text(json.dumps(rep, indent=1, default=float, ensure_ascii=False))
            (d / "export.json").write_text(to_json(doc))
            (d / "summary.md").write_text(summary_md(rep))
            rep["output_dir"] = str(d)
        reports.append(rep)
        log(f"{'PASS' if rep['passed'] else 'FAIL'}  {sc['name']}  {rep['assertions_passed']}/{rep['assertions_total']} "
            f"assertions, {sum(s['status'] == 'error' for s in rep['steps'])} step error(s), {rep['elapsed_s']} s"
            + (f"  -> {rep['output_dir']}" if rep.get("output_dir") else ""))
    return reports


def summary_md(rep):
    lines = [f"# Scenario: {rep['scenario']}", "", rep.get("description") or "", "",
             f"- Result: **{'PASS' if rep['passed'] else 'FAIL'}**",
             f"- Model: {rep['llm']['model']} (enabled {rep['llm']['enabled']}, available {rep['llm']['available']})",
             f"- Variant: {rep.get('variant') or 'base config'}",
             f"- Assertions: {rep['assertions_passed']}/{rep['assertions_total']}", f"- Time: {rep['elapsed_s']} s", "",
             "| Step | Action | User | Status | Summary | ms |", "|---|---|---|---|---|---|"]
    for s in rep["steps"]:
        summ = str(s["summary"]).replace("|", "\\|").replace("\n", " ")[:160]
        lines.append(f"| {s['step']} | {s['action']} | {s['user'] or ''} | {s['status']} | {summ} | {s['ms']} |")
    if rep["assertions"]:
        lines += ["", "## Assertions", ""]
        lines += [f"- {'✅' if a['passed'] else '❌'} {a['description']}"
                  + ("" if a["passed"] else f" (actual: `{a['actual']!r}`)") for a in rep["assertions"]]
    return "\n".join(lines) + "\n"

