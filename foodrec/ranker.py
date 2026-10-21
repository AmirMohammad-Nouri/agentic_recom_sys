"""Multi-criteria utility ranker and weight normalization rules."""

DEFAULT_WEIGHTS = {"p": 0.30, "h": 0.25, "s": 0.20, "c": 0.15, "t": 0.10}


def resolve_weights(weights=None, last_valid=None):
    """Normalizes criteria weights so they sum to 1.0.
    Falls back to `last_valid` (or `DEFAULT_WEIGHTS`) if all requested weights are 0 or empty.
    
    Returns:
        tuple: (normalized_weights_dict, status_message)
    """
    fallback = last_valid if last_valid is not None else DEFAULT_WEIGHTS

    if not weights:
        return fallback.copy(), "Using default weights."

    raw_p = float(weights.get("p", 0.0))
    raw_h = float(weights.get("h", 0.0))
    raw_s = float(weights.get("s", 0.0))
    raw_c = float(weights.get("c", 0.0))
    raw_t = float(weights.get("t", 0.0))

    total = raw_p + raw_h + raw_s + raw_c + raw_t

    if total <= 0:
        return fallback.copy(), "All weights were zero; retained last valid configuration."

    norm_w = {
        "p": round(raw_p / total, 4),
        "h": round(raw_h / total, 4),
        "s": round(raw_s / total, 4),
        "c": round(raw_c / total, 4),
        "t": round(raw_t / total, 4),
    }

    return norm_w, "Normalized weights successfully."


# Alias for backward compatibility across server and engine tools
normalize_weights = resolve_weights


def utility(weights, scores, missing_policy=None):
    """
    Computes weighted multi-criteria utility score U_m(u, i).
    
    Args:
        weights (dict): Normalized weights for each criterion.
        scores (dict): The computed recipe scores for each criterion.
        missing_policy (str/dict): Rule for handling missing score evidence.
        
    Returns:
        tuple: (total_utility, effective_weights, missing_flags, contributions)
    """
    contributions = {}
    missing_flags = []
    total_utility = 0.0
    
    keys = ["p", "h", "s", "c", "t"]
    
    for k in keys:
        w = weights.get(k, 0.0)
        
        # Check if the score is missing
        if k not in scores or scores[k] is None:
            missing_flags.append(k)
            s = 0.0
        else:
            s = float(scores[k])
            
        c = w * s
        contributions[k] = c
        total_utility += c
        
    return total_utility, weights, missing_flags, contributions