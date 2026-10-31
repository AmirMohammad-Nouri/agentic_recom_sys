"""Weight normalisation, weighted utility and the missing-value policy.

The user's saved values are three weights over (h, s, t) that sum to 1. Ranking expands
them with the fixed taste share:

    effective = {p: TASTE_SHARE, h: (1 - TASTE_SHARE) * w_h, s: ..., t: ...}
    U(i) = sum_k effective_k * score_k(i)

Reference case: values 0.5/0.3/0.2, scores taste 0.8, h 0.9, s 0.55, t 0.85 give
U = 0.25*0.8 + 0.75*(0.45 + 0.165 + 0.17) = 0.78875.

Missing-value policies (decided BEFORE ranking, disclosed per recipe):
* "renormalize"  drop the unscored VALUE criteria for this recipe and rescale the remaining value
                 weights so they keep the same (1 - TASTE_SHARE) total. Taste never grows past its
                 fixed share, so missing health data cannot turn a recipe into a taste-only ranking.
                 If the missing criteria carry more than `max_missing_share` of the value weight
                 (default 50%), or every weighted value criterion is missing, the recipe is not
                 ranked and the reason is reported.
* "zero"         an unscored criterion contributes 0 (a pessimistic penalty).
* "exclude"      a recipe missing any positively weighted criterion is not ranked.
"""
from . import CRITERIA, CRITERION_SPECS, TASTE_SHARE, VALUE_CRITERIA

# the configured starting weights of the enabled criteria (foodrec.config.json "criteria")
_dw = {k: c["default_weight"] for k, c in CRITERION_SPECS.items()}
DEFAULT_WEIGHTS = {k: v / sum(_dw.values()) for k, v in _dw.items()}
MISSING_POLICIES = ("renormalize", "zero", "exclude")
MAX_MISSING_SHARE = 0.5


def conform_weights(weights):
    """Weights saved under another criteria configuration, mapped onto the current criteria:
    a removed criterion is dropped, a new one starts at 0, the rest is renormalised. If
    nothing is left, the configured default weights are used."""
    w = {k: max(0.0, float((weights or {}).get(k) or 0.0)) for k in VALUE_CRITERIA}
    tot = sum(w.values())
    return {k: v / tot for k, v in w.items()} if tot > 0 else dict(DEFAULT_WEIGHTS)


def resolve_weights(weights=None, last_valid=None):
    """Return (normalised h/s/t weights, message). Raw weights must be finite and >= 0.

    If every weight is zero the last valid configuration is retained.
    """
    fallback = dict(last_valid if last_valid is not None else DEFAULT_WEIGHTS)
    fallback = {k: float(fallback.get(k, 0.0)) for k in VALUE_CRITERIA}
    if not weights:
        return fallback, "No weights given; using the last valid configuration."
    raw = {}
    for k in VALUE_CRITERIA:
        v = float(weights.get(k, 0.0) or 0.0)
        if v < 0 or v != v or v == float("inf"):
            raise ValueError(f"weight '{k}' must be a finite number >= 0")
        raw[k] = v
    total = sum(raw.values())
    if total <= 0:
        return fallback, ("All weights were zero, so the last valid configuration was kept. "
                          "Raise at least one slider above zero to change it.")
    return {k: raw[k] / total for k in VALUE_CRITERIA}, "Values normalised to sum to 100%."


def effective_weights(values, taste_share=TASTE_SHARE):
    """Three user values -> four ranking weights (taste share + scaled values)."""
    out = {"p": float(taste_share)}
    out.update({k: (1.0 - taste_share) * float(values.get(k, 0.0)) for k in VALUE_CRITERIA})
    return out


def utility(weights, scores, missing_policy="renormalize", max_missing_share=MAX_MISSING_SHARE):
    """weights over CRITERIA (use effective_weights). Returns (utility, effective, missing, contributions).

    utility is None when the recipe cannot be ranked under the policy (see module docstring)."""
    policy = missing_policy or "renormalize"
    if policy not in MISSING_POLICIES:
        raise ValueError(f"unknown missing policy '{policy}'")
    keys = [k for k in CRITERIA if k in weights]
    missing = [k for k in keys if scores.get(k) is None]
    if policy == "exclude" and any(weights.get(k, 0.0) > 0 for k in missing):
        return None, dict(weights), missing, {}
    eff = {k: float(weights.get(k, 0.0)) for k in keys}
    if policy == "renormalize" and missing:
        vkeys = [k for k in keys if k != "p"]
        vtot = sum(eff[k] for k in vkeys)
        vpresent = sum(eff[k] for k in vkeys if k not in missing)
        if any(k != "p" for k in missing) and vtot > 0:
            if vpresent <= 0 or (vtot - vpresent) / vtot > max_missing_share + 1e-12:
                return None, eff, missing, {}
            eff = {k: (0.0 if k in missing else eff[k] * vtot / vpresent) if k != "p" else eff[k]
                   for k in keys}
        if "p" in missing:
            # no taste estimate (e.g. a recipe outside the latest ranking): rank on the values alone
            present = sum(eff[k] for k in keys if k not in missing)
            if present <= 0:
                return None, eff, missing, {}
            eff = {k: (0.0 if k in missing else eff[k] / present) for k in keys}
    contrib = {k: eff[k] * (scores[k] if scores.get(k) is not None else 0.0) for k in keys}
    return sum(contrib.values()), eff, missing, contrib
