"""The three criteria and how every recipe is scored on them (0 = worst, 1 = best).

Every score is a fixed formula with published anchors, so each one can be explained per
food: `breakdown(recipe, goal)` returns the factor-by-factor calculation.

Healthiness (h)
    Nine factors, each scored 0-1 between a "good" and a "poor" anchor (linear in between):
      energy, total fat, saturated fat, sugars, sodium  per 100 g, FSA front-of-pack bands
          (UK Food Standards Agency: green at or below the "low" limit, red above the "high")
          energy uses the WCRF energy-density bands (125 / 400 kcal per 100 g)
      cholesterol per 100 g   20 mg (EU/US "low cholesterol" level) to 100 mg
      fibre per 100 g         0 g to 6 g (EU "high fibre" claim)
      protein                 0 % to 20 % of energy from protein (EU "high protein" claim)
      FSA score               the dataset's normalised FSA health score (1 = healthiest)
    Healthiness = weighted mean of the factors. The weights come from the user's health
    goal (GOALS below); "eat healthier" weights all nine equally. Missing factors are left
    out and the rest re-weighted (disclosed in the breakdown).
    Nutrient coverage = goal weight of the factors present / goal weight of all nine. Below
    the minimum coverage (FOODREC_MIN_NUTRIENT_COVERAGE, default 0.6) healthiness is treated
    as missing instead of being computed from a few factors; the missing-value policy in
    ranker.py then decides what happens, and the card says which nutrients are missing.
    This is an operational measure for ranking, not medical or dietetic advice.

Sustainability (s)
    Estimated kg CO2e per kg of dish = sum(ingredient grams x food-group factor) / total
    grams, water left out of both (factors: Poore & Nemecek 2018 via Our World in Data; see ingest/foodgroups.py).
    Score on a log scale: 0.5 kg CO2e/kg or less -> 1, 30 kg CO2e/kg or more -> 0.

Convenience (t)
    Number of ingredients (from ingredients_title): 3 or fewer -> 1, 15 or more -> 0, linear.

Field criteria (configured in foodrec.config.json, see model_spec.py)
    One numeric recipe field (e.g. nutrition_per_100g.kcal) scored between a "best" and a
    "worst" anchor, linear or on a log scale; outside the anchors the score is clipped to 0/1.
Only the ENABLED criteria are scored and ranked; h, s and t are still computed for the
breakdown so the explanations of the built-in criteria keep working when one is switched off.
"""
import math

from . import CRITERION_SPECS, VALUE_CRITERIA
from .model_spec import extract
SCORER_VERSION = "criteria_v2"
MIN_NUTRIENT_COVERAGE = 0.6

# ---- healthiness ------------------------------------------------------------------------
#  key: (label, nutrient key or special, unit, good, poor, source)
HEALTH_FACTORS = {
    "energy":      ("Energy", "kcal", "kcal/100 g", 125, 400, "WCRF energy-density bands"),
    "fat":         ("Total fat", "fat_g", "g/100 g", 3, 17.5, "FSA traffic light"),
    "sat_fat":     ("Saturated fat", "sat_fat_g", "g/100 g", 1.5, 5, "FSA traffic light"),
    "sugars":      ("Sugars", "sugars_g", "g/100 g", 5, 22.5, "FSA traffic light"),
    "sodium":      ("Sodium", "sodium_mg", "mg/100 g", 120, 600, "FSA traffic light (salt 0.3 / 1.5 g)"),
    "cholesterol": ("Cholesterol", "cholesterol_mg", "mg/100 g", 20, 100, "low-cholesterol claim level"),
    "fibre":       ("Fibre", "fibre_g", "g/100 g", 6, 0, "EU 'high fibre' claim"),
    "protein":     ("Protein", "protein_pct", "% of energy", 20, 0, "EU 'high protein' claim"),
    "fsa":         ("FSA health score (0-1)", "fsa_score", "", 1, 0, "HUMMUS dataset (FSA)"),
}
# Health goals chosen at onboarding. Weights per factor (missing = 1). "flip" factors are
# scored the other way round (gaining weight favours energy-dense food).
GOALS = {
    "eat_healthier": {"label": "Eat healthier overall", "weights": {}, "flip": []},
    "lose_weight": {"label": "Lose weight", "weights": {"energy": 3, "fat": 2, "sugars": 2, "fibre": 2, "protein": 1.5},
                    "flip": []},
    "gain_weight": {"label": "Gain weight", "weights": {"energy": 2, "protein": 2, "fat": 0.5, "fsa": 0.5},
                    "flip": ["energy"]},
    "build_muscle": {"label": "Build muscle", "weights": {"protein": 4, "energy": 0.5, "fat": 0.5}, "flip": []},
    "heart_health": {"label": "Heart-friendly eating", "weights": {"sat_fat": 3, "sodium": 3, "cholesterol": 2, "fibre": 2},
                     "flip": []},
    "less_sugar": {"label": "Cut down on sugar", "weights": {"sugars": 4, "fibre": 1.5, "energy": 1.5}, "flip": []},
}
DEFAULT_GOAL = "eat_healthier"

