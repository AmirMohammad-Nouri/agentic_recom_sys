"""Food knowledge graph: schema, projection from catalogue + event log, query templates.

Schema (node label: key property)
  Recipe {uid=recipe:<id>}      title, cuisine, meal, n_ingredients, co2e_kg_per_kg, kcal,
                                health_grade, fsa_score, image_url, score_h, score_s, score_t
  Ingredient {ingredient:<n>}   name, category (food group), co2e_kg_per_kg (group factor)
  Category {category:<group>}   label, co2e_kg_per_kg, source
  Cuisine, Category, Allergen, Diet
  User {user:<id>}
  Interaction {interaction:<event_id>}  type (like|save|choose|reject|answer|profile_updated|
                                goal_saved|proposal_accepted ...), reason, ts, meal, place
  Request {request:<id>}        ts, origin, mode
  Decision {decision:<id>}      kind (proposal|question|conflict|update_request), status, ts, source
  Goal {goal:<user>:<version>}  weights, diversity, mode, source, ts

Relationships
  (Recipe)-[:CONTAINS {grams}]->(Ingredient)       (Recipe)-[:OF_CUISINE]->(Cuisine)
  (Ingredient)-[:IN_CATEGORY]->(Category)          (Ingredient)-[:HAS_ALLERGEN]->(Allergen)
  (Recipe)-[:HAS_ALLERGEN]->(Allergen)             (Recipe)-[:SUITS]->(Diet)
  (Recipe)-[:SIMILAR_TO {score}]->(Recipe)
  (User)-[:PERFORMED]->(Interaction)-[:ON]->(Recipe)
  (User)-[:DECLARED {kind, strict}]->(Allergen|Diet|Ingredient|Cuisine)    current profile only
  (User)-[:SET_GOAL]->(Goal)                       (User)-[:RECEIVED]->(Request)
  (Request)-[:RECOMMENDED {rank, utility}]->(Recipe)
  (User)-[:SUBJECT_OF]->(Decision)-[:BASED_ON]->(Interaction)
  (Decision)-[:ABOUT]->(Recipe)

The graph is a PROJECTION: it can be dropped and rebuilt from the SQLite event log. Only
fixed, parameterised query templates are exposed to the supervisor; the language model never
writes graph queries.
"""
import time
from collections import Counter, defaultdict

from ..constraints import SUPPORTED_DIETS
from ..features import health_grade

INTERACTION_EVENTS = ("feedback", "question_answered", "profile_created", "profile_updated",
                      "goal_saved", "proposal_accepted", "proposal_dismissed", "profile_correction")
LABEL_OF_PREFIX = {"recipe": "Recipe", "ingredient": "Ingredient", "cuisine": "Cuisine",
                   "category": "Category", "allergen": "Allergen", "diet": "Diet", "user": "User",
                   "interaction": "Interaction", "request": "Request", "decision": "Decision",
                   "goal": "Goal"}


def rid_uid(rid):
    return f"recipe:{rid}"


