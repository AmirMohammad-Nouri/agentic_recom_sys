"""Mandatory-constraint eligibility filter.

Rules (all documented in docs/features.md):
* Missing allergen information is never evidence that a recipe is allergen free: if a
  user declares ANY allergen, recipes with incomplete allergen data are excluded.
* A diet flag that is unknown (None) fails a declared diet.
* A strict time or cost limit cannot be verified for a recipe with a missing value, so
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
    t = declared.get("max_prep_minutes")
    if t and t.get("strict"):
        out.append(("time_limit", f"Preparation at most {t['value']} min"))
    c = declared.get("max_cost_eur")
    if c and c.get("strict"):
        out.append(("cost_limit", f"Cost at most {c['value']} EUR"))
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
    t = declared.get("max_prep_minutes")
    if t and t.get("strict"):
        m = recipe.get("prep_minutes")
        if m is None:
            out.append(("time_limit", "preparation time unknown"))
        elif m > t["value"]:
            out.append(("time_limit", f"takes {m} min"))
    c = declared.get("max_cost_eur")
    if c and c.get("strict"):
        v = recipe.get("cost_eur_per_serving")
        if v is None:
            out.append(("cost_limit", "cost unknown"))
        elif v > c["value"]:
            out.append(("cost_limit", f"costs {v:.2f} EUR"))
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
    elif code == "time_limit":
        d["max_prep_minutes"] = None
    elif code == "cost_limit":
        d["max_cost_eur"] = None
    return d
