"""Frozen recipe catalogue, versioning and concept vectors."""
import hashlib
import json
import math
from pathlib import Path

# Ingredients that carry little taste/identity information; excluded from concept
# and similarity vectors so a pinch of salt does not make two dishes "similar".
SEASONING = {"salt", "sugar", "spice mix", "olive oil"}
CUISINE_W, INGREDIENT_W, CATEGORY_W = 0.35, 0.45, 0.20


class Catalogue:
    def __init__(self, recipes, version, name="catalogue", note=""):
        self.recipes = list(recipes)
        self.version = version
        self.name = name
        self.note = note
        self.by_id = {r["id"]: r for r in self.recipes}
        if len(self.by_id) != len(self.recipes):
            raise ValueError("duplicate recipe ids in catalogue")
        self._concepts = {}
        self._vectors = {}

    @classmethod
    def load(cls, path):
        path = Path(path)
        text = path.read_text()
        data = json.loads(text)
        version = hashlib.sha256(text.encode()).hexdigest()[:12]
        return cls(data["recipes"], version, data.get("name", "catalogue"), data.get("note", ""))

    def __len__(self):
        return len(self.recipes)

    def get(self, rid):
        return self.by_id[rid]

    # ---- concepts used by the taste model -------------------------------------
    def concepts(self, recipe):
        """Return {concept: weight}, weights sum to 1. Cached per recipe id."""
        rid = recipe["id"]
        if rid in self._concepts:
            return self._concepts[rid]
        ings = [i for i in recipe["ingredients"] if i["name"] not in SEASONING]
        total = sum(i["grams"] for i in ings) or 1.0
        w = {}
        w[f"cuisine:{recipe['cuisine']}"] = CUISINE_W
        for i in ings:
            k = f"ingredient:{i['name']}"
            w[k] = w.get(k, 0.0) + INGREDIENT_W * i["grams"] / total
        cats = sorted({i["category"] for i in ings})
        for c in cats:
            w[f"category:{c}"] = w.get(f"category:{c}", 0.0) + CATEGORY_W / len(cats)
        s = sum(w.values())
        w = {k: v / s for k, v in w.items()}
        self._concepts[rid] = w
        return w

    # ---- similarity vectors for the diversity reranker ------------------------
    def vector(self, recipe):
        rid = recipe["id"]
        if rid in self._vectors:
            return self._vectors[rid]
        ings = [i for i in recipe["ingredients"] if i["name"] not in SEASONING]
        total = sum(i["grams"] for i in ings) or 1.0
        v = {f"c:{recipe['cuisine']}": 0.8}
        for i in ings:
            v[f"i:{i['name']}"] = v.get(f"i:{i['name']}", 0.0) + i["grams"] / total
        cats = sorted({i["category"] for i in ings})
        for c in cats:
            v[f"k:{c}"] = 0.4 / math.sqrt(len(cats))
        n = math.sqrt(sum(x * x for x in v.values())) or 1.0
        v = {k: x / n for k, x in v.items()}
        self._vectors[rid] = v
        return v

    def similarity(self, a, b):
        va, vb = self.vector(a), self.vector(b)
        if len(va) > len(vb):
            va, vb = vb, va
        return sum(x * vb.get(k, 0.0) for k, x in va.items())

    # ---- coverage summary for the data audit ----------------------------------
    def coverage(self):
        n = len(self.recipes)
        def cov(pred):
            return sum(1 for r in self.recipes if pred(r)) / n
        nut = ("energy_kj", "sugars_g", "sat_fat_g", "sodium_mg", "fibre_g", "protein_g",
               "fruit_veg_nuts_pct")
        return {
            "recipes": n,
            "nutrition_complete": cov(lambda r: all(r["nutrition_per_100g"].get(k) is not None for k in nut)),
            "sustainability": cov(lambda r: r.get("co2e_kg_per_serving") is not None),
            "cost": cov(lambda r: r.get("cost_eur_per_serving") is not None),
            "prep_time": cov(lambda r: r.get("prep_minutes") is not None),
            "allergens_complete": cov(lambda r: r.get("allergens_complete", False)),
            "provenance_cost": sorted({r.get("cost_provenance", "unknown") for r in self.recipes}),
            "provenance_sustainability": sorted({r.get("co2e_provenance", "unknown") for r in self.recipes}),
        }
