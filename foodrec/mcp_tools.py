"""MCP (Model Context Protocol) and LangChain Tool Definitions."""
import json
from .ranker import normalize_weights


class MCPToolRegistry:
    def __init__(self, db, cat, scaler, kg, rag, learners, ing_db=None):
        self.db = db
        self.cat = cat
        self.scaler = scaler
        self.kg = kg
        self.rag = rag
        self.learners = learners
        self.ing_db = ing_db or {}
        if not self.ing_db:
            try:
                with open("data/fixture/ingredients.json") as f:
                    self.ing_db = json.load(f)
            except Exception:
                self.ing_db = {}
        self.tools = {}
        self._register_default_tools()

    def register_tool(self, name, description, parameters, handler):
        self.tools[name] = {
            "schema": {
                "name": name,
                "description": description,
                "parameters": parameters
            },
            "handler": handler
        }

    def execute_tool(self, name, args):
        if name not in self.tools:
            return {"error": f"Unknown MCP tool: '{name}'"}
        try:
            return self.tools[name]["handler"](**args)
        except Exception as e:
            return {"error": f"Tool execution failed: {str(e)}"}

    def _register_default_tools(self):
        # 1. Update Profile Tool
        self.register_tool(
            name="update_user_profile",
            description="Updates mandatory user profile constraints such as allergens, diets, and strict limits.",
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "allergens": {"type": "array", "items": {"type": "string"}},
                    "diets": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["user_id"]
            },
            handler=self._handle_update_profile
        )

        # 2. Ingest Custom Knowledge into RAG
        self.register_tool(
            name="ingest_custom_knowledge",
            description="Ingests new dietary facts, guidelines, or articles into the RAG context store.",
            parameters={
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "title": {"type": "string"},
                    "text": {"type": "string"}
                },
                "required": ["doc_id", "title", "text"]
            },
            handler=lambda doc_id, title, text: (
                self.rag.add_custom_document(doc_id, title, text),
                {"status": "success", "ingested": doc_id}
            )[1]
        )

        # 3. Retrieve Food Knowledge
        self.register_tool(
            name="retrieve_food_knowledge",
            description="Retrieves ground-truth nutrient facts and recipe composition guidelines using RAG.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 3}
                },
                "required": ["query"]
            },
            handler=lambda query, top_k=3: {"retrieved_docs": self.rag.search(query, top_k=top_k)}
        )

        # 4. Generate Recommendations
        self.register_tool(
            name="generate_recommendations",
            description="Triggers the multi-criteria recommendation engine with optional nutrient objectives and returns top-k recipes.",
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "mode": {"type": "string", "enum": ["lightweight", "extended"]},
                    "nutrient_goals": {"type": "object"}
                },
                "required": ["user_id"]
            },
            handler=self._handle_generate_recommendations
        )

        # 5. Query Knowledge Graph
        self.register_tool(
            name="query_knowledge_graph",
            description="Queries the Knowledge Graph for entity relations, nutrient sources, or user conflict history.",
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "nutrient": {"type": "string"}
                }
            },
            handler=self._handle_query_knowledge_graph
        )

        # 6. Analyze Recipe Ingredients
        self.register_tool(
            name="analyze_recipe_ingredients",
            description="Provides deep ingredient-level nutritional attribution (protein, sugars, etc.) for a specific recipe.",
            parameters={
                "type": "object",
                "properties": {
                    "recipe_id": {"type": "string"}
                },
                "required": ["recipe_id"]
            },
            handler=self.analyze_recipe_ingredients
        )

    def _handle_update_profile(self, user_id, **kwargs):
        prof = self.db.get_latest_profile(user_id) or {"declared": {}}
        declared = prof.get("declared", {})
        for key in ["allergens", "diets", "disliked_ingredients", "max_prep_minutes", "max_cost_eur"]:
            if kwargs.get(key) is not None:
                declared[key] = kwargs[key]

        ver = self.db.save_profile(user_id, declared)
        self.kg.record_profile_update(user_id, declared)
        return {"status": "success", "profile_version": ver, "declared": declared}

    def _handle_query_knowledge_graph(self, user_id="demo_user", nutrient=None):
        stats = self.kg.get_graph_stats()
        conflicts = self.kg.query_user_conflicts(user_id) if user_id else []
        nutrient_info = self.kg.query_nutrient_sources(nutrient) if nutrient else None
        return {
            "graph_stats": stats,
            "user_conflicts": conflicts,
            "nutrient_info": nutrient_info
        }

    def analyze_recipe_ingredients(self, recipe_id):
        """Audit exact ingredient contributions for protein, sugar, fiber, etc."""
        recipe = self.cat.get(recipe_id)
        if not recipe:
            return {"error": f"Recipe {recipe_id} not found."}

        serv_mult = recipe.get("serving_grams", 100) / 100.0
        n_100 = recipe.get("nutrition_per_100g", {})
        total_p_serv = round(n_100.get("protein_g", 0) * serv_mult, 1)
        total_s_serv = round(n_100.get("sugars_g", 0) * serv_mult, 1)

        breakdown = []
        for ing in recipe.get("ingredients", []):
            name = ing["name"]
            grams = ing["grams"]
            info = self.ing_db.get(name, {})
            p_contr = round(info.get("protein_g", 0) * (grams / 100.0), 1)
            s_contr = round(info.get("sugars_g", 0) * (grams / 100.0), 1)
            f_contr = round(info.get("fibre_g", 0) * (grams / 100.0), 1)
            breakdown.append({
                "ingredient": name,
                "grams": grams,
                "category": ing.get("category", "other"),
                "protein_g": p_contr,
                "sugars_g": s_contr,
                "fibre_g": f_contr,
                "allergens": ing.get("allergens", [])
            })

        protein_drivers = [b for b in sorted(breakdown, key=lambda x: -x["protein_g"]) if b["protein_g"] >= 0.5]
        sugar_drivers = [b for b in sorted(breakdown, key=lambda x: -x["sugars_g"]) if b["sugars_g"] >= 0.5]

        return {
            "recipe_id": recipe_id,
            "title": recipe["title"],
            "serving_grams": recipe.get("serving_grams"),
            "total_protein_per_serving": total_p_serv,
            "total_sugars_per_serving": total_s_serv,
            "nutrition_per_100g": n_100,
            "ingredients": breakdown,
            "protein_drivers": protein_drivers,
            "sugar_drivers": sugar_drivers
        }

    def _handle_generate_recommendations(self, user_id, mode="lightweight", nutrient_goals=None, weights=None):
        from .engine import recommend, public
        prof = self.db.get_latest_profile(user_id) or {"declared": {}}
        goal = self.db.get_latest_goal(user_id) or {
            "weights": {"p": 0.3, "h": 0.25, "s": 0.2, "c": 0.15, "t": 0.1},
            "diversity": 0.3, "mode": mode
        }

        active_weights = weights or goal.get("weights")
        config = {
            "weights": active_weights,
            "diversity": goal.get("diversity", 0.3),
            "mode": mode,
            "k": 15 if nutrient_goals else 5  # fetch larger candidate set if filtering by nutrients
        }

        learner = self.learners.get(user_id)
        if not learner:
            from .learner import Learner
            learner = Learner(self.cat)
            self.learners[user_id] = learner

        rec_res = recommend(self.cat, self.scaler, learner, prof.get("declared", {}), config, now=0.0)
        pub_res = public(rec_res)

        results = pub_res.get("results", [])

        # Enrich all results with ingredient breakdown and serving nutrition
        for r in results:
            rid = r["recipe_id"]
            analysis = self.analyze_recipe_ingredients(rid)
            r["nutrition_summary"] = {
                "protein_per_serving": analysis.get("total_protein_per_serving"),
                "sugars_per_serving": analysis.get("total_sugars_per_serving"),
                "protein_g_per_100g": analysis.get("nutrition_per_100g", {}).get("protein_g", 0),
                "sugars_g_per_100g": analysis.get("nutrition_per_100g", {}).get("sugars_g", 0),
                "serving_grams": analysis.get("serving_grams")
            }
            r["ingredient_breakdown"] = analysis

        # If nutrient goals are specified, rank/prioritize matching dishes
        if nutrient_goals:
            w_protein = 1.0 if nutrient_goals.get("protein") in ["high", "more", "increase"] else 0.0
            w_sugar = 1.0 if nutrient_goals.get("sugar") in ["high", "more", "increase"] else 0.0

            def nutrient_rank_key(item):
                ns = item.get("nutrition_summary", {})
                p_serv = ns.get("protein_per_serving", 0.0)
                s_serv = ns.get("sugars_per_serving", 0.0)
                u = item.get("utility", 0.5)

                # Composite score blending multi-criteria utility and nutrient fulfillment
                # Normalize protein (~10g to 50g) and sugar (~2g to 20g)
                p_score = min(1.0, max(0.0, p_serv / 30.0))
                s_score = min(1.0, max(0.0, s_serv / 15.0))

                nutri_fit = (w_protein * p_score + w_sugar * s_score) / (w_protein + w_sugar or 1.0)
                # 60% nutrient fit + 40% multi-criteria utility
                return (0.60 * nutri_fit) + (0.40 * u)

            results.sort(key=nutrient_rank_key, reverse=True)
            for idx, item in enumerate(results[:5]):
                item["final_rank"] = idx + 1
            pub_res["results"] = results[:5]

        return pub_res
