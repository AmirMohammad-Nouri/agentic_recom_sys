"""Frozen recipe catalogue, versioning and concept vectors.

The catalogue is built offline (python -m foodrec.ingest) into one portable gzip JSON file.
On start-up the app imports that file into its SQLite database (Store.import_catalogue) and
loads the recipes from there, so a device only needs the file (or its database) to run.
"""
import gzip
import hashlib
import json
import math
from pathlib import Path

# Food groups that carry little taste/identity information; excluded from concept and
# similarity vectors so a pinch of salt does not make two dishes "similar".
SEASONING_GROUPS = {"spices", "water"}


def _plain(recipe):
    return [i for i in recipe["ingredients"] if i.get("category") not in SEASONING_GROUPS]


def read_payload(path):
    """Read a catalogue file (.json.gz or .json) -> {"meta", "groups", "recipes"}."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    meta = data.get("meta") or {"name": data.get("name", path.stem), "note": data.get("note", "")}
    if not meta.get("version"):
        meta["version"] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return {"meta": meta, "groups": data.get("groups") or {}, "recipes": data["recipes"]}
CUISINE_W, INGREDIENT_W, CATEGORY_W = 0.35, 0.45, 0.20


class Catalogue:
    def __init__(self, recipes, version, name="catalogue", note="", groups=None, meta=None):
        self.recipes = list(recipes)
        self.version = version
        self.name = name
        self.note = note
        self.groups = groups or {}
        self.meta = meta or {}
        self.by_id = {r["id"]: r for r in self.recipes}
        if len(self.by_id) != len(self.recipes):
            raise ValueError("duplicate recipe ids in catalogue")
        self._concepts = {}
        self._vectors = {}

    @classmethod
    def from_payload(cls, payload):
        m = payload["meta"]
        return cls(payload["recipes"], m["version"], m.get("name", "catalogue"), m.get("note", ""),
                   payload.get("groups"), m)

    @classmethod
    def load(cls, path):
        return cls.from_payload(read_payload(path))

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
        ings = _plain(recipe)
        total = sum(i["grams"] for i in ings) or 1.0
        w = {}
        if recipe["cuisine"] != "Other":  # "Other" is not a taste
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
        ings = _plain(recipe)
        total = sum(i["grams"] for i in ings) or 1.0
        v = {f"c:{recipe['cuisine']}": 0.8} if recipe["cuisine"] != "Other" else {}
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
        n = len(self.recipes) or 1
        def cov(pred):
            return round(sum(1 for r in self.recipes if pred(r)) / n, 3)
        nut = ("kcal", "fat_g", "sat_fat_g", "sugars_g", "sodium_mg", "cholesterol_mg", "fibre_g", "protein_g")
        return {
            "recipes": len(self.recipes),
            "nutrition_complete": cov(lambda r: all((r.get("nutrition_per_100g") or {}).get(k) is not None for k in nut)),
            "fsa_score": cov(lambda r: r.get("fsa_score") is not None),
            "footprint": cov(lambda r: (r.get("footprint") or {}).get("kg_co2e_per_kg") is not None),
            "mass_classified": round(sum((r.get("footprint") or {}).get("mass_classified", 0) for r in self.recipes) / n, 3),
            "allergens_complete": cov(lambda r: r.get("allergens_complete", False)),
            "images": cov(lambda r: bool(r.get("image_url"))),
            "labeller": self.meta.get("labeller"),
        }