class GraphProjection:
    """Projection logic written against five primitives that each backend implements."""
    backend = "abstract"

    # primitives: merge_node, merge_edge, delete_out_edges, flush, clear
    def merge_node(self, uid, label, props):
        raise NotImplementedError

    def merge_edge(self, src, rel, dst, props=None):
        raise NotImplementedError

    def delete_out_edges(self, src, rel):
        raise NotImplementedError

    def flush(self):
        pass

    def clear(self):
        raise NotImplementedError

    # ---- catalogue -------------------------------------------------------------------
    def load_catalogue(self, catalogue, scaler):
        groups = catalogue.groups or {}
        for key, g in groups.items():
            self.merge_node(f"category:{key}", "Category", {"name": key, "label": g.get("label"),
                                                           "co2e_kg_per_kg": g.get("co2e"), "source": g.get("source")})
        for d in SUPPORTED_DIETS:
            self.merge_node(f"diet:{d}", "Diet", {"name": d})
        seen = set()
        for r in catalogue.recipes:
            u = rid_uid(r["id"])
            sc = scaler.scores(r)
            fp = r.get("footprint") or {}
            self.merge_node(u, "Recipe", {
                "recipe_id": r["id"], "title": r["title"], "cuisine": r["cuisine"], "meal": r.get("meal"),
                "n_ingredients": r.get("n_ingredients"), "co2e_kg_per_kg": fp.get("kg_co2e_per_kg"),
                "kcal": r.get("kcal_per_serving"), "fsa_score": r.get("fsa_score"),
                "health_grade": health_grade(sc["h"]), "image_url": r.get("image_url"),
                **{f"score_{k}": sc[k] for k in ("h", "s", "t")}})
            self.merge_node(f"cuisine:{r['cuisine']}", "Cuisine", {"name": r["cuisine"]})
            self.merge_edge(u, "OF_CUISINE", f"cuisine:{r['cuisine']}")
            for i in r["ingredients"]:
                iid = f"ingredient:{i['name']}"
                if iid not in seen:
                    seen.add(iid)
                    cat = i.get("category") or "unclassified"
                    self.merge_node(iid, "Ingredient", {"name": i["name"], "category": cat,
                                                        "co2e_kg_per_kg": i.get("co2e_factor")})
                    self.merge_node(f"category:{cat}", "Category", {"name": cat})
                    self.merge_edge(iid, "IN_CATEGORY", f"category:{cat}")
                    for a in i.get("allergens") or []:
                        self.merge_node(f"allergen:{a}", "Allergen", {"name": a})
                        self.merge_edge(iid, "HAS_ALLERGEN", f"allergen:{a}")
                self.merge_edge(u, "CONTAINS", iid, {"grams": i["grams"]})
            for a in r.get("allergens") or []:
                self.merge_node(f"allergen:{a}", "Allergen", {"name": a})
                self.merge_edge(u, "HAS_ALLERGEN", f"allergen:{a}")
            for d, ok in (r.get("diet") or {}).items():
                if ok:
                    self.merge_edge(u, "SUITS", f"diet:{d}")
        # neighbours are precomputed by the ingest step (vectorised); fall back to pairwise for
        # small catalogues without them
        for r in catalogue.recipes:
            sims = r.get("similar")
            if sims is None and len(catalogue) <= 500:
                sims = sorted(([o["id"], catalogue.similarity(r, o)] for o in catalogue.recipes
                               if o["id"] != r["id"]), key=lambda x: -x[1])[:3]
            for oid, sc_ in sims or []:
                if sc_ >= 0.3 and oid in catalogue.by_id:
                    self.merge_edge(rid_uid(r["id"]), "SIMILAR_TO", rid_uid(oid), {"score": round(sc_, 3)})
        self.flush()

    # ---- events --------------------------------------------------------------------
    def record_event(self, ev, catalogue=None):
        typ, pl, uid = ev["type"], ev["payload"], f"user:{ev['user_id']}"
        if typ not in INTERACTION_EVENTS:
            return
        self.merge_node(uid, "User", {"user_id": ev["user_id"]})
        iid = f"interaction:{ev['event_id']}"
        ctx = ev.get("context") or {}
        kind = pl.get("type") if typ == "feedback" else ("answer" if typ == "question_answered" else typ)
        props = {"event_id": ev["event_id"], "type": kind, "event_type": typ, "ts": ev["ts"],
                 "reason": pl.get("reason"), "meal": ctx.get("meal"), "place": ctx.get("place"),
                 "request_id": pl.get("request_id")}
        if typ == "question_answered":
            props.update({"winner": pl.get("winner"), "loser": pl.get("loser"),
                          "decision_id": pl.get("decision_id")})
        self.merge_node(iid, "Interaction", props)
        self.merge_edge(uid, "PERFORMED", iid)
        if pl.get("recipe_id"):
            self.merge_edge(iid, "ON", rid_uid(pl["recipe_id"]))
        if typ in ("profile_created", "profile_updated"):
            self._declare(uid, pl["declared"])
        if typ in ("goal_saved", "proposal_accepted") or (typ == "profile_created" and pl.get("weights")):
            v = pl.get("goal_version", 0)
            gid = f"goal:{ev['user_id']}:{v}"
            self.merge_node(gid, "Goal", {"version": v, "ts": ev["ts"], "source": typ,
                                          **{f"w_{k}": w for k, w in pl["weights"].items()}})
            self.merge_edge(uid, "SET_GOAL", gid)
        self.flush()

    def _declare(self, uid, d):
        self.delete_out_edges(uid, "DECLARED")
        for a in d.get("allergens", []):
            self.merge_node(f"allergen:{a}", "Allergen", {"name": a})
            self.merge_edge(uid, "DECLARED", f"allergen:{a}", {"kind": "allergy", "strict": True})
        for x in d.get("diets", []):
            self.merge_edge(uid, "DECLARED", f"diet:{x}", {"kind": "diet", "strict": True})
        for x in d.get("disliked_ingredients", []):
            self.merge_node(f"ingredient:{x['name']}", "Ingredient", {"name": x["name"]})
            self.merge_edge(uid, "DECLARED", f"ingredient:{x['name']}",
                            {"kind": "dislike", "strict": bool(x.get("strict"))})
        for x in d.get("liked_ingredients", []):
            self.merge_node(f"ingredient:{x}", "Ingredient", {"name": x})
            self.merge_edge(uid, "DECLARED", f"ingredient:{x}", {"kind": "like", "strict": False})
        for x in d.get("preferred_cuisines", []):
            self.merge_node(f"cuisine:{x}", "Cuisine", {"name": x})
            self.merge_edge(uid, "DECLARED", f"cuisine:{x}", {"kind": "preferred_cuisine", "strict": False})

    def record_request(self, user_id, result):
        rq = f"request:{result['request_id']}"
        self.merge_node(f"user:{user_id}", "User", {"user_id": user_id})
        self.merge_node(rq, "Request", {"request_id": result["request_id"], "ts": time.time(),
                                        "origin": result.get("origin", "ui"),
                                        "mode": result["config"]["mode"]})
        self.merge_edge(f"user:{user_id}", "RECEIVED", rq)
        for r in result.get("results", []):
            self.merge_edge(rq, "RECOMMENDED", rid_uid(r["recipe_id"]),
                            {"rank": r["final_rank"], "utility": round(r["utility"], 4)})
        self.flush()

    def record_decision(self, d):
        did = f"decision:{d['decision_id']}"
        self.merge_node(f"user:{d['user_id']}", "User", {"user_id": d["user_id"]})
        self.merge_node(did, "Decision", {"decision_id": d["decision_id"], "kind": d["kind"],
                                          "title": d["title"], "status": d["status"], "ts": d["ts"],
                                          "source": d["source"]})
        self.merge_edge(f"user:{d['user_id']}", "SUBJECT_OF", did)
        for eid in (d.get("body") or {}).get("evidence_events", [])[:30]:
            self.merge_edge(did, "BASED_ON", f"interaction:{eid}")
        for rid in (d.get("body") or {}).get("recipes", [])[:10]:
            self.merge_edge(did, "ABOUT", rid_uid(rid))
        self.flush()

    def update_decision_status(self, decision_id, status):
        d = self.node(f"decision:{decision_id}")
        if d:
            self.merge_node(f"decision:{decision_id}", "Decision", {**d["props"], "status": status})
            self.flush()


