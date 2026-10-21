"""The supervisor agent: one chat turn = perceive -> understand -> plan -> act -> (follow up)
-> compose -> validate. Every stage is a trace step streamed to the UI.

Division of labour (docs/supervisor.md):
  * the language model chooses among FIXED options (intent, next skill) and phrases answers;
  * a deterministic plan maps intents to skills and fills every skill argument from validated
    fields, so the model can never call a skill with invented arguments;
  * the recommendation engine ranks; the policy decides when to ask or propose;
  * the user decides: nothing that changes a goal or a constraint happens without a click.
"""
from dataclasses import dataclass

from ..constraints import SUPPORTED_ALLERGENS, SUPPORTED_DIETS
from ..explain import BANNED, _NUM
from ..llm import LLMUnavailable
from .intent import SCHEMA, model_prompt, parse_rules, validate_model
from .skills import Skills
from .trace import Trace

FOLLOW_UPS = ("alternatives", "graph_lookup", "search_knowledge", "explain_preference", "history")
HELP = ("I can suggest five recipes (\"something quick with lentils\"), explain why a recipe ranks where it "
        "does (\"why #2?\"), compare two dishes, show what would change if a priority moved (\"what if I cared "
        "more about sustainability?\"), tell you what I believe about your preferences and why, search your "
        "history (\"what did I reject last week?\"), answer questions from the recipe and ingredient knowledge "
        "base, and prepare profile changes for you to confirm. Everything I do is shown step by step above.")


@dataclass
class Ctx:
    user_id: str
    trace: Trace


