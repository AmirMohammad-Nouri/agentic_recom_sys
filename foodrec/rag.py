"""Expandable Domain Retrieval-Augmented Generation (RAG) Engine."""
import math
import re


class FoodRAG:
    def __init__(self):
        self.documents = []  # list of {id, type, title, text, metadata}

    def add_custom_document(self, doc_id, title, text, category="custom_knowledge"):
        """Dynamically ingest a custom document/context into the RAG index."""
        # Replace if ID exists
        self.documents = [d for d in self.documents if d["id"] != doc_id]
        self.documents.append({
            "id": doc_id,
            "type": category,
            "title": title,
            "text": f"Title: {title}. Content: {text}",
            "metadata": {"category": category, "raw_text": text}
        })

    def index_catalogue_and_ingredients(self, catalogue, ingredients_dict):
        """Index catalogue recipes and ingredient facts for semantic/keyword retrieval."""
        # Foundational Domain Nutrition Knowledge
        self.add_custom_document(
            "doc:nutrition_protein",
            "Nutritional Science: Dietary Protein & Muscle Satiety",
            "Protein is an essential macronutrient comprised of amino acids required for muscle protein synthesis, tissue repair, immune function, and prolonged satiety. High-protein ingredients include chicken breast (~31g/100g), tuna (~29g/100g), beef (~26g/100g), almonds (~21g/100g), oats (~13.5g/100g), eggs (~13g/100g), Greek yogurt (~10g/100g), and chickpeas/lentils (~9g/100g). In the Nutri-Score calculation, protein grants positive points (up to +5 points for >8g/100g), directly improving the dish's healthiness score.",
            category="guidelines"
        )
        self.add_custom_document(
            "doc:nutrition_sugars",
            "Nutritional Science: Sugars, Carbohydrates & Glycemic Energy",
            "Sugars are simple carbohydrates providing rapid cellular energy. Intrinsic natural sugars occur in whole fruits (fructose in berries, bananas, apples) and dairy (lactose in yogurt, milk), buffered by fiber and micronutrients that slow absorption. Free or added sugars cause rapid glycemic spikes without nutritional density. In Nutri-Score, sugars over 4.5g/100g accrue negative penalty points (from 1 to 10 points as sugars exceed 45g/100g). High-sugar dishes must be balanced by fiber and protein to maintain a favorable health profile.",
            category="guidelines"
        )
        self.add_custom_document(
            "doc:strategy_protein_sugar",
            "Dietary Strategy: High Protein & Elevated Sugar Meal Composition",
            "Meals pairing elevated protein with higher sugar provide an ideal macronutrient profile for post-exercise glycogen replenishment and muscle recovery, or for sustained energy during active periods. The health-optimal method to achieve this is pairing lean protein or dairy protein (such as Greek yogurt, oats, chicken, eggs) with whole-food sugars (berries, bananas, carrots, sweet potatoes, balsamic) rather than refined sugars, preserving dietary fiber, vitamins, and a strong Nutri-Score.",
            category="guidelines"
        )

        for r in catalogue.recipes:
            ings_str = ", ".join(i["name"] for i in r["ingredients"])
            n = r.get("nutrition_per_100g", {})
            serv_g = r.get("serving_grams", 100)
            serv_mult = serv_g / 100.0
            p_100 = n.get("protein_g", 0)
            s_100 = n.get("sugars_g", 0)
            p_serv = round(p_100 * serv_mult, 1)
            s_serv = round(s_100 * serv_mult, 1)

            text = (
                f"Recipe: {r['title']}. Cuisine: {r['cuisine']}. "
                f"Prep time: {r['prep_minutes']} min. Kcal: {r.get('kcal_per_serving')}. "
                f"Nutrition per 100g: Protein {p_100}g, Sugars {s_100}g, Fibre {n.get('fibre_g')}g, "
                f"Energy {n.get('energy_kj')} kJ, Sodium {n.get('sodium_mg')}mg. "
                f"Per serving ({serv_g}g): Protein {p_serv}g, Sugars {s_serv}g. "
                f"Cost: {r.get('cost_eur_per_serving')} EUR. CO2e: {r.get('co2e_kg_per_serving')} kg. "
                f"Ingredients: {ings_str}."
            )
            self.documents.append({"id": r["id"], "type": "recipe", "title": r["title"], "text": text, "metadata": r})

        for name, info in ingredients_dict.items():
            text = (
                f"Ingredient: {name}. Category: {info.get('category')}. "
                f"Allergens: {', '.join(info.get('allergens') or ['none'])}. "
                f"Kcal/100g: {info.get('kcal')}. Protein: {info.get('protein_g')}g. Sugars: {info.get('sugars_g')}g. "
                f"Fibre: {info.get('fibre_g')}g. "
                f"Price/kg: {info.get('price_eur_per_kg')} EUR. CO2e/kg: {info.get('co2e_kg_per_kg')} kg."
            )
            self.documents.append({"id": f"ing:{name}", "type": "ingredient", "title": name, "text": text, "metadata": info})

    def search(self, query, top_k=3):
        """Retrieve top_k documents using BM25 keyword scoring."""
        tokens = set(re.findall(r"\w+", query.lower()))
        if not tokens:
            return []

        scored = []
        for doc in self.documents:
            doc_tokens = re.findall(r"\w+", doc["text"].lower())
            score = 0.0
            for t in tokens:
                count = doc_tokens.count(t)
                if count > 0:
                    score += (count / (count + 1.5)) * math.log(1 + len(self.documents) / 2.0)
            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda x: -x[0])
        return [doc for _, doc in scored[:top_k]]

    def get_all_documents(self):
        return self.documents