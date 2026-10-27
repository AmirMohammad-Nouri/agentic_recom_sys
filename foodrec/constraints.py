"""Mandatory-constraint eligibility filter.

Rules (all documented in docs/features.md):
* Missing allergen information is never evidence that a recipe is allergen free: if a
  user declares ANY allergen, recipes with incomplete allergen data are excluded.
* A diet flag that is unknown (None) fails a declared diet.
* A strict ingredient-count limit cannot be verified for a recipe with a missing value, so
  that recipe is excluded.
* Mandatory constraints are never relaxed automatically. diagnose() only REPORTS which
  constraints conflict so the user can revise them.
"""

SUPPORTED_DIETS = ("vegetarian", "vegan", "pescatarian", "gluten_free", "dairy_free")
SUPPORTED_ALLERGENS = ("gluten", "crustaceans", "eggs", "fish", "peanuts", "soy", "milk", "nuts",
                       "celery", "mustard", "sesame", "sulphites", "lupin", "molluscs")


def validate_declared(declared):
    """Raise ValueError for constraints the engine cannot enforce (fail closed)."""
    for a in declared.get("allergens", []):
        if a not in SUPPORTED_ALLERGENS:
            raise ValueError(f"unsupported allergen '{a}'")
    for d in declared.get("diets", []):
        if d not in SUPPORTED_DIETS:
            raise ValueError(f"unsupported diet '{d}'")


def active_constraints(declared):
    """List the mandatory constraints as (code, human label)."""
    out = []
    for a in declared.get("allergens", []):
        out.append((f"allergen:{a}", f"Allergy: {a}"))
    for d in declared.get("diets", []):
        out.append((f"diet:{d}", f"Diet: {d.replace('_', ' ')}"))
    for d in declared.get("disliked_ingredients", []):
        if d.get("strict"):
            out.append((f"dislike:{d['name'].lower()}", f"Never include: {d['name']}"))
    t = declared.get("max_ingredients")
    if t and t.get("strict"):
        out.append(("ingredient_limit", f"At most {t['value']} ingredients"))
    return out


def violations(recipe, declared):
    """Return a list of (code, reason) for every mandatory constraint the recipe fails."""
    out = []
    allergens = declared.get("allergens", [])
    if allergens:
        if not recipe.get("allergens_complete", False):
            out.append(("allergen_unknown", "allergen data incomplete, cannot confirm safe"))
        for a in allergens:
            if a in recipe.get("allergens", []):
                out.append((f"allergen:{a}", f"contains {a}"))
    for d in declared.get("diets", []):
        flag = recipe.get("diet", {}).get(d)
        if flag is None:
            out.append((f"diet:{d}", f"cannot verify {d.replace('_', ' ')}"))
        elif flag is False:
            out.append((f"diet:{d}", f"not {d.replace('_', ' ')}"))
    names = {i["name"].lower() for i in recipe["ingredients"]}
    for d in declared.get("disliked_ingredients", []):
        if d.get("strict") and d["name"].lower() in names:
            out.append((f"dislike:{d['name'].lower()}", f"contains {d['name']}"))
    t = declared.get("max_ingredients")
    if t and t.get("strict"):
        m = recipe.get("n_ingredients")
        if m is None:
            out.append(("ingredient_limit", "ingredient count unknown"))
        elif m > t["value"]:
            out.append(("ingredient_limit", f"has {m} ingredients"))
    return out


def eligible(catalogue, declared):
    """Return (eligible_recipes, report). The report lists excluded ids with reasons."""
    validate_declared(declared)
    keep, excluded = [], {}
    for r in catalogue.recipes:
        v = violations(r, declared)
        if v:
            excluded[r["id"]] = v
        else:
            keep.append(r)
    by_code = {}
    for rid, vs in excluded.items():
        for code, _ in vs:
            by_code[code] = by_code.get(code, 0) + 1
    return keep, {"excluded": excluded, "excluded_count": len(excluded),
                  "eligible_count": len(keep), "excluded_by_code": by_code}


