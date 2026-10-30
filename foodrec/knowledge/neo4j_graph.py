"""Neo4j backend: same schema, projection and query templates as MemoryGraph, in Cypher.

Every node carries a unique `uid` (see graph.py) plus its schema label. Writes are buffered
and flushed with UNWIND batches. All queries are fixed templates with parameters; no query
text is ever produced by the language model.
"""
import json
from collections import defaultdict

from .graph import LABEL_OF_PREFIX, GraphProjection, rid_uid

LABELS = sorted(set(LABEL_OF_PREFIX.values()))


def _clean(props):
    out = {}
    for k, v in (props or {}).items():
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, int, float, bool)) else json.dumps(v)
    return out


class Neo4jGraph(GraphProjection):
    backend = "neo4j"

    def __init__(self, uri, user, password, database="neo4j"):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.driver.verify_connectivity()
        self.database = database
        self._nodes = defaultdict(list)
        self._edges = defaultdict(list)
        for label in LABELS:
            self._run(f"CREATE CONSTRAINT {label.lower()}_uid IF NOT EXISTS "
                      f"FOR (n:{label}) REQUIRE n.uid IS UNIQUE")
        self._run("CREATE INDEX interaction_ts IF NOT EXISTS FOR (n:Interaction) ON (n.ts)")

    def close(self):
        self.driver.close()

    def _run(self, q, **params):
        records, _, _ = self.driver.execute_query(q, params, database_=self.database)
        return [r.data() for r in records]

    # ---- primitives ----------------------------------------------------------------
    def merge_node(self, uid, label, props):
        self._nodes[label].append({"uid": uid, "props": _clean(props)})

    def merge_edge(self, src, rel, dst, props=None):
        sl = LABEL_OF_PREFIX[src.split(":", 1)[0]]
        dl = LABEL_OF_PREFIX[dst.split(":", 1)[0]]
        self._edges[(sl, rel, dl)].append({"src": src, "dst": dst, "props": _clean(props)})

    def delete_out_edges(self, src, rel):
        self.flush()
        self._run(f"MATCH (s {{uid:$uid}})-[r:{rel}]->() DELETE r", uid=src)

    def flush(self):
        for label, rows in self._nodes.items():
            self._run(f"UNWIND $rows AS row MERGE (n:{label} {{uid: row.uid}}) SET n += row.props", rows=rows)
        for (sl, rel, dl), rows in self._edges.items():
            # endpoints are MERGEd so an edge to a not-yet-seen node never fails
            self._run(f"UNWIND $rows AS row MERGE (s:{sl} {{uid: row.src}}) MERGE (d:{dl} {{uid: row.dst}}) "
                      f"MERGE (s)-[r:{rel}]->(d) SET r += row.props", rows=rows)
        self._nodes.clear()
        self._edges.clear()

    def clear(self):
        self._run("MATCH (n) DETACH DELETE n")

    def node(self, uid):
        rows = self._run("MATCH (n {uid:$uid}) RETURN labels(n)[0] AS label, properties(n) AS props", uid=uid)
        if not rows:
            return None
        props = {k: v for k, v in rows[0]["props"].items() if k != "uid"}
        return {"uid": uid, "label": rows[0]["label"], "props": props}

    # ---- query templates ---------------------------------------------------------------
    _INTERACTION_RETURN = ("i{.*} AS i, r.recipe_id AS recipe_id, r.title AS title, r.cuisine AS cuisine")

    def _irows(self, rows):
        out = []
        for row in rows:
            p = {k: v for k, v in row["i"].items() if k != "uid"}
            if row.get("recipe_id"):
                p.update({"recipe_id": row["recipe_id"], "title": row["title"], "cuisine": row["cuisine"]})
            out.append(p)
        return out

    def timeline(self, user_id, since=None, until=None, types=None, recipe_id=None, ingredient=None,
                 cuisine=None, meal=None, place=None, limit=50):
        q = """
        MATCH (:User {uid:$user})-[:PERFORMED]->(i:Interaction)
        OPTIONAL MATCH (i)-[:ON]->(r:Recipe)
        WITH i, r
        WHERE ($since IS NULL OR i.ts >= $since) AND ($until IS NULL OR i.ts <= $until)
          AND ($types IS NULL OR i.type IN $types)
          AND ($meal IS NULL OR i.meal = $meal) AND ($place IS NULL OR i.place = $place)
          AND ($recipe IS NULL OR r.uid = $recipe)
          AND ($cuisine IS NULL OR toLower(r.cuisine) = toLower($cuisine))
          AND ($ingredient IS NULL OR EXISTS { (r)-[:CONTAINS]->(:Ingredient {uid:$ingredient}) })
        RETURN """ + self._INTERACTION_RETURN + " ORDER BY i.ts DESC LIMIT $limit"
        return self._irows(self._run(
            q, user=f"user:{user_id}", since=since, until=until, types=types or None, meal=meal,
            place=place, recipe=rid_uid(recipe_id) if recipe_id else None, cuisine=cuisine,
            ingredient=f"ingredient:{ingredient}" if ingredient else None, limit=limit))

    def ingredient_profile(self, name, user_id=None):
        rows = self._run("""
        MATCH (g:Ingredient {uid:$uid})
        OPTIONAL MATCH (g)-[:IN_CATEGORY]->(c:Category)
        OPTIONAL MATCH (g)-[:HAS_ALLERGEN]->(a:Allergen)
        OPTIONAL MATCH (r:Recipe)-[:CONTAINS]->(g)
        RETURN g{.*} AS g, collect(DISTINCT c.name) AS cats, collect(DISTINCT a.name) AS allergens,
               collect(DISTINCT {recipe_id: r.recipe_id, title: r.title, cuisine: r.cuisine}) AS recipes
        """, uid=f"ingredient:{name}")
        if not rows:
            return None
        row = rows[0]
        out = {"ingredient": {k: v for k, v in row["g"].items() if k != "uid"}, "category": row["cats"],
               "allergens": sorted(row["allergens"]),
               "recipes": sorted((r for r in row["recipes"] if r["recipe_id"]), key=lambda x: x["recipe_id"])}
        if user_id:
            counts = self._run("""
            MATCH (:User {uid:$user})-[:PERFORMED]->(i:Interaction)-[:ON]->(:Recipe)-[:CONTAINS]->(:Ingredient {uid:$uid})
            RETURN i.type AS type, count(*) AS n""", user=f"user:{user_id}", uid=f"ingredient:{name}")
            out["user_interactions"] = {c["type"]: c["n"] for c in counts}
            out["user_declared"] = [r["p"] for r in self._run(
                "MATCH (:User {uid:$user})-[d:DECLARED]->(:Ingredient {uid:$uid}) RETURN properties(d) AS p",
                user=f"user:{user_id}", uid=f"ingredient:{name}")]
        return out

    def recipe_context(self, recipe_id, user_id=None):
        rows = self._run("""
        MATCH (r:Recipe {uid:$uid})
        OPTIONAL MATCH (r)-[c:CONTAINS]->(g:Ingredient)
        WITH r, collect({name: g.name, grams: c.grams}) AS ingredients
        OPTIONAL MATCH (r)-[:HAS_ALLERGEN]->(a:Allergen)
        WITH r, ingredients, collect(DISTINCT a.name) AS allergens
        OPTIONAL MATCH (r)-[:SUITS]->(d:Diet)
        WITH r, ingredients, allergens, collect(DISTINCT d.name) AS diets
        OPTIONAL MATCH (r)-[s:SIMILAR_TO]->(o:Recipe)
        RETURN r{.*} AS r, ingredients, allergens, diets,
               collect({recipe_id: o.recipe_id, title: o.title, cuisine: o.cuisine, score: s.score}) AS similar
        """, uid=rid_uid(recipe_id))
        if not rows:
            return None
        row = rows[0]
        out = {"recipe": {k: v for k, v in row["r"].items() if k != "uid"},
               "ingredients": sorted((i for i in row["ingredients"] if i["name"]), key=lambda x: -(x["grams"] or 0)),
               "allergens": sorted(row["allergens"]), "diets": sorted(row["diets"]),
               "similar": sorted((s for s in row["similar"] if s["recipe_id"]), key=lambda x: -x["score"])}
        if user_id:
            out["user_interactions"] = [
                {k: v for k, v in r["i"].items() if k != "uid"} for r in self._run("""
                MATCH (:User {uid:$user})-[:PERFORMED]->(i:Interaction)-[:ON]->(:Recipe {uid:$uid})
                RETURN i{.*} AS i ORDER BY i.ts DESC""", user=f"user:{user_id}", uid=rid_uid(recipe_id))]
            out["times_recommended"] = self._run("""
                MATCH (:User {uid:$user})-[:RECEIVED]->(:Request)-[:RECOMMENDED]->(:Recipe {uid:$uid})
                RETURN count(*) AS n""", user=f"user:{user_id}", uid=rid_uid(recipe_id))[0]["n"]
        return out

    def alternatives(self, recipe_id, criterion, k=3):
        if criterion not in ("h", "s", "t"):
            return []
        prop = f"score_{criterion}"
        rows = self._run(f"""
        MATCH (r:Recipe {{uid:$uid}}), (o:Recipe)
        WHERE o <> r AND o.{prop} IS NOT NULL AND r.{prop} IS NOT NULL AND o.{prop} > r.{prop}
        OPTIONAL MATCH (r)-[:CONTAINS]->(g:Ingredient)<-[:CONTAINS]-(o)
        WITH r, o, collect(g.name) AS shared, o.cuisine = r.cuisine AS same_cuisine
        WHERE size(shared) > 0 OR same_cuisine
        RETURN o.recipe_id AS recipe_id, o.title AS title, o.cuisine AS cuisine,
               round(o.{prop}, 3) AS score, round(r.{prop}, 3) AS base_score,
               shared AS shared_ingredients, same_cuisine
        ORDER BY size(shared) + toInteger(same_cuisine) DESC, score DESC, recipe_id
        LIMIT $k""", uid=rid_uid(recipe_id), k=k)
        for r in rows:
            r["shared_ingredients"] = sorted(r["shared_ingredients"])
        return rows

    def concept_evidence(self, user_id, concept, limit=30):
        kind, _, name = concept.partition(":")
        if kind == "ingredient":
            return self.timeline(user_id, ingredient=name, limit=limit)
        if kind == "cuisine":
            return self.timeline(user_id, cuisine=name, limit=limit)
        if kind == "category":
            return self._irows(self._run("""
            MATCH (:User {uid:$user})-[:PERFORMED]->(i:Interaction)-[:ON]->(r:Recipe)
            WHERE EXISTS { (r)-[:CONTAINS]->(:Ingredient)-[:IN_CATEGORY]->(:Category {uid:$cat}) }
            RETURN """ + self._INTERACTION_RETURN + " ORDER BY i.ts DESC LIMIT $limit",
                user=f"user:{user_id}", cat=f"category:{name}", limit=limit))
        return []

    def decisions(self, user_id, since=None, kind=None, limit=50):
        rows = self._run("""
        MATCH (:User {uid:$user})-[:SUBJECT_OF]->(d:Decision)
        WHERE ($since IS NULL OR d.ts >= $since) AND ($kind IS NULL OR d.kind = $kind)
        OPTIONAL MATCH (d)-[:ABOUT]->(r:Recipe)
        WITH d, collect({recipe_id: r.recipe_id, title: r.title, cuisine: r.cuisine}) AS about
        RETURN d{.*} AS d, COUNT { (d)-[:BASED_ON]->() } AS based_on, about
        ORDER BY d.ts DESC LIMIT $limit""", user=f"user:{user_id}", since=since, kind=kind, limit=limit)
        return [{**{k: v for k, v in r["d"].items() if k != "uid"}, "based_on": r["based_on"],
                 "about": [a for a in r["about"] if a["recipe_id"]]} for r in rows]

    def recommended(self, user_id, since=None, limit=20):
        return self._run("""
        MATCH (:User {uid:$user})-[:RECEIVED]->(q:Request)-[e:RECOMMENDED]->(r:Recipe)
        WHERE $since IS NULL OR q.ts >= $since
        RETURN r.recipe_id AS recipe_id, r.title AS title, r.cuisine AS cuisine, e.rank AS rank,
               q.ts AS ts, q.request_id AS request_id
        ORDER BY ts DESC, rank LIMIT $limit""", user=f"user:{user_id}", since=since, limit=limit)

    def search_nodes(self, text, labels=None, limit=20):
        return self._run("""
        MATCH (n) WHERE ($labels IS NULL OR labels(n)[0] IN $labels)
          AND (toLower(coalesce(n.title, n.name, n.uid)) CONTAINS toLower($t) OR toLower(n.uid) CONTAINS toLower($t))
        WITH n, coalesce(n.title, n.name, n.uid) AS name
        RETURN n.uid AS uid, labels(n)[0] AS label, name
        ORDER BY NOT toLower(name) STARTS WITH toLower($t), size(name), uid LIMIT $limit""",
                         t=text.strip(), labels=labels or None, limit=limit)

    def neighborhood(self, uid, depth=1, limit=120):
        depth = max(1, min(int(depth), 2))
        rows = self._run(f"""
        MATCH p=(c {{uid:$uid}})-[*1..{depth}]-(m)
        WITH p LIMIT $limit
        UNWIND relationships(p) AS r
        WITH DISTINCT r
        RETURN startNode(r).uid AS source, type(r) AS type, endNode(r).uid AS target, properties(r) AS props,
               labels(startNode(r))[0] AS sl, properties(startNode(r)) AS sp,
               labels(endNode(r))[0] AS tl, properties(endNode(r)) AS tp""", uid=uid, limit=limit)
        nodes, edges = {}, []
        for r in rows:
            nodes[r["source"]] = {"uid": r["source"], "label": r["sl"], "props": r["sp"]}
            nodes[r["target"]] = {"uid": r["target"], "label": r["tl"], "props": r["tp"]}
            edges.append({"source": r["source"], "target": r["target"], "type": r["type"], "props": r["props"]})
        if not nodes:
            n = self.node(uid)
            if n:
                nodes[uid] = n
        return {"nodes": list(nodes.values()), "edges": edges}

    def stats(self):
        labels = {r["label"]: r["n"] for r in self._run(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n")}
        rels = {r["type"]: r["n"] for r in self._run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n")}
        return {"backend": self.backend, "nodes": sum(labels.values()), "relationships": sum(rels.values()),
                "labels": labels, "relationship_types": rels}
