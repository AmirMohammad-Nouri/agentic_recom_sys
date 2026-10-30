"""Your own recipes in a simple JSON format -> rows the catalogue builder understands.

    python -m foodrec.ingest --recipes my_recipes.json --name my_study \\
        --out data/my_study/catalogue.json.gz --no-llm

The file is a JSON list (or {"recipes": [...]}) of objects:
    {"id": "r1",                                   optional; the position is used otherwise
     "title": "Chickpea curry",                    required
     "ingredients": [                              required; strings or objects
        {"name": "chickpeas", "amount": "2 cups", "text": "2 cups cooked chickpeas"},
        "onion"],
     "nutrition_per_100g": {"kcal": 120, "fat_g": 4, "sat_fat_g": 0.5, "sugars_g": 3,
                            "sodium_mg": 210, "cholesterol_mg": 0, "fibre_g": 5,
                            "protein_g": 6, "carbs_g": 15},      kcal is required
     "nutrition_per_serving": {...same keys...},   optional (gives the serving size)
     "tags": ["Vegetarian"],                       optional ("Vegetarian", "Vegan", "Contains Gluten" ...)
     "url": "...", "image_url": "...", "fsa_score": 0.4}         optional
Food groups, CO2e footprints, allergens, diets, cuisine and meal type are derived exactly
as for HUMMUS (model labels when Ollama is available, keyword rules otherwise).
"""
import json
from pathlib import Path

from .parse import NUTRIENT_COLUMNS


def rows_from_simple(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    recipes = data.get("recipes") if isinstance(data, dict) else data
    if not isinstance(recipes, list):
        raise ValueError(f"{path}: expected a list of recipes or {{\"recipes\": [...]}}")
    rows, problems = [], []
    for i, r in enumerate(recipes, 1):
        if not r.get("title") or not r.get("ingredients"):
            problems.append(f"recipe {i}: title and ingredients are required")
            continue
        names, lines = [], []
        for ing in r["ingredients"]:
            if isinstance(ing, str):
                ing = {"name": ing}
            name = str(ing.get("name") or "").strip()
            if not name:
                continue
            names.append(name)
            lines.append([str(ing.get("text") or (f"{ing.get('amount', '')} {name}".strip())), str(ing.get("amount") or "")])
        per100, serv = r.get("nutrition_per_100g") or {}, r.get("nutrition_per_serving") or {}
        if per100.get("kcal") is None:
            problems.append(f"recipe {i} ({r['title']}): nutrition_per_100g.kcal is required")
        row = {"recipe_id": r.get("id", i), "title": r["title"], "ingredients_title": repr(names),
               "ingredients": repr({"": lines}), "tags": repr(list(r.get("tags") or [])),
               "recipe_url": r.get("url"), "image_url": r.get("image_url"), "fsa_score": r.get("fsa_score")}
        for k, (c100, cs) in NUTRIENT_COLUMNS.items():
            row[c100], row[cs] = per100.get(k), serv.get(k)
        rows.append(row)
    if problems:
        raise ValueError("; ".join(problems[:10]) + (f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""))
    return rows