def diagnose(catalogue, declared):
    """Explain an empty or tiny eligible set without relaxing anything."""
    rows = []
    for code, label in active_constraints(declared):
        reduced = _without(declared, code)
        keep, _ = eligible(catalogue, reduced)
        rows.append({"code": code, "label": label, "eligible_if_removed": len(keep)})
    rows.sort(key=lambda r: -r["eligible_if_removed"])
    return {
        "message": ("No recipe satisfies all your mandatory constraints. Nothing was relaxed "
                    "automatically. Revising one of the constraints below would change the result."),
        "constraints": rows,
    }


def _without(declared, code):
    d = {k: (list(v) if isinstance(v, list) else v) for k, v in declared.items()}
    kind, _, name = code.partition(":")
    if kind == "allergen":
        d["allergens"] = [a for a in d.get("allergens", []) if a != name]
    elif kind == "diet":
        d["diets"] = [x for x in d.get("diets", []) if x != name]
    elif kind == "dislike":
        d["disliked_ingredients"] = [x for x in d.get("disliked_ingredients", [])
                                     if x["name"].lower() != name]
    elif code == "ingredient_limit":
        d["max_ingredients"] = None
    return d


# ---- request-scoped filters --------------------------------------------------------
# Temporary filters that come from ONE request (e.g. the assistant parsed "something
# high in protein under 20 minutes"). They narrow eligibility for that request only, are
# shown to the user as applied filters, are never saved into the profile and never change
# the food weights. A recipe whose value is unknown fails the filter (fail closed).
def per_serving(recipe, nutrient):
    return (recipe.get("nutrition_per_serving") or {}).get(nutrient)


REQUEST_FIELDS = {
    "protein_g": ("Protein per serving (g)", lambda r: per_serving(r, "protein_g")),
    "sugars_g": ("Sugars per serving (g)", lambda r: per_serving(r, "sugars_g")),
    "fibre_g": ("Fibre per serving (g)", lambda r: per_serving(r, "fibre_g")),
    "kcal": ("Energy per serving (kcal)", lambda r: per_serving(r, "kcal")),
    "n_ingredients": ("Number of ingredients", lambda r: r.get("n_ingredients")),
    "co2e_kg": ("Footprint (kg CO2e per kg)", lambda r: (r.get("footprint") or {}).get("kg_co2e_per_kg")),
    "cuisine": ("Cuisine", lambda r: r.get("cuisine")),
    "meal": ("Meal type", lambda r: r.get("meal")),
    "ingredient": ("Ingredient", lambda r: {i["name"].lower() for i in r["ingredients"]}),
}
REQUEST_OPS = ("min", "max", "is", "has", "lacks")


def filter_label(f):
    label = REQUEST_FIELDS[f["field"]][0]
    sym = {"min": ">=", "max": "<=", "is": "=", "has": "includes", "lacks": "excludes"}[f["op"]]
    v = f["value"]
    return f"{label} {sym} {' or '.join(map(str, v)) if isinstance(v, (list, tuple)) else v}"


def validate_request_filters(filters):
    out = []
    for f in filters or []:
        if f.get("field") not in REQUEST_FIELDS or f.get("op") not in REQUEST_OPS:
            raise ValueError(f"unsupported request filter {f}")
        out.append({"field": f["field"], "op": f["op"], "value": f["value"],
                    "label": filter_label(f), "source": f.get("source", "request")})
    return out


def request_filter_fails(recipe, f):
    v = REQUEST_FIELDS[f["field"]][1](recipe)
    if v is None:
        return "value unknown"
    op, x = f["op"], f["value"]
    if op == "min" and not v >= float(x):
        return f"{v:.0f} < {x}"
    if op == "max" and not v <= float(x):
        return f"{v:.0f} > {x}"
    if op == "is" and isinstance(x, (list, tuple)):
        if str(v).lower() not in {str(y).lower() for y in x}:
            return f"is {v}"
        return None
    if op == "is" and str(v).lower() != str(x).lower():
        return f"is {v}"
    if op == "has" and str(x).lower() not in v:
        return f"no {x}"
    if op == "lacks" and str(x).lower() in v:
        return f"contains {x}"
    return None
