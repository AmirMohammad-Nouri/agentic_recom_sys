"""Criterion definitions, Nutri-Score implementation and the frozen reference scaler.

Healthiness: Nutri-Score points for solid foods, per 100 g, as published on
https://world.openfoodfacts.org/nutriscore-formula (2015 HCSP notice version).
The 2023 Nutri-Score update is NOT implemented. It is applied here to composed dishes
as an operational measure; it is not a clinical assessment and is not a dish-level
validated label. Deviations: legumes count with vegetables, olive oil is not counted
in the fruit/vegetable/nut share.
"""
import json
import math

import numpy as np

NUTRI_VERSION = "nutriscore_2015_hcsp_solid_foods"
SCALER_VERSION = "scaler_v1"

_ENERGY = [335, 670, 1005, 1340, 1675, 2010, 2345, 2680, 3015, 3350]
_SUGARS = [4.5, 9, 13.5, 18, 22.5, 27, 31, 36, 40, 45]
_SATFAT = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
_SODIUM = [90, 180, 270, 360, 450, 540, 630, 720, 810, 900]
_FIBRE = [0.7, 1.4, 2.1, 2.8, 3.5]
_PROTEIN = [1.6, 3.2, 4.8, 6.4, 8.0]
_NUTRIENTS = ("energy_kj", "sugars_g", "sat_fat_g", "sodium_mg", "fibre_g", "protein_g",
              "fruit_veg_nuts_pct")


def _points(value, thresholds):
    return sum(1 for t in thresholds if value > t)


def nutriscore(n):
    """Return a dict with points and score, or None if any required nutrient is missing."""
    if n is None or any(n.get(k) is None for k in _NUTRIENTS):
        return None
    a_energy = _points(n["energy_kj"], _ENERGY)
    a_sugars = _points(n["sugars_g"], _SUGARS)
    a_sat = _points(n["sat_fat_g"], _SATFAT)
    a_sodium = _points(n["sodium_mg"], _SODIUM)
    a = a_energy + a_sugars + a_sat + a_sodium
    pct = n["fruit_veg_nuts_pct"]
    c_fv = 5 if pct > 80 else 2 if pct > 60 else 1 if pct > 40 else 0
    c_fibre = _points(n["fibre_g"], _FIBRE)
    c_protein = _points(n["protein_g"], _PROTEIN)
    protein_counted = not (a >= 11 and c_fv < 5)
    c = c_fv + c_fibre + (c_protein if protein_counted else 0)
    score = a - c
    grade = "A" if score <= -1 else "B" if score <= 2 else "C" if score <= 10 else "D" if score <= 18 else "E"
    return {
        "score": score, "grade": grade, "negative_points": a, "positive_points": c,
        "parts": {"energy": a_energy, "sugars": a_sugars, "sat_fat": a_sat, "sodium": a_sodium,
                  "fruit_veg_nuts": c_fv, "fibre": c_fibre, "protein": c_protein},
        "protein_counted": protein_counted, "variant": NUTRI_VERSION,
    }


# Criterion raw-value definitions. "lower" = lower raw value is better.
DEFINITIONS = {
    "h": {"label": "Healthiness", "raw": "negative Nutri-Score points (higher is better)",
          "direction": "higher", "transform": "identity"},
    "s": {"label": "Sustainability", "raw": "ln(1 + kg CO2e per serving)",
          "direction": "lower", "transform": "log1p"},
    "c": {"label": "Affordability", "raw": "cost in EUR per serving",
          "direction": "lower", "transform": "identity"},
    "t": {"label": "Convenience", "raw": "preparation minutes",
          "direction": "lower", "transform": "identity"},
}


def raw_values(recipe):
    """Raw (pre-normalisation) criterion values; None when the evidence is missing."""
    ns = nutriscore(recipe.get("nutrition_per_100g"))
    co2 = recipe.get("co2e_kg_per_serving")
    cost = recipe.get("cost_eur_per_serving")
    mins = recipe.get("prep_minutes")
    return {
        "h": None if ns is None else -float(ns["score"]),
        "s": None if co2 is None else math.log1p(co2),
        "c": None if cost is None else float(cost),
        "t": None if mins is None else float(mins),
    }


class ReferenceScaler:
    """Maps raw values to [0, 1] with higher always favourable.

    Fitted ONCE on the frozen catalogue (5th to 95th percentile, clipped) and reused for
    every request, so scales never depend on the current result list.
    """

    def __init__(self, params, catalogue_version):
        self.params = params
        self.catalogue_version = catalogue_version
        self.version = SCALER_VERSION

    @classmethod
    def fit(cls, catalogue, lo_pct=5, hi_pct=95):
        cols = {k: [] for k in DEFINITIONS}
        missing = {k: 0 for k in DEFINITIONS}
        for r in catalogue.recipes:
            for k, v in raw_values(r).items():
                if v is None:
                    missing[k] += 1
                else:
                    cols[k].append(v)
        params = {}
        for k, vals in cols.items():
            lo, hi = (float(np.percentile(vals, lo_pct)), float(np.percentile(vals, hi_pct)))
            if hi - lo < 1e-9:
                hi = lo + 1.0
            params[k] = {"lo": lo, "hi": hi, "direction": DEFINITIONS[k]["direction"],
                         "n": len(vals), "n_missing": missing[k],
                         "percentiles": [lo_pct, hi_pct], "raw": DEFINITIONS[k]["raw"]}
        return cls(params, catalogue.version)

    def transform(self, key, raw):
        if raw is None:
            return None
        p = self.params[key]
        x = (raw - p["lo"]) / (p["hi"] - p["lo"])
        x = min(1.0, max(0.0, x))
        return x if p["direction"] == "higher" else 1.0 - x

    def scores(self, recipe):
        raws = raw_values(recipe)
        return {k: self.transform(k, v) for k, v in raws.items()}

    def to_json(self):
        return json.dumps({"version": self.version, "catalogue_version": self.catalogue_version,
                           "params": self.params}, indent=1, sort_keys=True)

    @classmethod
    def from_json(cls, text):
        d = json.loads(text)
        s = cls(d["params"], d["catalogue_version"])
        s.version = d.get("version", SCALER_VERSION)
        return s