# ---- sustainability and convenience anchors ------------------------------------------------
CO2_BEST, CO2_WORST = 0.5, 30.0
ING_BEST, ING_WORST = 3, 15

DEFINITIONS = {
    "h": {"label": "Healthiness", "raw": "goal-weighted mean of nine nutrient factors", "direction": "higher"},
    "s": {"label": "Sustainability", "raw": "kg CO2e per kg of dish (log scale)", "direction": "lower"},
    "t": {"label": "Convenience", "raw": "number of ingredients", "direction": "lower"},
}


def _lin(x, good, poor):
    if good == poor:
        return 1.0
    return min(1.0, max(0.0, (x - poor) / (good - poor)))


def _factor_value(recipe, key):
    n = recipe.get("nutrition_per_100g") or {}
    if key == "protein_pct":
        kcal, p = n.get("kcal"), n.get("protein_g")
        if not kcal or p is None:
            return None
        return min(100.0, 400.0 * p / kcal)
    if key == "fsa_score":
        return recipe.get("fsa_score")
    return n.get(key)


def health_factors(recipe, goal=None):
    g = GOALS.get(goal or DEFAULT_GOAL, GOALS[DEFAULT_GOAL])
    rows = []
    for k, (label, key, unit, good, poor, src) in HEALTH_FACTORS.items():
        v = _factor_value(recipe, key)
        flip = k in g["flip"]
        score = None if v is None else _lin(v, good, poor)
        if score is not None and flip:
            score = 1.0 - score
        rows.append({"factor": k, "label": label, "value": None if v is None else round(v, 2), "unit": unit,
                     "good": poor if flip else good, "poor": good if flip else poor, "source": src,
                     "weight": float(g["weights"].get(k, 1.0)), "score": None if score is None else round(score, 4),
                     "flipped": flip})
    return rows


def healthiness(recipe, goal=None):
    rows = health_factors(recipe, goal)
    num = sum(r["weight"] * r["score"] for r in rows if r["score"] is not None)
    den = sum(r["weight"] for r in rows if r["score"] is not None)
    return (num / den if den else None), rows


def health_coverage(rows):
    """Share of the goal's factor weight that has data (1 = all nine factors known)."""
    total = sum(r["weight"] for r in rows)
    return sum(r["weight"] for r in rows if r["score"] is not None) / total if total else 0.0


def sustainability(recipe):
    fp = recipe.get("footprint") or {}
    x = fp.get("kg_co2e_per_kg")
    if x is None:
        return None
    lo, hi = math.log(CO2_BEST), math.log(CO2_WORST)
    return min(1.0, max(0.0, (hi - math.log(max(x, 1e-3))) / (hi - lo)))


def convenience(recipe):
    n = recipe.get("n_ingredients")
    return None if n is None else _lin(n, ING_BEST, ING_WORST)


def field_score(recipe, spec):
    """Score of a configured field criterion: (score, raw value)."""
    v = extract(recipe, spec["field"])
    if v is None:
        return None, None
    best, worst = spec["best"], spec["worst"]
    if spec.get("scale") == "log":
        v2 = max(v, 1e-9)
        x = (math.log(worst) - math.log(v2)) / (math.log(worst) - math.log(best))
    else:
        x = (worst - v) / (worst - best)
    return min(1.0, max(0.0, x)), v


def raw_values(recipe):
    """Raw values behind each criterion (for display and for the assistant)."""
    fp = recipe.get("footprint") or {}
    out = {"h": None, "s": fp.get("kg_co2e_per_kg"), "t": recipe.get("n_ingredients")}
    for k, c in CRITERION_SPECS.items():
        if c["kind"] == "field":
            out[k] = extract(recipe, c["field"])
    return {k: out.get(k) for k in VALUE_CRITERIA}


def health_grade(score):
    """A-E letter for a healthiness score (FoodRec's own banding, not Nutri-Score)."""
    if score is None:
        return None
    return "A" if score >= 0.8 else "B" if score >= 0.65 else "C" if score >= 0.5 else "D" if score >= 0.35 else "E"


