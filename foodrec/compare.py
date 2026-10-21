"""Before/after comparison built ONLY from two recorded results (never recomputed)."""
from . import CRITERIA, CRITERION_LABELS


def _diff_config(a, b, extra_a=None, extra_b=None):
    changes = []
    ca, cb = a["config"], b["config"]
    for k in CRITERIA:
        wa, wb = ca["weights_applied"][k], cb["weights_applied"][k]
        if abs(wa - wb) > 1e-9:
            changes.append({"input": f"weight:{k}", "label": f"{CRITERION_LABELS[k]} weight",
                            "before": round(wa, 4), "after": round(wb, 4)})
    for k, label in (("diversity", "Diversity"), ("mode", "Computation mode"),
                     ("missing_policy", "Missing-value policy"), ("k", "List size")):
        if ca[k] != cb[k]:
            changes.append({"input": k, "label": label, "before": ca[k], "after": cb[k]})
    for k, label in (("catalogue", "Catalogue version"), ("scaler", "Scaler version")):
        if a["versions"][k] != b["versions"][k]:
            changes.append({"input": k, "label": label, "before": a["versions"][k], "after": b["versions"][k]})
    for k in sorted(set(extra_a or {}) | set(extra_b or {})):
        va, vb = (extra_a or {}).get(k), (extra_b or {}).get(k)
        if va != vb:
            changes.append({"input": k, "label": k.replace("_", " ").capitalize(), "before": va, "after": vb})
    return changes


def compare(a, b, extra_a=None, extra_b=None):
    """a = previous result, b = current result (as returned by engine.public)."""
    ra = {r["recipe_id"]: r for r in a["results"]}
    rb = {r["recipe_id"]: r for r in b["results"]}
    ta = {t["recipe_id"]: t for t in a["ranking_table"]}
    tb = {t["recipe_id"]: t for t in b["ranking_table"]}
    entered = [rid for rid in rb if rid not in ra]
    left = [rid for rid in ra if rid not in rb]
    moved, same = [], []
    for rid in rb:
        if rid in ra:
            d = ra[rid]["final_rank"] - rb[rid]["final_rank"]
            (moved if d else same).append({"recipe_id": rid, "title": rb[rid]["title"],
                                           "from_rank": ra[rid]["final_rank"],
                                           "to_rank": rb[rid]["final_rank"], "places_gained": d})
    changes = _diff_config(a, b, extra_a, extra_b)

    def utility_delta(rid):
        if rid in ta and rid in tb:
            return tb[rid]["utility"] - ta[rid]["utility"]
        return None

    def why_moved(rid):
        """Name the criterion whose weighted contribution changed most (from recorded data)."""
        if rid in ta and rid in tb:
            best, bestv = None, 0.0
            for k in CRITERIA:
                sa, sb = ta[rid]["scores"].get(k), tb[rid]["scores"].get(k)
                if sa is None or sb is None:
                    continue
                dv = b["config"]["weights_applied"][k] * sb - a["config"]["weights_applied"][k] * sa
                if abs(dv) > abs(bestv):
                    best, bestv = k, dv
            if best:
                return {"criterion": best, "contribution_change": bestv}
        return None

    detail = {}
    for rid in set(entered) | set(left) | {m["recipe_id"] for m in moved}:
        t = tb.get(rid) or {}
        detail[rid] = {
            "title": (rb.get(rid) or ra.get(rid))["title"],
            "utility_before": ta.get(rid, {}).get("utility"),
            "utility_after": tb.get(rid, {}).get("utility"),
            "utility_change": utility_delta(rid),
            "utility_rank_before": ta.get(rid, {}).get("utility_rank"),
            "utility_rank_after": t.get("utility_rank"),
            "final_rank_after": t.get("final_rank"),
            "main_driver": why_moved(rid),
            "left_reason": ("no longer eligible" if rid in left and rid not in tb else None),
        }
    note = None
    if not changes:
        note = "No input changed between the two requests."
    return {"changed_inputs": changes, "entered": entered, "left": left, "moved": moved,
            "unchanged": same, "detail": detail, "note": note}
