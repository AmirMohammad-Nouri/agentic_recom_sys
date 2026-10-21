"""Weights, weighted utility and missing-value policy.

U(u, i) = w_p P(u,i) + w_h H(i) + w_s S(i) + w_c C(i) + w_t T(i), weights >= 0, sum to 1.
Order of keys is always (p, h, s, c, t).

Missing criterion values are never turned into an average. Policy "renormalize"
(default) drops a missing criterion for that recipe and renormalises the remaining
weights; the effective weights are returned so they can be disclosed. Policy "exclude"
removes recipes whose missing criterion carries positive weight.
"""
from . import CRITERIA

DEFAULT_WEIGHTS = {"p": 0.30, "h": 0.25, "s": 0.20, "c": 0.15, "t": 0.10}
MISSING_POLICIES = ("renormalize", "exclude")


def normalize_weights(raw):
    """Return normalised weights summing to 1, or None if invalid (negative/NaN/all zero)."""
    try:
        vals = {k: float(raw.get(k, 0.0)) for k in CRITERIA}
    except (TypeError, ValueError):
        return None
    if any(v < 0 or v != v for v in vals.values()):
        return None
    s = sum(vals.values())
    if s <= 0:
        return None
    return {k: v / s for k, v in vals.items()}


def resolve_weights(requested, last_valid):
    """Apply requested weights; if invalid keep the last valid set and say why."""
    w = normalize_weights(requested)
    if w is not None:
        return w, None
    keep = normalize_weights(last_valid) or dict(DEFAULT_WEIGHTS)
    return keep, ("The requested weights are not valid (all zero or negative). The last valid "
                  "configuration was kept. Raise at least one slider above zero to apply new weights.")


def utility(weights, scores, policy="renormalize"):
    """Return (U or None, effective_weights, missing_keys, contributions)."""
    if policy not in MISSING_POLICIES:
        raise ValueError(f"unknown missing policy {policy}")
    missing = [k for k in CRITERIA if scores.get(k) is None]
    if policy == "exclude" and any(weights[k] > 0 for k in missing):
        return None, dict(weights), missing, {}
    avail = {k: weights[k] for k in CRITERIA if scores.get(k) is not None}
    s = sum(avail.values())
    if s <= 0:
        return None, {k: 0.0 for k in CRITERIA}, missing, {}
    eff = {k: (avail[k] / s if k in avail else 0.0) for k in CRITERIA}
    contrib = {k: eff[k] * scores[k] for k in CRITERIA if k in avail}
    return sum(contrib.values()), eff, missing, contrib