class MemoryGraph(GraphProjection):
    """In-process graph with the same schema and query templates as the Neo4j backend."""
    backend = "memory"

    def __init__(self):
        self.clear()

    # ---- primitives --------------------------------------------------------------------
    def clear(self):
        self.nodes = {}
        self.out = defaultdict(dict)    # src -> {(rel, dst): props}
        self.inc = defaultdict(dict)    # dst -> {(rel, src): props}

    def merge_node(self, uid, label, props):
        n = self.nodes.setdefault(uid, {"uid": uid, "label": label, "props": {}})
        n["props"].update({k: v for k, v in props.items() if v is not None})

    def merge_edge(self, src, rel, dst, props=None):
        if dst not in self.nodes:
            prefix = dst.split(":", 1)[0]
            self.merge_node(dst, LABEL_OF_PREFIX.get(prefix, "Node"), {})
        self.out[src][(rel, dst)] = dict(props or {})
        self.inc[dst][(rel, src)] = self.out[src][(rel, dst)]

    def delete_out_edges(self, src, rel):
        for (r, dst) in [k for k in self.out[src] if k[0] == rel]:
            del self.out[src][(r, dst)]
            self.inc[dst].pop((r, src), None)

    # ---- helpers ---------------------------------------------------------------------
    def node(self, uid):
        return self.nodes.get(uid)

    def _o(self, uid, rel):
        return [(d, p) for (r, d), p in self.out.get(uid, {}).items() if r == rel]

    def _i(self, uid, rel):
        return [(s, p) for (r, s), p in self.inc.get(uid, {}).items() if r == rel]

    def _recipe_brief(self, ruid):
        p = self.nodes[ruid]["props"]
        return {"recipe_id": p.get("recipe_id"), "title": p.get("title"), "cuisine": p.get("cuisine")}

    def _interaction_row(self, iuid):
        p = dict(self.nodes[iuid]["props"])
        on = self._o(iuid, "ON")
        if on:
            p.update(self._recipe_brief(on[0][0]))
        return p

    # ---- query templates -------------------------------------------------------------
    def timeline(self, user_id, since=None, until=None, types=None, recipe_id=None, ingredient=None,
                 cuisine=None, meal=None, place=None, limit=50):
        rows = []
        for iuid, _ in self._o(f"user:{user_id}", "PERFORMED"):
            p = self.nodes[iuid]["props"]
            if since is not None and p.get("ts", 0) < since:
                continue
            if until is not None and p.get("ts", 0) > until:
                continue
            if types and p.get("type") not in types:
                continue
            if meal and p.get("meal") != meal:
                continue
            if place and p.get("place") != place:
                continue
            on = [d for d, _ in self._o(iuid, "ON")]
            if (recipe_id or ingredient or cuisine) and not on:
                continue
            if recipe_id and on[0] != rid_uid(recipe_id):
                continue
            if ingredient and (f"ingredient:{ingredient}" not in {d for d, _ in self._o(on[0], "CONTAINS")}):
                continue
            if cuisine and self.nodes[on[0]]["props"].get("cuisine", "").lower() != cuisine.lower():
                continue
            rows.append(self._interaction_row(iuid))
        rows.sort(key=lambda r: -r.get("ts", 0))
        return rows[:limit]

    def ingredient_profile(self, name, user_id=None):
        iuid = f"ingredient:{name}"
        n = self.node(iuid)
        if n is None:
            return None
        recipes = [s for s, _ in self._i(iuid, "CONTAINS")]
        out = {"ingredient": n["props"], "category": [d.split(":", 1)[1] for d, _ in self._o(iuid, "IN_CATEGORY")],
               "allergens": [d.split(":", 1)[1] for d, _ in self._o(iuid, "HAS_ALLERGEN")],
               "recipes": sorted((self._recipe_brief(r) for r in recipes), key=lambda x: x["recipe_id"])}
        if user_id:
            out["user_interactions"] = dict(Counter(
                self.nodes[i]["props"].get("type") for r in recipes for i, _ in self._i(r, "ON")
                if any(u == f"user:{user_id}" for u, _ in self._i(i, "PERFORMED"))))
            out["user_declared"] = [p for (rel, d), p in self.out.get(f"user:{user_id}", {}).items()
                                    if rel == "DECLARED" and d == iuid]
        return out

    def recipe_context(self, recipe_id, user_id=None):
        u = rid_uid(recipe_id)
        n = self.node(u)
        if n is None:
            return None
        out = {"recipe": n["props"],
               "ingredients": sorted(({"name": d.split(":", 1)[1], "grams": p.get("grams")}
                                      for d, p in self._o(u, "CONTAINS")), key=lambda x: -(x["grams"] or 0)),
               "allergens": sorted(d.split(":", 1)[1] for d, _ in self._o(u, "HAS_ALLERGEN")),
               "diets": sorted(d.split(":", 1)[1] for d, _ in self._o(u, "SUITS")),
               "similar": sorted(({**self._recipe_brief(d), "score": p.get("score")}
                                  for d, p in self._o(u, "SIMILAR_TO")), key=lambda x: -x["score"])}
        if user_id:
            mine = [i for i, _ in self._i(u, "ON")
                    if any(s == f"user:{user_id}" for s, _ in self._i(i, "PERFORMED"))]
            out["user_interactions"] = sorted((self.nodes[i]["props"] for i in mine), key=lambda p: -p.get("ts", 0))
            out["times_recommended"] = sum(1 for rq, _ in self._i(u, "RECOMMENDED")
                                           if any(s == f"user:{user_id}" for s, _ in self._i(rq, "RECEIVED")))
        return out

    def alternatives(self, recipe_id, criterion, k=3):
        """Recipes that share ingredients or a cuisine with `recipe_id` and score better on
        `criterion` (h, s or t). Used for counterfactual questions ("what is similar but greener?")."""
        u = rid_uid(recipe_id)
        if u not in self.nodes:
            return []
        base = self.nodes[u]["props"].get(f"score_{criterion}")
        mine = {d for d, _ in self._o(u, "CONTAINS")}
        rows = []
        for other, n in self.nodes.items():
            if n["label"] != "Recipe" or other == u:
                continue
            theirs = {d for d, _ in self._o(other, "CONTAINS")}
            shared = mine & theirs
            same_cuisine = n["props"].get("cuisine") == self.nodes[u]["props"].get("cuisine")
            sc = n["props"].get(f"score_{criterion}")
            if (shared or same_cuisine) and sc is not None and base is not None and sc > base:
                rows.append({**self._recipe_brief(other), "score": round(sc, 3), "base_score": round(base, 3),
                             "shared_ingredients": sorted(s.split(":", 1)[1] for s in shared),
                             "same_cuisine": same_cuisine})
        rows.sort(key=lambda r: (-len(r["shared_ingredients"]) - r["same_cuisine"], -r["score"], r["recipe_id"]))
        return rows[:k]

    def concept_evidence(self, user_id, concept, limit=30):
        """Interactions on recipes that contain an ingredient / belong to a cuisine."""
        kind, _, name = concept.partition(":")
        if kind == "ingredient":
            return self.timeline(user_id, ingredient=name, limit=limit)
        if kind == "cuisine":
            return self.timeline(user_id, cuisine=name, limit=limit)
        if kind == "category":
            rows = []
            for r in self.timeline(user_id, limit=500):
                if r.get("recipe_id") and any(
                        f"category:{name}" in {c for c, _ in self._o(i, "IN_CATEGORY")}
                        for i, _ in self._o(rid_uid(r["recipe_id"]), "CONTAINS")):
                    rows.append(r)
            return rows[:limit]
        return []

    def decisions(self, user_id, since=None, kind=None, limit=50):
        rows = []
        for d, _ in self._o(f"user:{user_id}", "SUBJECT_OF"):
            p = dict(self.nodes[d]["props"])
            if since is not None and p.get("ts", 0) < since:
                continue
            if kind and p.get("kind") != kind:
                continue
            p["based_on"] = len(self._o(d, "BASED_ON"))
            p["about"] = [self._recipe_brief(r) for r, _ in self._o(d, "ABOUT")]
            rows.append(p)
        return sorted(rows, key=lambda r: -r.get("ts", 0))[:limit]

    def recommended(self, user_id, since=None, limit=20):
        rows = []
        for rq, _ in self._o(f"user:{user_id}", "RECEIVED"):
            p = self.nodes[rq]["props"]
            if since is not None and p.get("ts", 0) < since:
                continue
            for r, ep in self._o(rq, "RECOMMENDED"):
                rows.append({**self._recipe_brief(r), "rank": ep.get("rank"), "ts": p.get("ts"),
                             "request_id": p.get("request_id")})
        return sorted(rows, key=lambda r: (-r["ts"], r["rank"]))[:limit]

    def search_nodes(self, text, labels=None, limit=20):
        t = text.lower().strip()
        rows = []
        for uid, n in self.nodes.items():
            if labels and n["label"] not in labels:
                continue
            name = str(n["props"].get("title") or n["props"].get("name") or uid)
            if t in name.lower() or t in uid.lower():
                rows.append({"uid": uid, "label": n["label"], "name": name})
        rows.sort(key=lambda r: (not r["name"].lower().startswith(t), len(r["name"]), r["uid"]))
        return rows[:limit]

    def neighborhood(self, uid, depth=1, limit=120):
        if uid not in self.nodes:
            return {"nodes": [], "edges": []}
        seen, frontier, edges = {uid}, [uid], []
        for _ in range(depth):
            nxt = []
            for u in frontier:
                links = [(u, r, d, p) for (r, d), p in self.out.get(u, {}).items()] + \
                        [(s, r, u, p) for (r, s), p in self.inc.get(u, {}).items()]
                for s, r, d, p in links:
                    if len(seen) >= limit and (s not in seen or d not in seen):
                        continue
                    edges.append({"source": s, "target": d, "type": r, "props": p})
                    for x in (s, d):
                        if x not in seen:
                            seen.add(x)
                            nxt.append(x)
            frontier = nxt
        uniq = {(e["source"], e["type"], e["target"]): e for e in edges}
        return {"nodes": [{"uid": u, "label": self.nodes[u]["label"], "props": self.nodes[u]["props"]}
                          for u in seen if u in self.nodes],
                "edges": list(uniq.values())}

    def stats(self):
        rels = Counter(r for edges in self.out.values() for (r, _) in edges)
        return {"backend": self.backend, "nodes": len(self.nodes),
                "relationships": sum(rels.values()),
                "labels": dict(Counter(n["label"] for n in self.nodes.values())),
                "relationship_types": dict(rels)}
