"""Supervisor skills: typed, side-effect-declared operations over the engine, the knowledge
graph, the retriever and the learner.

Every skill returns a SkillResult:
  summary    one line for the trace
  data       structured output for the UI (recipe cards, tables, decisions)
  facts      short factual sentences the answer is built from
  numbers    every number the answer may mention (used by the narrative validator)
  titles     recipe titles the answer may mention
Side effects: "none" (read only), "proposal" (creates a decision that waits for the user),
"snapshot" (stores an immutable recommendation snapshot). No skill edits a saved goal or
a mandatory constraint.
"""
import json
import time
from dataclasses import dataclass, field

from .. import CRITERIA, CRITERION_LABELS
from ..constraints import violations
from ..features import nutriscore
from ..ranker import utility
from . import policy
from .intent import filters_to_request


@dataclass
class SkillResult:
    summary: str
    data: dict = field(default_factory=dict)
    facts: list = field(default_factory=list)
    numbers: list = field(default_factory=list)
    titles: list = field(default_factory=list)
    evidence: object = None


@dataclass
class Skill:
    name: str
    description: str
    side_effect: str
    run: object


def _r2(x):
    return None if x is None else round(float(x), 2)


class Skills:
    def __init__(self, app):
        self.app = app
        self.registry = {s.name: s for s in [
            Skill("recommend", "Rank five recipes with the saved goal and optional request-only filters", "snapshot", self.recommend),
            Skill("explain_recipe", "Evidence for one recipe: fit, weighted contributions, compromises, gaps", "none", self.explain_recipe),
            Skill("compare_recipes", "Criterion-by-criterion comparison of two recipes under the current goal", "none", self.compare_recipes),
            Skill("what_if", "Counterfactual ranking with one priority changed; nothing is saved", "none", self.what_if),
            Skill("explain_preference", "What the system believes about a priority or taste, with confidence and evidence", "none", self.explain_preference),
            Skill("history", "Interactions, recommendations and supervisor decisions from the knowledge graph", "none", self.history),
            Skill("search_knowledge", "Hybrid (BM25 + vector) search over recipes, ingredients, method notes and user documents", "none", self.search_knowledge),
            Skill("graph_lookup", "Knowledge-graph facts for an ingredient or cuisine, incl. the user's interactions with it", "none", self.graph_lookup),
            Skill("alternatives", "Similar recipes that score better on one criterion (knowledge graph)", "none", self.alternatives),
            Skill("review", "Run the supervisor's conflict and confidence check", "proposal", self.review),
            Skill("propose_profile_change", "Prepare an allergy/diet/dislike change for the user to confirm", "proposal", self.propose_profile_change),
        ]}

    def catalog(self):
        return [{"name": s.name, "description": s.description, "side_effect": s.side_effect}
                for s in self.registry.values()]

    # ---- helpers ------------------------------------------------------------------------
    def last_result(self, user_id):
        return self.app.store.last_list(user_id)

    def resolve(self, user_id, refs):
        """'#n' -> recipe id in the latest list; ids pass through. Returns (ids, notes)."""
        last = self.last_result(user_id)
        ids, notes = [], []
        for r in refs or []:
            if r.startswith("#"):
                n = int(r[1:])
                res = (last or {}).get("results") or []
                if 1 <= n <= len(res):
                    ids.append(res[n - 1]["recipe_id"])
                else:
                    notes.append(f"there is no #{n} in the latest list")
            elif r in self.app.catalogue.by_id:
                ids.append(r)
        return list(dict.fromkeys(ids)), notes

    # ---- skills ---------------------------------------------------------------------------
    def recommend(self, ctx, filters=None, criterion=None, direction=None):
        app = self.app
        overrides, notes = {}, []
        rf = filters_to_request(filters or {})
        if rf:
            overrides["request_filters"] = [{**f, "source": "your message"} for f in rf]
        if criterion and direction == "more" and not rf:
            goal = app.store.goal(ctx.user_id)
            w = dict(goal["weights"])
            w[criterion] += 0.15
            tot = sum(w.values())
            overrides["weights"] = {k: v / tot for k, v in w.items()}
            notes.append(f"For this request only, {CRITERION_LABELS[criterion].lower()} weight raised from "
                         f"{goal['weights'][criterion]:.2f} to {overrides['weights'][criterion]:.2f}; your saved goal "
                         f"is unchanged.")
        res = app.recommend(ctx.user_id, overrides, origin="assistant")
        if res.get("empty"):
            facts = [res["empty"]["message"]]
            for f in res["empty"].get("request_filters", []):
                facts.append(f"Filter '{f['label']}' excludes {f['excluded']} of the recipes your rules allow.")
            for c in res["empty"].get("constraints", [])[:3]:
                facts.append(f"Without '{c['label']}', {c['eligible_if_removed']} recipes would be eligible.")
            nums = [f["excluded"] for f in res["empty"].get("request_filters", [])] + \
                   [c["eligible_if_removed"] for c in res["empty"].get("constraints", [])[:3]]
            return SkillResult("No recipe matched", {"result": res, "empty": True}, facts + notes, nums)
        top = res["results"]
        facts = notes + [f"Applied request filters: {', '.join(f['label'] for f in res['config']['request_filters'])}."
                         if res["config"]["request_filters"] else "No request filters; only your saved rules and goal."]
        nums, titles = [], []
        for r in top:
            ev = r["evidence"]
            k = ev["ranking"]["top_contributor"]
            line = (f"#{r['final_rank']} {r['title']} ({r['cuisine']}, {r['prep_minutes']} min, "
                    f"utility {r['utility']:.2f}); {CRITERION_LABELS[k].lower()} contributes most")
            if ev["compromises"]:
                line += f"; compromise: {ev['compromises'][0]['text']}"
            facts.append(line + ".")
            nums += [r["final_rank"], r["prep_minutes"], round(r["utility"], 2)] + \
                    [round(v["contribution"], 2) for v in ev["ranking"]["contributions"].values()]
            titles.append(r["title"])
        el = res["eligibility"]
        summary = (f"{el['eligible_count']} recipes pass your mandatory rules"
                   + (f", {el['after_request_filters']} also pass this request's filters" if rf else "")
                   + f"; ranked with {res['weights_source']} in {res['config']['mode']} mode.")
        return SkillResult(summary, {"result": res}, facts, nums + [el["eligible_count"], el["after_request_filters"]],
                           titles, evidence={"request_id": res["request_id"], "weights": res["config"]["weights_applied"],
                                             "filters": [f["label"] for f in res["config"]["request_filters"]]})

    def explain_recipe(self, ctx, recipe_id):
        app = self.app
        r = app.catalogue.get(recipe_id)
        last = self.last_result(ctx.user_id)
        prof = app.store.profile(ctx.user_id) or {"declared": {}}
        kg = app.graph.recipe_context(recipe_id, ctx.user_id) or {}
        shown = next((x for x in (last or {}).get("results", []) if x["recipe_id"] == recipe_id), None)
        facts, nums = [], []
        if shown:
            rs = shown["reasons"]
            facts += [f"It is #{shown['final_rank']} in your latest list (utility rank {shown['utility_rank']})."]
            facts += [x[0].upper() + x[1:] + "." for x in rs["fits"][:3] + rs["ranked_here"][:2] + rs["compromises"][:2]]
            facts += ["Uncertain: " + g + "." for g in rs["gaps"][:2]]
            from ..explain import _collect_numbers
            s = set()
            _collect_numbers(shown["evidence"], s)
            nums += sorted(s)
            data = {"recipe": shown}
        else:
            viol = violations(r, prof["declared"])
            goal = app.store.goal(ctx.user_id)
            sc = app.scaler.scores(r)
            row = next((t for t in (last or {}).get("ranking_table", []) if t["recipe_id"] == recipe_id), None)
            if viol:
                facts.append(f"{r['title']} is not eligible for you: " + "; ".join(v[1] for v in viol) + ".")
            elif row:
                facts.append(f"{r['title']} was a candidate at utility rank {row['utility_rank']} "
                             f"(utility {row['utility']:.2f}) but did not make the top five.")
                nums += [row["utility_rank"], round(row["utility"], 2)]
            if goal and not viol:
                sc_p = (row or {}).get("scores", {}).get("p")
                u, eff, missing, contrib = utility(goal["weights"], {**sc, "p": sc_p})
                parts = [f"{CRITERION_LABELS[k]} {sc[k]:.2f}" for k in ("h", "s", "c", "t") if sc[k] is not None]
                facts.append("Criterion scores: " + ", ".join(parts) + ".")
                nums += [round(sc[k], 2) for k in ("h", "s", "c", "t") if sc[k] is not None]
            data = {"recipe": {"recipe_id": recipe_id, "title": r["title"], "scores": sc, "violations": viol}}
        ns = nutriscore(r.get("nutrition_per_100g"))
        facts.append(f"Nutri-Score grade {ns['grade'] if ns else 'unknown'}; {r['prep_minutes']} min; "
                     f"{r['kcal_per_serving']} kcal per serving.")
        nums += [r["prep_minutes"], r["kcal_per_serving"]]
        if kg.get("user_interactions"):
            types = [i["type"] for i in kg["user_interactions"]]
            facts.append(f"Your history with it: {', '.join(types[:5])}.")
        if kg.get("similar"):
            facts.append("Similar recipes in the graph: " + ", ".join(s["title"] for s in kg["similar"][:3]) + ".")
        data["graph"] = kg
        return SkillResult(f"Built evidence for {r['title']}" + (" from the latest list" if shown else " (not in the latest list)"),
                           data, facts, nums, [r["title"]] + [s["title"] for s in kg.get("similar", [])[:3]],
                           evidence={"recipe_id": recipe_id, "in_latest_list": bool(shown)})

    def _scores_now(self, user_id, rid):
        last = self.last_result(user_id) or {}
        row = next((t for t in last.get("ranking_table", []) if t["recipe_id"] == rid), None)
        sc = dict(row["scores"]) if row else {**self.app.scaler.scores(self.app.catalogue.get(rid)), "p": None}
        return sc

    def compare_recipes(self, ctx, a, b):
        app = self.app
        goal = app.store.goal(ctx.user_id)
        ra, rb = app.catalogue.get(a), app.catalogue.get(b)
        sa, sb = self._scores_now(ctx.user_id, a), self._scores_now(ctx.user_id, b)
        rows, facts, nums = [], [], []
        for k in CRITERIA:
            va, vb = sa.get(k), sb.get(k)
            w = goal["weights"][k]
            rows.append({"criterion": k, "label": CRITERION_LABELS[k], "weight": w, "a": _r2(va), "b": _r2(vb),
                         "delta": None if va is None or vb is None else _r2(va - vb)})
            if va is not None and vb is not None and abs(va - vb) >= 0.05:
                better = ra["title"] if va > vb else rb["title"]
                facts.append(f"{CRITERION_LABELS[k]}: {better} is higher ({_r2(va)} vs {_r2(vb)}; weight {w:.2f}).")
                nums += [_r2(va), _r2(vb), round(w, 2)]
        ua = utility(goal["weights"], sa)[0]
        ub = utility(goal["weights"], sb)[0]
        if ua is not None and ub is not None:
            facts.append(f"Under your current goal: {ra['title']} {ua:.2f} vs {rb['title']} {ub:.2f}.")
            nums += [round(ua, 2), round(ub, 2)]
        shared = sorted({i["name"] for i in ra["ingredients"]} & {i["name"] for i in rb["ingredients"]})
        if shared:
            facts.append("Shared ingredients: " + ", ".join(shared) + ".")
        missing_p = sa.get("p") is None or sb.get("p") is None
        if missing_p:
            facts.append("Preference match is only available for recipes in your latest ranking; it is left out here.")
        return SkillResult(f"Compared {ra['title']} with {rb['title']} on five criteria",
                           {"comparison": {"a": {"recipe_id": a, "title": ra["title"], "utility": ua},
                                           "b": {"recipe_id": b, "title": rb["title"], "utility": ub},
                                           "rows": rows, "shared_ingredients": shared}},
                           facts, nums, [ra["title"], rb["title"]], evidence=rows)

    def what_if(self, ctx, criterion, direction="more", amount=0.15):
        app = self.app
        goal = app.store.goal(ctx.user_id)
        w = dict(goal["weights"])
        w[criterion] = max(0.0, w[criterion] + (amount if direction != "less" else -amount))
        tot = sum(w.values())
        w = {k: v / tot for k, v in w.items()}
        wi = app.what_if(ctx.user_id, weights=w)
        cmp_ = wi["comparison"]
        if cmp_ is None:
            return SkillResult("No eligible recipes under one of the settings", {"what_if": wi}, ["No comparison possible."])
        titles = {r["recipe_id"]: r["title"] for r in wi["alternative"]["results"] + wi["base"]["results"]}
        facts = [f"If {CRITERION_LABELS[criterion].lower()} weight went from {goal['weights'][criterion]:.2f} to "
                 f"{w[criterion]:.2f} (others rescaled):"]
        if cmp_["entered"]:
            facts.append("Would enter the top five: " + ", ".join(titles[x] for x in cmp_["entered"]) + ".")
        if cmp_["left"]:
            facts.append("Would leave: " + ", ".join(titles[x] for x in cmp_["left"]) + ".")
        for m in cmp_["moved"][:3]:
            facts.append(f"{m['title']} would move from #{m['from_rank']} to #{m['to_rank']}.")
        if not (cmp_["entered"] or cmp_["left"] or cmp_["moved"]):
            facts.append("The top five would stay the same in the same order.")
        nums = [round(goal["weights"][criterion], 2), round(w[criterion], 2)] + \
               [x for m in cmp_["moved"] for x in (m["from_rank"], m["to_rank"])]
        return SkillResult(f"Counterfactual: {len(cmp_['entered'])} in, {len(cmp_['left'])} out, {len(cmp_['moved'])} moved",
                           {"what_if": {"weights": w, "base": wi["base"], "alternative": wi["alternative"],
                                        "comparison": cmp_}},
                           facts, nums, list(titles.values()), evidence={"weights": {k: round(v, 3) for k, v in w.items()}})

    def explain_preference(self, ctx, criterion=None, concept=None):
        app = self.app
        ins = app.insight(ctx.user_id)
        facts, nums, ev_rows = [], [], []
        if concept:
            t = next((x for x in ins["taste"] if x["concept"] == concept), None)
            lr = app.learner(ctx.user_id)
            mean = lr.taste.mean(concept)
            n = lr.taste.evidence(concept)
            prov = lr.taste.prov.get(concept, [])
            name = concept.split(":", 1)[1]
            if not prov:
                facts.append(f"I have no recorded evidence about {name}; it is treated as neutral (0.5).")
            else:
                facts.append(f"Belief that you like {name}: {mean:.2f} (0 = dislike, 1 = like), based on "
                             f"{n:.1f} units of observed evidence.")
                kinds = [p["kind"] for p in prov]
                if "declared" in kinds:
                    facts.append(f"You declared {name} in your profile.")
                if "user_correction" in kinds:
                    facts.append("You corrected this belief yourself.")
            rows = app.graph.concept_evidence(ctx.user_id, concept, limit=10)
            for r in rows[:6]:
                ev_rows.append({"event_id": r["event_id"], "type": r["type"], "recipe": r.get("title"), "ts": r["ts"]})
            if rows:
                facts.append("Recent interactions with it: " + ", ".join(f"{r['type']} {r.get('title')}" for r in rows[:4]) + ".")
            nums += [round(mean, 2), round(n, 1)]
            data = {"taste": t or {"concept": concept, "mean": mean, "evidence": n, "provenance": prov}}
        else:
            k = criterion or max(CRITERIA, key=lambda x: next((c["prob_differs"] for c in ins["criteria"] if c["criterion"] == x), 0))
            c = next((c for c in ins["criteria"] if c["criterion"] == k), None)
            if c is None:
                return SkillResult("No preference model yet", {}, ["Complete onboarding first."])
            facts.append(f"Your saved goal gives {c['label'].lower()} a weight of {c['stated']:.2f}.")
            facts.append(f"From your behaviour I estimate {c['inferred']:.2f} (90% interval {c['lo']:.2f} to "
                         f"{c['hi']:.2f}); confidence {c['confidence']:.2f} on a 0-1 scale.")
            if c["prob_differs"] >= policy.THRESHOLDS["conflict_prob"]:
                facts.append(f"That differs from your goal with probability {c['prob_differs']:.2f}, so I treat it as "
                             f"a possible conflict, but I never change your goal myself.")
            else:
                facts.append("Your behaviour is consistent with your saved goal on this priority.")
            lr = app.learner(ctx.user_id)
            logged = {e["event_id"]: e for e in app.store.events(ctx.user_id)}
            for e in lr.choice.evidence[::-1][:8]:
                src = logged.get(e.get("event_id"), {})
                pl = src.get("payload", {})
                rid = pl.get("recipe_id")
                ev_rows.append({"event_id": e.get("event_id"), "kind": e["kind"],
                                "reason": e.get("reason") or pl.get("reason"), "flag": e.get("flag"),
                                "recipe": app.catalogue.by_id[rid]["title"] if rid in app.catalogue.by_id else None,
                                "ts": src.get("ts")})
            nums += [round(c[x], 2) for x in ("stated", "inferred", "lo", "hi", "confidence", "prob_differs")]
            data = {"criterion": c, "counts": ins["counts"]}
            if ins["counts"]:
                facts.append(f"Evidence so far: {ins['counts']['choices']} choices, {ins['counts']['rejections']} "
                             f"reasoned rejections, {ins['counts']['statements']} explicit goal statements.")
                nums += list(ins["counts"].values())
        data["evidence"] = ev_rows
        return SkillResult("Read the preference model and its provenance", data, facts, nums, [], evidence=ev_rows)

    def history(self, ctx, days=None, types=None, concept=None, recipe_id=None):
        app = self.app
        since = time.time() - days * 86400 if days else None
        kw = {}
        if concept and concept.startswith("ingredient:"):
            kw["ingredient"] = concept.split(":", 1)[1]
        if concept and concept.startswith("cuisine:"):
            kw["cuisine"] = concept.split(":", 1)[1]
        rows = app.graph.timeline(ctx.user_id, since=since, types=types or None, recipe_id=recipe_id, limit=25, **kw)
        recs = app.graph.recommended(ctx.user_id, since=since, limit=15)
        decs = app.graph.decisions(ctx.user_id, since=since, limit=10)
        food_rows = [r for r in rows if r.get("recipe_id")]
        window = f"in the last {days} day(s)" if days else "so far"
        facts = [f"{len(food_rows)} food interactions {window}" + (f" matching {concept.split(':', 1)[1]}" if concept else "") + "."]
        for r in food_rows[:6]:
            facts.append(f"{time.strftime('%d %b %H:%M', time.localtime(r['ts']))}: {r['type']} {r.get('title')}"
                         + (f" (reason: {r['reason'].replace('_', ' ')})" if r.get("reason") else "") + ".")
        if recs and not types:
            facts.append(f"{len({r['request_id'] for r in recs})} recommendation lists {window}; most recent top pick: {recs[0]['title']}.")
        if decs:
            facts.append(f"{len(decs)} supervisor decisions {window}, latest: {decs[0]['title']} ({decs[0]['status']}).")
        return SkillResult(f"Queried the knowledge graph: {len(rows)} interactions, {len(recs)} recommended items, {len(decs)} decisions",
                           {"timeline": rows, "recommended": recs, "decisions": decs}, facts,
                           [len(food_rows), days or 0, len(decs)], [r.get("title") for r in food_rows if r.get("title")] +
                           [r["title"] for r in recs[:1]],
                           evidence={"since": since, "types": types, "concept": concept})

    def search_knowledge(self, ctx, query, kinds=None, k=4):
        hits = self.app.retriever.search(query, k=k, kinds=kinds)
        facts, nums = [], []
        for h in hits:
            label = {"user": "your document", "method": "method note", "recipe": "recipe", "ingredient": "ingredient"}[h["kind"]]
            facts.append(f"[{h['id']}] ({label}) {h['title']}: {h['text'][:420]}")
            from ..explain import _collect_numbers
            s = set()
            _collect_numbers(h["text"], s)
            nums += sorted(s)
        desc = self.app.retriever.description
        return SkillResult(f"Retrieved {len(hits)} passages ({desc['embedder']} + BM25, {desc['vector_backend']})",
                           {"hits": hits, "retriever": desc}, facts, nums,
                           [h["title"] for h in hits if h["kind"] == "recipe"],
                           evidence=[{"id": h["id"], "bm25_rank": h["bm25_rank"], "dense_rank": h["dense_rank"],
                                      "rrf": h["rrf"]} for h in hits])

    def graph_lookup(self, ctx, concept):
        kind, _, name = concept.partition(":")
        if kind == "ingredient":
            p = self.app.graph.ingredient_profile(name, ctx.user_id)
            if not p:
                return SkillResult(f"No graph node for {name}", {}, [f"{name} is not in the knowledge graph."])
            ing = p["ingredient"]
            facts = [f"{name}: category {', '.join(p['category']) or 'unknown'}; allergens {', '.join(p['allergens']) or 'none recorded'}.",
                     f"Per 100 g: protein {ing.get('protein_g')} g, sugars {ing.get('sugars_g')} g, fibre {ing.get('fibre_g')} g, "
                     f"{ing.get('kcal')} kcal.",
                     f"Used in {len(p['recipes'])} recipes, e.g. " + ", ".join(r["title"] for r in p["recipes"][:4]) + "."]
            if p.get("user_interactions"):
                facts.append("Your interactions with dishes containing it: " +
                             ", ".join(f"{k} {v}" for k, v in p["user_interactions"].items()) + ".")
            nums = [ing.get(x) for x in ("protein_g", "sugars_g", "fibre_g", "kcal") if ing.get(x) is not None] + \
                   [len(p["recipes"])] + list((p.get("user_interactions") or {}).values())
            return SkillResult(f"Graph facts for ingredient {name}", {"graph": p}, facts, nums,
                               [r["title"] for r in p["recipes"][:4]], evidence={"node": f"ingredient:{name}"})
        rows = [r for r in self.app.catalogue.recipes if r["cuisine"] == name]
        hist = self.app.graph.timeline(ctx.user_id, cuisine=name, limit=20)
        facts = [f"{len(rows)} {name} recipes in the catalogue: " + ", ".join(r["title"] for r in rows[:5]) + "."]
        if hist:
            facts.append(f"You interacted with {name} dishes {len(hist)} times: " +
                         ", ".join(f"{h['type']} {h.get('title')}" for h in hist[:4]) + ".")
        return SkillResult(f"Graph facts for cuisine {name}", {"recipes": [r["id"] for r in rows], "timeline": hist},
                           facts, [len(rows), len(hist)], [r["title"] for r in rows[:5]] + [h.get("title") for h in hist[:4]])

    def alternatives(self, ctx, recipe_id, criterion):
        alts = self.app.graph.alternatives(recipe_id, criterion, k=3)
        prof = self.app.store.profile(ctx.user_id) or {"declared": {}}
        alts = [a for a in alts if not violations(self.app.catalogue.get(a["recipe_id"]), prof["declared"])]
        r = self.app.catalogue.get(recipe_id)
        if not alts:
            return SkillResult("No eligible alternative found", {"alternatives": []},
                               [f"No eligible recipe similar to {r['title']} scores higher on {CRITERION_LABELS[criterion].lower()}."])
        facts = [f"Similar to {r['title']} but higher on {CRITERION_LABELS[criterion].lower()} "
                 f"({alts[0]['base_score']:.2f} for {r['title']}):"]
        for a in alts:
            facts.append(f"{a['title']} scores {a['score']:.2f}" + (f", shares {', '.join(a['shared_ingredients'][:3])}"
                                                                   if a["shared_ingredients"] else ", same cuisine") + ".")
        return SkillResult(f"Found {len(alts)} alternatives via shared ingredients/cuisine", {"alternatives": alts}, facts,
                           [alts[0]["base_score"]] + [a["score"] for a in alts], [r["title"]] + [a["title"] for a in alts],
                           evidence=alts)

    def review(self, ctx):
        res = policy.review(self.app, ctx.user_id, ctx.trace, trigger="chat")
        facts = {"aligned": ["Your behaviour is consistent with your saved goal."],
                 "uncertain": ["No conflict, but some revealed priorities are still uncertain; recommendations rely mainly on your saved goal."],
                 "conflict": [f"Possible conflict on {CRITERION_LABELS[c['criterion']].lower()}: saved {c['stated']:.2f}, "
                              f"behaviour suggests {c['revealed']:.2f}." for c in res["conflicts"][:2]],
                 "insufficient_data": ["Not enough data yet."]}[res["status"]]
        for d in res["decisions"]:
            facts.append(f"I raised: {d['title']} (it waits for you; nothing was changed).")
        nums = [round(x, 2) for c in res["conflicts"][:2] for x in (c["stated"], c["revealed"])]
        return SkillResult(f"Supervisor review: {res['status']}", {"review": res, "decisions": res["decisions"]}, facts, nums)

    def propose_profile_change(self, ctx, op, value):
        app = self.app
        prof = app.store.profile(ctx.user_id)
        declared = json.loads(json.dumps(prof["declared"]))
        human = value.replace("_", " ")
        if op == "add_allergen":
            declared["allergens"] = sorted(set(declared.get("allergens", [])) | {value})
            title = f"Add allergy: {human}"
        elif op == "remove_allergen":
            declared["allergens"] = [a for a in declared.get("allergens", []) if a != value]
            title = f"Remove allergy: {human}"
        elif op == "add_diet":
            declared["diets"] = sorted(set(declared.get("diets", [])) | {value})
            title = f"Add diet: {human}"
        elif op == "remove_diet":
            declared["diets"] = [a for a in declared.get("diets", []) if a != value]
            title = f"Remove diet: {human}"
        elif op == "add_dislike":
            declared["disliked_ingredients"] = [d for d in declared.get("disliked_ingredients", []) if d["name"] != value] + \
                                               [{"name": value, "strict": True}]
            title = f"Never include: {human}"
        else:
            declared["disliked_ingredients"] = [d for d in declared.get("disliked_ingredients", []) if d["name"] != value]
            title = f"Allow {human} again"
        from ..constraints import eligible
        before = eligible(app.catalogue, prof["declared"])[1]["eligible_count"]
        after = eligible(app.catalogue, declared)[1]["eligible_count"]
        d = app.add_decision(ctx.user_id, "profile_change", title, {
            "topic": f"profile:{op}:{value}", "op": op, "value": value, "proposed_declared": declared,
            "explanation": (f"Mandatory rules are only changed when you confirm. With this change "
                            f"{after} recipes would be eligible (now {before})."),
            "eligible_before": before, "eligible_after": after}, "assistant")
        return SkillResult(f"Prepared a profile change for confirmation: {title}", {"decisions": [d]},
                           [f"I prepared the change '{title}'. It is not applied until you confirm it.",
                            f"Eligible recipes would go from {before} to {after}."], [before, after],
                           evidence={"decision_id": d["decision_id"]})
