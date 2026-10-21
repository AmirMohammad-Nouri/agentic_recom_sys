"""Knowledge Graph layer with graph export for 2D visual canvas rendering."""
import logging
import time

logger = logging.getLogger("foodrec.graph")


class KnowledgeGraph:
    def __init__(self, neo4j_uri=None, auth=None):
        self.driver = None
        self.nodes = {}  # node_id -> {id, label, properties}
        self.edges = []  # [{source, target, type, properties}]
        self.mutation_events = []

    def _emit_mutation(self, event_type, details):
        event = {
            "timestamp": time.time(),
            "event_type": event_type,
            "details": details
        }
        self.mutation_events.append(event)
        if len(self.mutation_events) > 100:
            self.mutation_events.pop(0)

    def add_node(self, node_id, label, props=None):
        is_new = node_id not in self.nodes
        self.nodes[node_id] = {"id": node_id, "label": label, "properties": props or {}}
        if is_new:
            self._emit_mutation("NODE_ADDED", {"id": node_id, "label": label, "properties": props or {}})

    def add_edge(self, source, target, rel_type, props=None):
        edge = {"source": source, "target": target, "type": rel_type, "properties": props or {}}
        self.edges.append(edge)
        self._emit_mutation("RELATIONSHIP_CREATED", {"source": source, "target": target, "type": rel_type})

    def seed_from_catalogue(self, catalogue, ingredients_dict=None):
        ingredients_dict = ingredients_dict or {}

        # Seed Primary Nutrient Nodes
        self.add_node("nutrient:protein", "Nutrient", {"name": "Protein", "role": "Macronutrient for muscle repair, recovery and satiety"})
        self.add_node("nutrient:sugar", "Nutrient", {"name": "Sugar", "role": "Simple carbohydrate for rapid glycemic energy"})
        self.add_node("nutrient:fibre", "Nutrient", {"name": "Fibre", "role": "Dietary fiber for digestion and glycemic buffering"})

        for recipe in catalogue.recipes:
            rid = recipe["id"]
            n = recipe.get("nutrition_per_100g", {})
            serv_g = recipe.get("serving_grams", 100)
            serv_mult = serv_g / 100.0
            p_serv = round(n.get("protein_g", 0) * serv_mult, 1)
            s_serv = round(n.get("sugars_g", 0) * serv_mult, 1)

            self.add_node(rid, "Recipe", {
                "title": recipe["title"],
                "cuisine": recipe["cuisine"],
                "protein_per_serving": p_serv,
                "sugars_per_serving": s_serv
            })
            cid = f"cuisine:{recipe['cuisine']}"
            self.add_node(cid, "Cuisine", {"name": recipe["cuisine"]})
            self.add_edge(rid, cid, "BELONGS_TO")

            if p_serv >= 20.0:
                self.add_edge(rid, "nutrient:protein", "HIGH_IN", {"amount_g": p_serv})
            if s_serv >= 10.0:
                self.add_edge(rid, "nutrient:sugar", "HIGH_IN", {"amount_g": s_serv})

            for ing in recipe["ingredients"]:
                iname = ing["name"]
                iid = f"ingredient:{iname}"
                props = {"name": iname, "category": ing.get("category")}
                ing_info = ingredients_dict.get(iname, {})
                if ing_info:
                    props["protein_g"] = ing_info.get("protein_g", 0)
                    props["sugars_g"] = ing_info.get("sugars_g", 0)

                self.add_node(iid, "Ingredient", props)
                self.add_edge(rid, iid, "USES_INGREDIENT", {"grams": ing["grams"]})

                if ing_info.get("protein_g", 0) >= 10.0:
                    self.add_edge(iid, "nutrient:protein", "RICH_IN", {"protein_g_per_100g": ing_info["protein_g"]})
                if ing_info.get("sugars_g", 0) >= 3.0:
                    self.add_edge(iid, "nutrient:sugar", "CONTAINS_SUGAR", {"sugars_g_per_100g": ing_info["sugars_g"]})

                for allergen in ing.get("allergens") or []:
                    aid = f"allergen:{allergen}"
                    self.add_node(aid, "Allergen", {"name": allergen})
                    self.add_edge(iid, aid, "CONTAINS_ALLERGEN")

    def query_nutrient_sources(self, nutrient_name):
        """Query knowledge graph for ingredients and recipes providing a given nutrient."""
        target_node = f"nutrient:{nutrient_name.lower()}"
        recipes = []
        ingredients = []
        for e in self.edges:
            if e["target"] == target_node:
                src_id = e["source"]
                node = self.nodes.get(src_id, {})
                if node.get("label") == "Recipe":
                    recipes.append({"id": src_id, "title": node.get("properties", {}).get("title"), "edge": e})
                elif node.get("label") == "Ingredient":
                    ingredients.append({"id": src_id, "name": node.get("properties", {}).get("name"), "edge": e})
        return {"nutrient": nutrient_name, "recipes": recipes, "ingredients": ingredients}

    def record_user_interaction(self, user_id, recipe_id, action, timestamp=0.0):
        uid = f"user:{user_id}"
        self.add_node(uid, "User", {"user_id": user_id})
        eid = f"action:{user_id}:{int(time.time() * 1000)}"
        self.add_node(eid, "UserAction", {"action": action, "timestamp": timestamp or time.time()})
        self.add_edge(uid, eid, "PERFORMED")
        self.add_edge(eid, recipe_id, "TARGETS")

    def record_profile_update(self, user_id, declared):
        uid = f"user:{user_id}"
        self.add_node(uid, "User", {"user_id": user_id})
        for allergen in declared.get("allergens", []):
            aid = f"allergen:{allergen}"
            self.add_node(aid, "Allergen", {"name": allergen})
            self.add_edge(uid, aid, "DECLARED_ALLERGY")

    def query_user_conflicts(self, user_id):
        uid = f"user:{user_id}"
        actions = [e for e in self.edges if e["source"] == uid and e["type"] == "PERFORMED"]
        results = []
        for act in actions:
            targets = [e for e in self.edges if e["source"] == act["target"] and e["type"] == "TARGETS"]
            act_node = self.nodes.get(act["target"], {})
            for t in targets:
                results.append({"action": act_node.get("properties", {}).get("action"), "recipe_id": t["target"]})
        return results

    def get_graph_stats(self):
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "recent_mutations": self.mutation_events[-10:]
        }

    def get_vis_network_graph(self):
        """Export nodes and edges formatted for 2D vis-network graph rendering."""
        group_colors = {
            "Recipe": "#38bdf8",
            "Cuisine": "#a855f7",
            "Ingredient": "#22c55e",
            "Nutrient": "#e879f9",
            "Allergen": "#ef4444",
            "User": "#f59e0b",
            "UserAction": "#ec4899"
        }

        nodes_list = []
        for nid, n in self.nodes.items():
            lbl = n["properties"].get("title") or n["properties"].get("name") or nid
            nodes_list.append({
                "id": nid,
                "label": f"{n['label']}\n{lbl}",
                "group": n["label"],
                "color": group_colors.get(n["label"], "#64748b"),
                "title": f"Label: {n['label']}<br>Properties: {n['properties']}"
            })

        edges_list = []
        for i, e in enumerate(self.edges):
            edges_list.append({
                "id": f"e{i}",
                "from": e["source"],
                "to": e["target"],
                "label": e["type"],
                "font": {"size": 10, "color": "#94a3b8"}
            })

        return {"nodes": nodes_list, "edges": edges_list}