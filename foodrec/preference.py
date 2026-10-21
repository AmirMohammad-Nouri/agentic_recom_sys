"""Preference-match scorers P_m(u, i).

lightweight  P = sum_c w_c * mean_c over the recipe's concepts (cached taste beliefs).
extended     P = (1 - gamma) * P_concept + gamma * K, where K is a similarity-weighted
             average of the user's feedback history (kernel regression) shrunk toward 0.5.
             gamma = n_eff / (n_eff + n0). It costs O(candidates x history).

Both return a model-specific SUITABILITY ESTIMATE in [0, 1]. It is not an observed rating
and not a calibrated probability.
"""

EXTENDED_PARAMS = {"n0": 4.0, "kappa": 0.5, "sharpness": 2.0}


def concept_preference(catalogue, taste, recipe, now=None):
    conc = catalogue.concepts(recipe)
    return sum(w * taste.mean(c, now) for c, w in conc.items())


def concept_support(catalogue, taste, recipe, now=None, top=3):
    """Concepts driving P for this recipe (for explanations)."""
    rows = []
    for c, w in catalogue.concepts(recipe).items():
        m = taste.mean(c, now)
        rows.append({"concept": c, "weight": w, "mean": m, "contribution": w * (m - 0.5),
                     "evidence": taste.evidence(c)})
    rows.sort(key=lambda r: -abs(r["contribution"]))
    return rows[:top]


def extended_preference(catalogue, taste, recipe, history, now=None, params=None):
    p = params or EXTENDED_PARAMS
    base = concept_preference(catalogue, taste, recipe, now)
    if not history:
        return base, {"n_eff": 0.0, "gamma": 0.0, "kernel": None, "base": base}
    num, den, n_eff = p["kappa"] * 0.5, p["kappa"], 0.0
    for rid, y, a in history:
        other = catalogue.by_id.get(rid)
        if other is None:
            continue
        s = max(0.0, catalogue.similarity(recipe, other)) ** p["sharpness"]
        num += s * y * a
        den += s * a
        n_eff += a
    kernel = num / den
    gamma = n_eff / (n_eff + p["n0"])
    return (1.0 - gamma) * base + gamma * kernel, {"n_eff": n_eff, "gamma": gamma,
                                                   "kernel": kernel, "base": base}