def breakdown(recipe, goal=None, min_coverage=MIN_NUTRIENT_COVERAGE):
    """Everything needed to explain the three scores of one recipe."""
    h, rows = healthiness(recipe, goal)
    cov = health_coverage(rows)
    below = cov < min_coverage
    if below:
        h = None
    fp = recipe.get("footprint") or {}
    s, t = sustainability(recipe), convenience(recipe)
    g = GOALS.get(goal or DEFAULT_GOAL, GOALS[DEFAULT_GOAL])
    used = [r for r in rows if r["score"] is not None]
    out = {
        "recipe_id": recipe["id"], "title": recipe["title"],
        "h": {"score": h, "grade": health_grade(h), "goal": goal or DEFAULT_GOAL, "goal_label": g["label"],
              "factors": rows, "missing": [r["label"] for r in rows if r["score"] is None],
              "coverage": round(cov, 4), "min_coverage": min_coverage, "below_min_coverage": below,
              "formula": "sum(weight x factor score) / sum(weight) over " + f"{len(used)} factors"},
        "s": {"score": s, "kg_co2e_per_kg": fp.get("kg_co2e_per_kg"), "kg_co2e_per_serving": fp.get("kg_co2e_per_serving"),
              "anchors": {"best": CO2_BEST, "worst": CO2_WORST}, "mass_classified": fp.get("mass_classified"),
              "breakdown": fp.get("breakdown", []),
              "formula": f"(ln {CO2_WORST} - ln x) / (ln {CO2_WORST} - ln {CO2_BEST}), clipped to 0-1"},
        "t": {"score": t, "n_ingredients": recipe.get("n_ingredients"),
              "ingredients": [i["name"] for i in recipe.get("ingredients", [])],
              "anchors": {"best": ING_BEST, "worst": ING_WORST},
              "formula": f"({ING_WORST} - n) / ({ING_WORST} - {ING_BEST}), clipped to 0-1"},
    }
    for k, c in CRITERION_SPECS.items():
        if c["kind"] == "field":
            sc, v = field_score(recipe, c)
            b, w = c["best"], c["worst"]
            out[k] = {"score": sc, "kind": "field", "label": c["label"], "field": c["field"], "value": v,
                      "unit": c.get("unit", ""), "direction": c["direction"], "scale": c["scale"],
                      "anchors": {"best": b, "worst": w},
                      "formula": (f"(ln {w} - ln x) / (ln {w} - ln {b})" if c["scale"] == "log"
                                  else f"({w} - x) / ({w} - {b})") + ", clipped to 0-1"}
    return out


class Scorer:
    """Scores recipes on h, s, t. Health depends on the user's goal; results are cached."""

    version = SCORER_VERSION

    def __init__(self, catalogue, min_coverage=MIN_NUTRIENT_COVERAGE):
        self.catalogue_version = catalogue.version
        self.min_coverage = float(min_coverage)
        self.version = f"{SCORER_VERSION}+cov{self.min_coverage:g}"
        self._cache = {}
        self._cov = {}

    def scores(self, recipe, goal=None):
        key = (recipe["id"], goal or DEFAULT_GOAL)
        if key not in self._cache:
            h, rows = healthiness(recipe, goal)
            cov = health_coverage(rows)
            self._cov[key] = {"coverage": cov, "missing": [r["label"] for r in rows if r["score"] is None],
                              "below_min": cov < self.min_coverage}
            allc = {"h": None if cov < self.min_coverage else h, "s": sustainability(recipe),
                    "t": convenience(recipe)}
            for k, c in CRITERION_SPECS.items():
                if c["kind"] == "field":
                    allc[k] = field_score(recipe, c)[0]
            self._cache[key] = {k: allc[k] for k in VALUE_CRITERIA}
        return dict(self._cache[key])

    def coverage(self, recipe, goal=None):
        """Nutrient coverage of one recipe: {"coverage", "missing" factor labels, "below_min"}."""
        self.scores(recipe, goal)
        return dict(self._cov[(recipe["id"], goal or DEFAULT_GOAL)])

    def describe(self):
        return {"version": self.version, "min_nutrient_coverage": self.min_coverage,
                "health_factors": {k: v[:5] for k, v in HEALTH_FACTORS.items()},
                "goals": {k: v["label"] for k, v in GOALS.items()},
                "sustainability_anchors": [CO2_BEST, CO2_WORST], "convenience_anchors": [ING_BEST, ING_WORST],
                "criteria": list(VALUE_CRITERIA)}
