"""Server-side enforcement of access roles (see appconfig.FEATURES).

The UI hides what a role may not see, but hiding alone is not enough for a study: a
participant could read the API response. These functions remove the hidden fields from
recommendation results and chat attachments before they leave the server. Stored request
snapshots keep everything, plus an `access` record of what this user could see, so the
export can report both.
"""
import copy

from . import CRITERIA
from .explain import plain_reason

FOOD_DETAIL_KEYS = ("nutrition_per_100g", "nutrition_per_serving", "per_serving", "serving_grams", "kcal",
                    "co2e_kg_per_kg", "co2e_kg_per_serving", "fsa_score", "health_grade", "ingredients_detail",
                    "allergens", "allergens_complete", "diet", "raw")
EMPTY_REASONS = {"summary": "", "fits": [], "ranked_here": [], "compromises": [], "gaps": []}


def snapshot(acc):
    """The part of an access record stored with a request: role and what was visible."""
    return {"role": acc["role"], "visible_criteria": list(acc["visible_criteria"]),
            "features": dict(acc["features"]), "show_similarity": acc.get("show_similarity", True)}


def full(acc):
    f = acc["features"]
    return all(f.get(k, True) for k in ("explanations", "score_breakdown", "utility", "food_details", "images")) \
        and set(acc["visible_criteria"]) == set(CRITERIA) and acc.get("show_similarity", True)


def _crit_filter(d, visible):
    return {k: v for k, v in d.items() if k in visible} if isinstance(d, dict) else d


def redact_recipe(r, acc):
    f, vis = acc["features"], set(acc["visible_criteria"])
    r = dict(r)
    if "scores" in r:
        r["scores"] = _crit_filter(r["scores"], vis)
    ev = copy.deepcopy(r.get("evidence") or {})
    rk = ev.get("ranking") or {}
    if not f["utility"] or vis != set(CRITERIA):
        # contributions reveal the hidden scores (contribution = weight x score)
        if not f["utility"]:
            for k in ("utility", "contributions", "effective_weights"):
                r.pop(k, None)
            rk.pop("contributions", None)
            rk.pop("top_contributor", None)
        else:
            r["contributions"] = _crit_filter(r.get("contributions"), vis)
            rk["contributions"] = _crit_filter(rk.get("contributions"), vis)
            if rk.get("top_contributor") not in vis:
                rk.pop("top_contributor", None)
    if not acc.get("show_similarity", True):
        # only the number goes: the bar and the reason do not reveal it
        r.pop("utility", None)
        rk.pop("utility", None)
    if f["explanations"] and r.get("reasons"):
        # hidden criteria must not appear in the sentence on the card
        r["reasons"] = {**r["reasons"], "summary": plain_reason(ev, vis)}
    if not f["explanations"]:
        r["reasons"] = dict(EMPTY_REASONS)
        for k in ("fit", "compromises", "comparison", "uncertainty", "claims_allowed"):
            ev.pop(k, None)
        r["missing_info"] = []
    if not f["score_breakdown"]:
        r.pop("breakdown", None)
    if not f["food_details"]:
        for k in FOOD_DETAIL_KEYS:
            r.pop(k, None)
        r.pop("breakdown", None)
    if not f["images"]:
        r["image_url"] = None
    ev["ranking"] = rk
    r["evidence"] = ev
    return r


def redact_result(res, acc):
    """A recommendation result as this user may see it."""
    if not res:
        return res
    out = dict(res)
    out["access"] = snapshot(acc)
    if full(acc) and acc["features"]["explanations"]:
        return out
    out["results"] = [redact_recipe(r, acc) for r in res.get("results") or []]
    out.pop("ranking_table", None)
    if not acc["features"]["explanations"]:
        out["narrative"] = None
        out.pop("pipeline_trace", None)
    return out


def redact_attachments(att, acc):
    """Chat attachments (recipe lists, comparisons, breakdowns) as this user may see them."""
    if not att:
        return att
    att = dict(att)
    f, vis = acc["features"], set(acc["visible_criteria"])
    if att.get("result"):
        att["result"] = redact_result(att["result"], acc)
    if att.get("what_if"):
        wi = dict(att["what_if"])
        for k in ("base", "alternative"):
            if wi.get(k):
                wi[k] = redact_result(wi[k], acc)
        att["what_if"] = wi
    if att.get("comparison") and att["comparison"].get("rows"):
        c = dict(att["comparison"])
        c["rows"] = [row for row in c["rows"] if row.get("criterion") in vis]
        if not f["utility"]:
            for side in ("a", "b"):
                if isinstance(c.get(side), dict):
                    c[side] = {k: v for k, v in c[side].items() if k != "utility"}
        att["comparison"] = c
    if att.get("breakdown") and not (f["score_breakdown"] and f["food_details"]):
        att.pop("breakdown")
    if isinstance(att.get("recipe"), dict):
        att["recipe"] = redact_recipe(att["recipe"], acc)
    if not f["supervisor"] and att.get("decisions"):
        att["decisions"] = [d for d in att["decisions"] if d.get("kind") == "profile_change"]
    return att


def redact_message(m, acc):
    m = dict(m)
    if not acc["features"]["agent_trace"]:
        m["trace"] = []
    m["attachments"] = redact_attachments(m.get("attachments") or {}, acc)
    return m


def visible_scores(scores, visible):
    return {k: v for k, v in (scores or {}).items() if k in set(visible)}