class Supervisor:
    def __init__(self, app):
        self.app = app
        self.skills = Skills(app)
        cat = app.catalogue
        self.vocab = {"cuisines": sorted({r["cuisine"] for r in cat.recipes}),
                      "ingredients": sorted({i["name"] for r in cat.recipes for i in r["ingredients"]}),
                      "allergens": list(SUPPORTED_ALLERGENS), "diets": list(SUPPORTED_DIETS)}

    @property
    def llm(self):
        return self.app.llm

    # ---- one chat turn ------------------------------------------------------------------
    def chat(self, user_id, text, sink=None, think=None):
        app = self.app
        trace = Trace(sink)
        ctx = Ctx(user_id, trace)
        app.store.add_message(user_id, "user", text)
        llm_ok = bool(app.llm.status().get("available")) if hasattr(app.llm, "status") else False

        # 1. perceive
        state = app.state(user_id)
        last = app.store.last_list(user_id)
        listing = ", ".join(f"#{r['final_rank']} {r['title']}" for r in (last or {}).get("results", []))
        trace.step("perceive", "Loaded your current state",
                   f"Profile v{(state['profile'] or {}).get('version', 0)}, goal v{(state['goal'] or {}).get('version', 0)}, "
                   f"{len(state['constraints'])} mandatory rules, {len(state['open_decisions'])} open question(s). "
                   + (f"Latest list: {listing}." if listing else "No list shown yet."),
                   evidence={"constraints": [c["label"] for c in state["constraints"]],
                             "goal_weights": (state["goal"] or {}).get("weights"),
                             "model": app.llm.model if llm_ok else None}, source="store")
        if not state["onboarded"]:
            return self._finish(ctx, "Please complete the short onboarding first, so I know your allergies, "
                                     "diet and priorities. Nothing is recommended before that.", {}, [], [], text)

        # 2. understand
        intent = self._understand(ctx, text, listing, llm_ok, think)

        # 3. plan
        plan, clarify = self._plan(ctx, intent)
        if clarify:
            trace.step("reason", "Need clarification", clarify)
            return self._finish(ctx, clarify, {}, [], [], text)
        trace.step("reason", "Plan: " + (" -> ".join(p[0] for p in plan) or "answer directly"),
                   self._plan_rationale(intent, plan),
                   evidence=[{"skill": s, "args": a, "side_effect": self.skills.registry[s].side_effect} for s, a in plan])

        # 4. act
        results = []
        for name, args in plan:
            results.append((name, self._run_skill(ctx, name, args)))

        # 5. bounded follow-up chosen by the model (or by rule when offline)
        steps = len(plan)
        while steps < self.app.s.max_agent_steps:
            nxt = self._next_step(ctx, intent, results, text, llm_ok)
            if nxt is None:
                break
            results.append((nxt[0], self._run_skill(ctx, *nxt)))
            steps += 1

        # 6. compose + validate
        facts, numbers, titles, attachments = [], [], [], {}
        for name, r in results:
            facts += r.facts
            numbers += [n for n in r.numbers if isinstance(n, (int, float))]
            titles += [t for t in r.titles if t]
            for k, v in r.data.items():
                if k == "decisions":
                    attachments.setdefault("decisions", []).extend(v)
                else:
                    attachments[k] = v
        if intent["intent"] == "help" and not results:
            facts = [HELP]
        if attachments.get("empty"):
            intent = {**intent, "intent": "no_match"}
        return self._finish(ctx, self._template(intent, facts), attachments, numbers, titles, text,
                            facts=facts, llm_ok=llm_ok, think=think)

    # ---- stages ---------------------------------------------------------------------------
    def _understand(self, ctx, text, listing, llm_ok, think):
        tr = ctx.trace
        rules = parse_rules(text, self.app.catalogue, self.vocab)
        if not llm_ok:
            reason = self.app.llm.status().get("reason") if hasattr(self.app.llm, "status") else "no model"
            tr.step("decide", f"Intent: {rules['intent'].replace('_', ' ')}",
                    f"Rule-based parser (language model unavailable: {reason}).",
                    evidence=_compact(rules), source="rules",
                    data={"options": list(SCHEMA["properties"]["intent"]["enum"]), "chosen": rules["intent"]})
            return rules
        s = tr.start("decide", "Understanding your message", f"Asking {self.app.llm.model} to classify it into a fixed schema",
                     source="model")
        mstep = {}

        def on_think(tok):
            if "s" not in mstep:
                mstep["s"] = tr.start("model", "Model reasoning (unverified)", "", source="model")
            tr.model_token(mstep["s"], tok)
        try:
            out = self.app.llm.chat(model_prompt(text, listing, self.vocab), schema=SCHEMA, think=think,
                                    on_thinking=on_think, max_tokens=400)
            if "s" in mstep:
                tr.finish(mstep["s"])
            intent, notes = validate_model(out["data"], text, self.app.catalogue, self.vocab)
            # keep rule-detected request filters the model missed (they are explicit numbers in the text)
            for k, v in rules["filters"].items():
                intent["filters"].setdefault(k, v)
            agree = intent["intent"] == rules["intent"]
            tr.finish(s, title=f"Intent: {intent['intent'].replace('_', ' ')}",
                      detail=(f"Model output validated ({out['tokens_in']} tokens in, {out['tokens_out']} out, "
                              f"{out['ms']:.0f} ms). Rule parser {'agrees' if agree else 'suggested ' + rules['intent']}."),
                      evidence=_compact(intent), data={"chosen": intent["intent"], "rules": rules["intent"]})
            if notes:
                tr.step("check", "Corrected model output", "; ".join(notes), source="validator")
            return intent
        except (LLMUnavailable, ValueError, KeyError, TypeError) as e:
            if "s" in mstep:
                tr.finish(mstep["s"], status="failed")
            tr.finish(s, title=f"Intent: {rules['intent'].replace('_', ' ')}", status="failed",
                      detail=f"Model output unusable ({type(e).__name__}: {str(e)[:120]}); used the rule-based parser.",
                      evidence=_compact(rules))
            return rules

    def _plan(self, ctx, it):
        refs, notes = self.skills.resolve(ctx.user_id, it["recipe_refs"])
        if notes:
            ctx.trace.step("check", "Recipe reference", "; ".join(notes), source="validator")
        i = it["intent"]
        if i == "recommend":
            return [("recommend", {"filters": it["filters"], "criterion": it["criterion"],
                                   "direction": it["direction"]}), ("review", {})], None
        if i == "explain_recipe":
            if not refs:
                return [], "Which recipe do you mean? You can say \"#2\" or use its name."
            plan = [("explain_recipe", {"recipe_id": refs[0]})]
            if it["criterion"] in ("h", "s", "c", "t") and it["direction"] == "more":
                plan.append(("alternatives", {"recipe_id": refs[0], "criterion": it["criterion"]}))
            return plan, None
        if i == "compare_recipes":
            last = self.skills.last_result(ctx.user_id)
            if len(refs) == 1 and last and last.get("results"):
                top = last["results"][0]["recipe_id"]
                refs = refs + [top] if refs[0] != top else refs + [last["results"][1]["recipe_id"]]
            if len(refs) < 2:
                return [], "Which two recipes should I compare? For example: \"compare #1 and #3\"."
            return [("compare_recipes", {"a": refs[0], "b": refs[1]})], None
        if i == "what_if":
            if not it["criterion"]:
                return [], ("Which priority should change? Taste, healthiness, sustainability, affordability "
                            "or convenience.")
            return [("what_if", {"criterion": it["criterion"], "direction": it["direction"] or "more"})], None
        if i == "why_preference":
            return [("explain_preference", {"criterion": None if it["concept"] else it["criterion"],
                                            "concept": it["concept"]})], None
        if i == "history":
            return [("history", {"days": it["time_window_days"], "types": it["history_types"],
                                 "concept": it["concept"], "recipe_id": refs[0] if refs else None})], None
        if i == "knowledge":
            plan = []
            if it["concept"]:
                plan.append(("graph_lookup", {"concept": it["concept"]}))
            plan.append(("search_knowledge", {"query": it["query"] or "", "kinds": None}))
            return plan, None
        if i == "update_profile":
            return [("propose_profile_change", dict(it["profile_change"]))], None
        if i == "review":
            return [("review", {})], None
        return [], None

    @staticmethod
    def _plan_rationale(it, plan):
        i = it["intent"]
        why = {
            "recommend": "Rank with your saved goal (plus filters from this message, which are not saved), then let the "
                         "supervisor check confidence and conflicts before presenting.",
            "explain_recipe": "Rebuild the recorded evidence for that recipe instead of generating a new explanation.",
            "compare_recipes": "Compare recorded criterion scores under your current weights.",
            "what_if": "Rerun the ranking with one changed weight and diff it against the current one; nothing is saved.",
            "why_preference": "Read the preference model and trace each belief back to the events that support it.",
            "history": "Query the knowledge graph with the time window and filters from your message.",
            "knowledge": "Look up structured facts in the graph and passages in the knowledge base.",
            "update_profile": "Mandatory rules change only with your confirmation, so prepare the change and wait.",
            "review": "Run the supervisor's conflict and confidence check.",
            "help": "No tool needed.",
        }[i]
        return why

    def _run_skill(self, ctx, name, args):
        tr = ctx.trace
        s = tr.start("tool", f"{name}", ", ".join(f"{k}={_short(v)}" for k, v in args.items() if v not in (None, [], {})),
                     source="skill", data={"skill": name, "args": args})
        try:
            r = self.skills.registry[name].run(ctx, **args)
            tr.finish(s, detail=r.summary, evidence=r.evidence)
            return r
        except Exception as e:  # noqa: BLE001 - a failing skill must not kill the turn
            tr.finish(s, detail=f"failed: {type(e).__name__}: {e}", status="failed")
            from .skills import SkillResult
            return SkillResult(f"{name} failed", {}, [f"The {name} step failed ({type(e).__name__})."])

    def _next_step(self, ctx, it, results, text, llm_ok):
        done = {n for n, _ in results}
        last_rid = next((r.data.get("recipe", {}).get("recipe_id") for n, r in results if n == "explain_recipe"), None)
        options = {}
        if last_rid and it["criterion"] in ("h", "s", "c", "t") and "alternatives" not in done:
            options["alternatives"] = {"recipe_id": last_rid, "criterion": it["criterion"]}
        if it["concept"] and "graph_lookup" not in done:
            options["graph_lookup"] = {"concept": it["concept"]}
        if it["concept"] and "history" not in done and it["intent"] in ("why_preference",):
            options["history"] = {"concept": it["concept"]}
        if it["intent"] in ("explain_recipe", "why_preference") and "search_knowledge" not in done and it.get("query"):
            options["search_knowledge"] = {"query": it["query"]}
        if not options:
            return None
        if not llm_ok:
            return None
        s = ctx.trace.start("decide", "Is the evidence sufficient?",
                            f"Model chooses: answer now, or one of {', '.join(options)}", source="model")
        schema = {"type": "object", "properties": {"next": {"type": "string", "enum": ["answer"] + list(options)},
                                                   "why": {"type": "string"}}, "required": ["next"]}
        facts = "\n".join(f"- {f}" for _, r in results for f in r.facts)[:2500]
        try:
            out = self.app.llm.chat([
                {"role": "system", "content": "You supervise a food recommender. Decide if the gathered facts answer "
                                              "the user's message. Choose 'answer' unless one listed tool would add "
                                              "clearly missing information. JSON only."},
                {"role": "user", "content": f"Message: {text}\nFacts so far:\n{facts}\nTools: " +
                                            "; ".join(f"{k}: {self.skills.registry[k].description}" for k in options)}],
                schema=schema, think=False, max_tokens=120)
            choice = out["data"].get("next")
        except (LLMUnavailable, ValueError, KeyError, TypeError) as e:
            ctx.trace.finish(s, detail=f"Model unavailable ({type(e).__name__}); answering now.", status="failed")
            return None
        if choice not in options:
            ctx.trace.finish(s, title="Evidence sufficient", detail=f"Model chose to answer. {out['data'].get('why', '')}".strip())
            return None
        ctx.trace.finish(s, title=f"Need more: {choice}", detail=out["data"].get("why") or "",
                         data={"options": ["answer"] + list(options), "chosen": choice})
        return choice, options[choice]

    # ---- answer ---------------------------------------------------------------------------
    @staticmethod
    def _template(it, facts):
        lead = {"recommend": "Here are five recipes for you.", "no_match": "Nothing matched this request.", "explain_recipe": "Here is what the evidence says.",
                "compare_recipes": "Side by side:", "what_if": "This is a preview only; nothing was saved.",
                "why_preference": "Here is what I believe and where it comes from.",
                "history": "From your history:", "knowledge": "From the knowledge base:",
                "update_profile": "", "review": "I checked your goal against your recent behaviour.", "help": ""}[it["intent"]]
        body = "\n".join(f"- {f}" for f in facts if f)
        return (lead + "\n" + body).strip() if lead else body

    def _finish(self, ctx, template, attachments, numbers, titles, user_text, facts=None, llm_ok=False, think=None):
        tr = ctx.trace
        text, method = template, "template"
        if llm_ok and facts and len(facts) > 0:
            text, method = self._phrase(ctx, template, facts, numbers, titles, user_text, think)
        tr.step("answer", "Answer ready", f"Written by {'the model, validated against the evidence' if method == 'model' else 'the template from recorded evidence'}.",
                source=method)
        msg = self.app.store.add_message(ctx.user_id, "assistant", text, tr.steps, attachments)
        return {"message": msg, "method": method, "elapsed_ms": tr.elapsed_ms()}

    def _phrase(self, ctx, template, facts, numbers, titles, user_text, think):
        tr = ctx.trace
        s = tr.start("model", "Writing the answer", f"{self.app.llm.model} rewrites the facts; it may not add any", source="model")
        prompt = [
            {"role": "system", "content": (
                "You are the voice of a transparent food recommender. Rewrite the FACTS as a short, friendly reply "
                "(at most 110 words, plain sentences, no headings). Use only the facts: do not add numbers, recipes, "
                "health or medical claims, or advice that is not in them. Keep every number exactly as written. "
                "If the facts contain a question or a pending confirmation, say so.")},
            {"role": "user", "content": f"User message: {user_text}\nFACTS:\n" + "\n".join(f"- {f}" for f in facts)}]
        try:
            out = self.app.llm.chat(prompt, think=False, max_tokens=260, temperature=0.2)
        except LLMUnavailable as e:
            tr.finish(s, detail=f"Model unavailable ({e}); using the template.", status="failed")
            return template, "template"
        tr.finish(s, detail=f"{out['tokens_out']} tokens in {out['ms']:.0f} ms")
        ok, reason = validate_answer(out["content"], numbers + _numbers_in(user_text) + _numbers_in("\n".join(facts)),
                                     titles, [r["title"] for r in self.app.catalogue.recipes])
        tr.step("check", "Answer validated" if ok else "Model answer rejected",
                "Every number and recipe name is backed by the evidence." if ok else
                f"{reason}. Falling back to the template built from the same evidence.", source="validator",
                status="done" if ok else "failed")
        return (out["content"], "model") if ok else (template, "template")


def _numbers_in(text):
    out = []
    for tok in _NUM.findall(text or ""):
        try:
            out.append(float(tok.rstrip("%")))
        except ValueError:
            pass
    return out


def validate_answer(text, numbers, titles, all_titles):
    if not text or len(text) > 1500:
        return False, "empty or overlong answer"
    m = BANNED.search(text)
    if m:
        return False, f"unsupported health/certainty claim '{m.group(0)}'"
    allowed_t = {t.lower() for t in titles}
    for t in all_titles:
        if t.lower() in text.lower() and t.lower() not in allowed_t:
            return False, f"mentions '{t}', which is not in the evidence"
    allowed = [float(n) for n in numbers]
    for tok in _NUM.findall(text):
        raw = tok.rstrip("%")
        x = float(raw)
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        if decimals == 0 and 0 <= x <= 5:
            continue
        if not any(abs(x - a) <= tol or (tok.endswith("%") and abs(x - 100 * a) <= tol) for a in allowed):
            return False, f"number {tok} does not appear in the evidence"
    return True, None


def _short(v):
    s = str(v)
    return s if len(s) < 60 else s[:57] + "..."


def _compact(it):
    return {k: v for k, v in it.items() if v not in (None, [], {}, "")}
