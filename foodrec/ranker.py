"""Weight normalisation, weighted utility and the missing-value policy.

U_m(u, i) = sum_k w_k * score_k(i)   with w_k >= 0 and sum_k w_k = 1.

Reference case (brief, section 2): weights 0.30/0.25/0.20/0.15/0.10 and scores
0.80/0.90/0.55/0.70/0.85 give U = 0.765.

Missing-value policies (decided BEFORE ranking, disclosed per recipe):
* "renormalize"  drop the unscored criteria for this recipe and rescale the remaining
                 weights to sum to 1. The recipe is ranked on the evidence that exists.
* "zero"         an unscored criterion contributes 0 (a pessimistic penalty).
* "exclude"      a recipe missing any POSITIVELY weighted criterion is not ranked.
A missing value is never replaced by an average quality score.
"""
from . import CRITERIA

DEFAULT_WEIGHTS = {"p": 0.30, "h": 0.25, "s": 0.20, "c": 0.15, "t": 0.10}
MISSING_POLICIES = ("renormalize", "zero", "exclude")


def resolve_weights(weights=None, last_valid=None):
    """Return (normalised weights, message). Raw weights must be finite and >= 0.

    If every weight is zero the last valid configuration is retained (brief, section 1).
    """
    fallback = dict(last_valid if last_valid is not None else DEFAULT_WEIGHTS)
    if not weights:
        return fallback, "No weights given; using the last valid configuration."
    raw = {}
    for k in CRITERIA:
        v = float(weights.get(k, 0.0) or 0.0)
        if v < 0 or v != v or v == float("inf"):
            raise ValueError(f"weight '{k}' must be a finite number >= 0")
        raw[k] = v
    total = sum(raw.values())
    if total <= 0:
        return fallback, ("All weights were zero, so the last valid configuration was kept. "
                          "Raise at least one slider above zero to change it.")
    return {k: raw[k] / total for k in CRITERIA}, "Weights normalised to sum to 1."


def utility(weights, scores, missing_policy="renormalize"):
    """Return (utility, effective_weights, missing_criteria, contributions).

    utility is None when the policy excludes the recipe or nothing can be scored.
    """
    policy = missing_policy or "renormalize"
    if policy not in MISSING_POLICIES:
        raise ValueError(f"unknown missing policy '{policy}'")
    missing = [k for k in CRITERIA if scores.get(k) is None]
    if policy == "exclude" and any(weights.get(k, 0.0) > 0 for k in missing):
        return None, dict(weights), missing, {}
    eff = {k: float(weights.get(k, 0.0)) for k in CRITERIA}
    if policy == "renormalize" and missing:
        present = sum(eff[k] for k in CRITERIA if k not in missing)
        if present <= 0:
            return None, eff, missing, {}
        eff = {k: (0.0 if k in missing else eff[k] / present) for k in CRITERIA}
    contrib = {k: eff[k] * (scores[k] if scores.get(k) is not None else 0.0) for k in CRITERIA}
    return sum(contrib.values()), eff, missing, contrib
