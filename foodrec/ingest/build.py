"""Build the portable catalogue file from the HUMMUS spreadsheet.

    python -m foodrec.ingest                          # model labels via Ollama (falls back to rules)
    python -m foodrec.ingest --no-llm                 # keyword rules only, no model needed
    python -m foodrec.ingest --fixture 120 --out data/fixture/catalogue.json.gz

Output: one gzip JSON file {"meta": {...}, "groups": {...}, "recipes": [...]}. It is self-
contained: copy it (or just the label cache) to another device and the app imports it into
its SQLite database on the next start (see Store.import_catalogue). Re-running the build
reuses the label cache, so the model is only asked about names and recipes it has not seen.
"""
import gzip
import hashlib
import json
import math
import time
from collections import Counter
from pathlib import Path

import numpy as np

from .classify import LabelCache, label_ingredients, label_recipes, printer
from .foodgroups import EXTRA_ALLERGENS, UNCLASSIFIED, group, is_food, reference
from .parse import NON_FOOD, clean_name, fix_text, ingredient_lines, literal, nutrition, parse_amount

SCHEMA_VERSION = 2
DIET_TAGS = {"Vegetarian": "vegetarian", "Vegan": "vegan"}


def _rows(xlsx, limit=None):
    """Rows in the HUMMUS column layout from .xlsx, .csv or a JSON list of row objects."""
    path = Path(xlsx)
    if path.suffix == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
        return rows[:limit] if limit else rows
    import pandas as pd  # only needed for building, not for running the app
    df = pd.read_csv(xlsx) if path.suffix == ".csv" else pd.read_excel(xlsx)
    if limit:
        df = df.sample(n=min(limit, len(df)), random_state=7).sort_values("recipe_id")
    return df.to_dict("records")


def _recipe(row, labels_ing, labels_rec, id_prefix="h"):
    per100, serv, serving_g, bad = nutrition(row)
    titles = [str(x) for x in literal(row["ingredients_title"], [])]
    lines = ingredient_lines(row)
    aligned = len(lines) == len(titles)
    tags = [str(t) for t in literal(row.get("tags"), [])]
    ings, seen = [], set()
    for j, raw in enumerate(titles):
        name = clean_name(raw)
        if not is_food(name) or not is_food(raw) or name in seen:
            continue
        seen.add(name)
        lab = labels_ing.get(name) or {"group": UNCLASSIFIED, "source": "none"}
        g = group(lab["group"])
        text, amount = lines[j] if aligned else (raw, "")
        text = fix_text(text)
        grams, basis = parse_amount(amount, name, g["density"], g["unit_g"])
        allergens = set(g["allergens"])
        for rx, a in EXTRA_ALLERGENS:
            if rx.search(name) or rx.search(text.lower()):
                allergens.add(a)
        ings.append({"name": name, "raw": raw, "text": text, "amount": amount.replace("time(s)", "").strip(),
                     "grams": round(grams, 1), "grams_basis": basis, "category": g["key"],
                     "label_source": lab["source"], "co2e_factor": g["co2e"], "allergens": sorted(allergens)})
    # ---- footprint: mass-weighted kg CO2e per kg of dish -----------------------------------
    # Water is left out of the dish mass: cooking water is mostly drained or evaporated, and
    # counting it would make "rice + 3 litres of water" look like a low-carbon food.
    eaten = [i for i in ings if i["category"] != "water"]
    total = sum(i["grams"] for i in eaten) or 1.0
    rows = []
    for i in eaten:
        kg = i["grams"] / 1000 * i["co2e_factor"]
        rows.append({"name": i["name"], "category": i["category"], "grams": i["grams"],
                     "factor": i["co2e_factor"], "kg_co2e": round(kg, 4)})
    co2 = sum(r["kg_co2e"] for r in rows)
    for r in rows:
        r["share"] = round(r["kg_co2e"] / co2, 4) if co2 > 0 else 0.0
    rows.sort(key=lambda r: -r["kg_co2e"])
    intensity = co2 / (total / 1000)
    classified = sum(i["grams"] for i in eaten if i["category"] != UNCLASSIFIED) / total
    footprint = {"kg_co2e_per_kg": round(intensity, 3),
                 "kg_co2e_per_serving": round(intensity * serving_g / 1000, 3) if serving_g else None,
                 "recipe_grams_estimated": round(total), "mass_classified": round(classified, 3),
                 "breakdown": rows}
    # ---- allergens and diets (fail closed) -------------------------------------------------
    unknown = [i["name"] for i in ings if i["category"] == UNCLASSIFIED]
    allergens = sorted({a for i in ings for a in i["allergens"]} |
                       ({"gluten"} if "Contains Gluten" in tags else set()) |
                       ({"crustaceans"} if "Shellfish" in tags else set()))
    animals = {group(i["category"])["animal"] for i in ings} - {None}
    complete = not unknown
    veg_tag, vegan_tag = "Vegetarian" in tags, "Vegan" in tags
    meat, fish = "meat" in animals or "Contains Pork" in tags, "fish" in animals
    diet = {
        "vegetarian": False if (meat or fish) else (True if veg_tag or complete else None),
        "vegan": False if animals else (True if vegan_tag or complete else None),
        "pescatarian": False if meat else (True if veg_tag or fish or complete else None),
        "gluten_free": False if "gluten" in allergens else (True if complete else None),
        "dairy_free": False if "milk" in allergens else (True if complete else None),
    }
    lab = labels_rec.get(str(row["recipe_id"])) or {}
    fsa = row.get("fsa_score")
    fsa = None if fsa is None or (isinstance(fsa, float) and math.isnan(fsa)) else round(float(fsa), 3)
    return {
        "id": f"{id_prefix}{row['recipe_id']}", "source_id": _source_id(row["recipe_id"]),
        "title": fix_text(row["title"]).strip(),
        "url": row.get("recipe_url"), "image_url": row.get("image_url"),
        "cuisine": lab.get("cuisine", "Other"), "meal": lab.get("meal", "main"), "label_source": lab.get("source", "none"),
        "tags": tags, "ingredients": ings, "n_ingredients": len(ings),
        "nutrition_per_100g": per100, "nutrition_per_serving": serv, "serving_grams": serving_g,
        "kcal_per_serving": serv.get("kcal"), "fsa_score": fsa,
        "footprint": footprint, "allergens": allergens, "allergens_complete": complete,
        "unknown_allergen_ingredients": unknown, "diet": diet,
        "quality": {"impossible": bad},
    }


def _source_id(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return str(x)


def _similar(recipes, k=3, min_sim=0.3):
    """Top-k cosine neighbours over ingredient-name mass shares (for SIMILAR_TO edges)."""
    vocab = {}
    for r in recipes:
        for i in r["ingredients"]:
            if i["category"] not in ("spices", "water"):
                vocab.setdefault(i["name"], len(vocab))
    X = np.zeros((len(recipes), len(vocab) + 1), dtype=np.float32)
    for row, r in enumerate(recipes):
        ings = [i for i in r["ingredients"] if i["name"] in vocab]
        tot = sum(i["grams"] for i in ings) or 1.0
        for i in ings:
            X[row, vocab[i["name"]]] += i["grams"] / tot
        X[row, -1] = 0.3 * (hash(r["cuisine"]) % 997) / 997  # weak cuisine signal
    X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
    out = {}
    for s in range(0, len(recipes), 512):
        S = X[s:s + 512] @ X.T
        for a in range(S.shape[0]):
            S[a, s + a] = -1
        top = np.argsort(-S, axis=1)[:, :k]
        for a in range(S.shape[0]):
            out[recipes[s + a]["id"]] = [[recipes[b]["id"], round(float(S[a, b]), 3)] for b in top[a] if S[a, b] >= min_sim]
    return out


def build(xlsx, out, cache_path, llm=None, limit=None, verbose=True, rows=None, name=None, id_prefix="h",
          note=None):
    """`rows`: already-loaded rows in the HUMMUS layout (e.g. from ingest.simple); otherwise read `xlsx`.
    `name`, `id_prefix`, `note`: for a dataset other than HUMMUS."""
    t0 = time.time()
    rows = rows if rows is not None else _rows(xlsx, limit)
    cache = LabelCache(cache_path)
    names = {clean_name(str(x)) for r in rows for x in literal(r["ingredients_title"], [])}
    prog = printer if verbose else None
    if verbose:
        print(f"{len(rows)} rows, {len(names)} unique ingredient names; model: {getattr(llm, 'model', None) or 'none (rules)'}")
    label_ingredients(sorted(n for n in names if is_food(n)), cache, llm, progress=prog)
    label_recipes([(r["recipe_id"], str(r["title"]), [clean_name(str(x)) for x in literal(r["ingredients_title"], [])])
                   for r in rows], cache, llm, progress=prog)
    recipes, dropped = [], Counter()
    for r in rows:
        rec = _recipe(r, cache.data["ingredients"], cache.data["recipes"], id_prefix)
        if rec["meal"] == "non_food" or NON_FOOD.search(rec["title"]):
            dropped["not food (household or pet recipe)"] += 1
            continue
        if rec["quality"]["impossible"]:
            dropped.update(rec["quality"]["impossible"])
            continue
        if not rec["ingredients"] or rec["nutrition_per_100g"].get("kcal") is None:
            dropped["no ingredients or energy"] += 1
            continue
        recipes.append(rec)
    sim = _similar(recipes)
    for r in recipes:
        r["similar"] = sim.get(r["id"], [])
    ing_src = Counter(v["source"] for v in cache.data["ingredients"].values())
    rec_src = Counter(r["label_source"] for r in recipes)
    agree = [v["group"] == v["rule"] for v in cache.data["ingredients"].values() if v["source"] == "model"]
    meta = {
        "name": name or ("hummus" if not limit else f"hummus_sample_{len(recipes)}"), "schema": SCHEMA_VERSION,
        "source": Path(xlsx).name, "rows": len(rows), "recipes": len(recipes),
        "dropped": dict(dropped), "built_at": time.strftime("%Y-%m-%d %H:%M"),
        "labeller": getattr(llm, "model", None) or "rules",
        "ingredient_labels": dict(ing_src), "recipe_labels": dict(rec_src),
        "model_rule_agreement": round(sum(agree) / len(agree), 3) if agree else None,
        "note": (note or "HUMMUS recipes (food.com). Nutrients and FSA score from the dataset;") +
                (" footprints estimated from ingredient amounts x food-group CO2e factors; cuisine, meal and food groups "
                 "labelled by " + (getattr(llm, "model", None) or "keyword rules") + ". Allergens/diets detected from "
                 "ingredient names: verify before relying on them."),
    }
    payload = {"meta": meta, "groups": reference(), "recipes": recipes}
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    meta["version"] = hashlib.sha256(text.encode()).hexdigest()[:12]
    payload["meta"] = meta
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), sort_keys=True, default=str)
    if verbose:
        print(f"wrote {out} ({len(recipes)} recipes, dropped {sum(dropped.values())}, "
              f"{out.stat().st_size / 1e6:.1f} MB) in {time.time() - t0:.0f}s")
    return meta
