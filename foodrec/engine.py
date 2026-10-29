"""Recommendation pipeline: filter -> candidates -> scoring -> rerank -> evidence.

Eligibility rules, criterion definitions, normalisation, weighting and diversity are
identical for both computation modes. Only the preference estimate P differs. Both modes
rank the SAME candidate set (controlled comparison).

Candidate pool (when more than `candidate_cap` recipes are eligible): the highest-utility
recipes under the requested weights come first (half the cap, at least k), then the best
specialists per criterion fill the rest. In lightweight mode with diversity 0 the top k is
therefore identical to ranking the whole eligible catalogue.

Repetition: a recipe eaten recently (chosen, or rejected as "had it recently") loses
    penalty = repeat_penalty * 0.5 ** (days since / repeat_half_life_days)
utility for repeat_window_days. It is disclosed per recipe and changes nothing else.
"""
import math

from . import CRITERIA, CRITERION_LABELS, TASTE_SHARE
from .constraints import (active_constraints, diagnose, eligible, per_serving,
                          request_filter_fails, validate_request_filters)
from .diversity import rerank
from .explain import build_evidence, ground_narrative, make_pack, template_reasons
from .features import DEFAULT_GOAL, breakdown, health_grade, raw_values
from .preference import concept_preference, concept_support, extended_preference
from .ranker import DEFAULT_WEIGHTS, MAX_MISSING_SHARE, effective_weights, resolve_weights, utility
from .router import route
from .telemetry import Telemetry

DEFAULT_CONFIG = {"weights": dict(DEFAULT_WEIGHTS), "diversity": 0.3, "mode": "lightweight",
                  "k": 5, "missing_policy": "renormalize", "candidate_cap": 60,
                  "request_filters": [], "health_goal": DEFAULT_GOAL, "taste_share": TASTE_SHARE,
                  "max_missing_share": MAX_MISSING_SHARE, "repeat_penalty": 0.15,
                  "repeat_half_life_days": 3.0, "repeat_window_days": 14.0}


def repetition_penalty(last_ts, now, cfg):
    """Utility lost because the recipe was eaten recently (0 when never or long ago)."""
    if last_ts is None:
        return 0.0
    days = max(0.0, ((now or 0.0) - last_ts) / 86400.0)
    if days >= cfg["repeat_window_days"]:
        return 0.0
    return cfg["repeat_penalty"] * 0.5 ** (days / cfg["repeat_half_life_days"])


def _dedupe(recipes):
    seen, keep, dropped = {}, [], []
    for r in sorted(recipes, key=lambda x: x["id"]):
        key = (r["title"].strip().lower(), tuple(sorted(i["name"] for i in r["ingredients"])))
        if key in seen:
            dropped.append({"id": r["id"], "duplicate_of": seen[key]})
        else:
            seen[key] = r["id"]
            keep.append(r)
    return keep, dropped


def _pool(candidates, scaler, taste, catalogue, cap, now, goal, weights, cfg, penalties):
    """Cap the candidate set without losing the best recipes.

    1. the top max(k, cap/2) recipes by utility (exact utility with the lightweight taste
       estimate, missing-value policy and repetition penalty), then
    2. the best recipes per criterion (specialists), round-robin, until the cap is reached.
    """
    if len(candidates) <= cap:
        return candidates, False
    cols = {"p": {r["id"]: concept_preference(catalogue, taste, r, now) for r in candidates}}
    for r in candidates:
        for k, v in scaler.scores(r, goal).items():
            cols.setdefault(k, {})[r["id"]] = v
    util = {}
    for r in candidates:
        rid = r["id"]
        u = utility(weights, {k: cols[k].get(rid) for k in CRITERIA}, cfg["missing_policy"],
                    cfg["max_missing_share"])[0]
        if u is not None:
            util[rid] = u - penalties.get(rid, 0.0)
    n_top = min(cap, max(int(cfg["k"]), cap // 2))
    order = sorted(util, key=lambda rid: (-util[rid], rid))
    chosen = list(order[:n_top])
    seen = set(chosen)
    lists = [sorted((rid for rid, v in cols[k].items() if v is not None and rid in util),
                    key=lambda rid: (-cols[k][rid], rid)) for k in CRITERIA]
    pos = [0] * len(lists)
    while len(chosen) < cap and any(pos[i] < len(lists[i]) for i in range(len(lists))):
        for i, lst in enumerate(lists):
            while pos[i] < len(lst) and lst[pos[i]] in seen:
                pos[i] += 1
            if pos[i] < len(lst) and len(chosen) < cap:
                chosen.append(lst[pos[i]])
                seen.add(lst[pos[i]])
    return [r for r in candidates if r["id"] in seen], True


def recommend(catalogue, scaler, learner, declared, config, now, cache=None, tele=None,
              narrator=None):
    tele = tele or Telemetry()
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in config.items() if v is not None})
    pipeline = route(cfg["mode"])
    values, weights_msg = resolve_weights(cfg["weights"], cfg.get("last_valid_weights") or DEFAULT_WEIGHTS)
    weights = effective_weights(values, cfg["taste_share"])
    goal = cfg["health_goal"] or DEFAULT_GOAL
    rfilters = validate_request_filters(cfg["request_filters"])
    taste = learner.taste
    temp = learner.temporary_effects(now) if hasattr(learner, "temporary_effects") else {"hidden": []}
    recent = getattr(learner, "recent", {}) or {}
    out = {"config": {"weights_requested": cfg["weights"], "values_applied": values, "weights_applied": weights,
                      "taste_share": cfg["taste_share"], "health_goal": goal,
                      "weights_message": weights_msg, "diversity": cfg["diversity"],
                      "mode": cfg["mode"], "k": cfg["k"], "missing_policy": cfg["missing_policy"],
                      "candidate_cap": cfg["candidate_cap"], "request_filters": rfilters,
                      "max_missing_share": cfg["max_missing_share"],
                      "min_nutrient_coverage": getattr(scaler, "min_coverage", None),
                      "repeat_penalty": cfg["repeat_penalty"],
                      "repeat_half_life_days": cfg["repeat_half_life_days"]},
           "pipeline": {"mode": cfg["mode"], "preference_scorer": pipeline["preference_scorer"],
                        "uses_llm": pipeline["uses_llm"]},
           "versions": {"catalogue": catalogue.version, "scaler": scaler.version},
           "results": [], "ranking_table": [], "empty": None, "narrative": None}

    # ---- 1. mandatory constraints -------------------------------------------------
    with tele.stage("filter"):
        elig, report = eligible(catalogue, declared)
        by_filter, kept = {}, []
        hidden = set(temp.get("hidden") or [])
        n_hidden = sum(1 for r in elig if r["id"] in hidden)
        elig = [r for r in elig if r["id"] not in hidden]
        for r in elig:
            fails = [f["label"] for f in rfilters if request_filter_fails(r, f)]
            for lab in fails:
                by_filter[lab] = by_filter.get(lab, 0) + 1
            if not fails:
                kept.append(r)
    out["eligibility"] = {"eligible_count": report["eligible_count"],
                          "excluded_count": report["excluded_count"],
                          "excluded_by_code": report["excluded_by_code"],
                          "after_request_filters": len(kept),
                          "excluded_by_request_filter": by_filter,
                          "hidden_for_today": n_hidden}
    out["_excluded"] = report["excluded"]
    if elig and not kept:
        out["empty"] = {"message": ("Your mandatory rules leave recipes, but none also meets the "
                                    "filters of this request. Loosen or drop one of them."),
                        "constraints": [], "request_filters": [
                            {"label": f["label"], "excluded": by_filter.get(f["label"], 0)}
                            for f in rfilters]}
        out["telemetry"] = tele.summary()
        return out
    elig = kept
    if not elig:
        out["empty"] = diagnose(catalogue, declared)
        out["telemetry"] = tele.summary()
        return out

    # ---- 2. fixed candidate set ---------------------------------------------------
    with tele.stage("candidates"):
        cands, dropped = _dedupe(elig)
        penalties = {r["id"]: repetition_penalty(recent.get(r["id"]), now, cfg) for r in cands}
        penalties = {k: v for k, v in penalties.items() if v > 0}
        cands, capped = _pool(cands, scaler, taste, catalogue, cfg["candidate_cap"], now, goal, weights, cfg,
                              penalties)
    out["candidates"] = {"ids": [r["id"] for r in cands], "duplicates_removed": dropped,
                         "capped": capped}

    # ---- 3. scores and utility ----------------------------------------------------
    key = (catalogue.version, scaler.version, cfg["mode"], goal, learner.n_events,
           tuple(r["id"] for r in cands), int(now // 3600) if now else 0)
    with tele.stage("scoring"):
        if cache is not None and key in cache:
            cached = cache[key]
            tele.cache = "hit"
        else:
            tele.cache = "miss" if cache is not None else "not_used"
            cached = {}
            for r in cands:
                s = scaler.scores(r, goal)
                if cfg["mode"] == "extended":
                    p, info = extended_preference(catalogue, taste, r, learner.history, now)
                else:
                    p, info = concept_preference(catalogue, taste, r, now), None
                s["p"] = p
                cached[r["id"]] = {"scores": s, "p_info": info,
                                   "support": concept_support(catalogue, taste, r, now)}
            if cache is not None:
                cache[key] = cached
        rows, unranked = [], []
        for r in cands:
            c = cached[r["id"]]
            u, eff, missing, contrib = utility(weights, c["scores"], cfg["missing_policy"],
                                               cfg["max_missing_share"])
            if u is None:
                unranked.append({"recipe_id": r["id"], "missing_criteria": missing})
                continue
            pen = penalties.get(r["id"], 0.0)
            rows.append({"recipe": r, "scores": c["scores"], "utility": u - pen, "utility_before_penalty": u,
                         "repetition_penalty": pen, "last_eaten_ts": recent.get(r["id"]) if pen else None,
                         "effective_weights": eff,
                         "missing_criteria": missing, "missing_policy": cfg["missing_policy"],
                         "contributions": contrib, "p_info": c["p_info"], "support": c["support"]})
    out["eligibility"]["not_ranked_missing_data"] = len(unranked)
    out["candidates"]["not_ranked_missing_data"] = unranked[:50]
    if not rows:
        out["empty"] = {"message": ("No eligible recipe has enough data for the criteria you weighted "
                                    f"({len(unranked)} were left out because the missing scores carry more than "
                                    f"{round(100 * cfg['max_missing_share'])}% of your weight). "
                                    "Raise the weight of a criterion that can be scored."),
                        "constraints": []}
        out["telemetry"] = tele.summary()
        return out

    # ---- 4. diversity-aware list --------------------------------------------------
    with tele.stage("rerank"):
        util = {x["recipe"]["id"]: x["utility"] for x in rows}
        by_id = {x["recipe"]["id"]: x for x in rows}
        final = rerank([x["recipe"] for x in rows], util,
                       lambda a, b: catalogue.similarity(a, b), cfg["k"], cfg["diversity"])
    ranked = []
    for f in final:
        x = dict(by_id[f["recipe"]["id"]])
        x.update({k: f[k] for k in ("utility_rank", "final_rank", "max_similarity_to_earlier",
                                    "most_similar_earlier", "promoted_by_diversity",
                                    "demoted_by_diversity", "passed_over")})
        ranked.append(x)
    shown = {x["recipe"]["id"]: x["final_rank"] for x in ranked}
    urank = {r["recipe"]["id"]: i + 1 for i, r in enumerate(
        sorted(rows, key=lambda x: (-x["utility"], x["recipe"]["id"])))}
    out["ranking_table"] = [{"recipe_id": rid, "utility": util[rid], "utility_rank": urank[rid],
                             "final_rank": shown.get(rid), "scores": by_id[rid]["scores"]}
                            for rid in sorted(util, key=lambda i: urank[i])]

    # ---- 5. evidence, reasons, optional narrative ---------------------------------
    with tele.stage("evidence"):
        labels = active_constraints(declared)
        evs = []
        for x in ranked:
            x["constraint_labels"] = labels
            r = x["recipe"]
            bd = breakdown(r, goal, getattr(scaler, "min_coverage", 0.0))
            ev = build_evidence(x, ranked, declared, x["support"], bd)
            evs.append(ev)
            fp = r.get("footprint") or {}
            out["results"].append({
                "recipe_id": r["id"], "title": r["title"], "cuisine": r["cuisine"], "meal": r.get("meal"),
                "image_url": r.get("image_url"), "url": r.get("url"),
                "n_ingredients": r.get("n_ingredients"), "kcal": r.get("kcal_per_serving"),
                "co2e_kg_per_kg": fp.get("kg_co2e_per_kg"), "co2e_kg_per_serving": fp.get("kg_co2e_per_serving"),
                "ingredients": [i["name"] for i in r["ingredients"]],
                "ingredients_detail": [{k: i.get(k) for k in ("name", "text", "amount", "grams", "category")}
                                       for i in r["ingredients"]],
                "nutrition_per_100g": r.get("nutrition_per_100g", {}),
                "nutrition_per_serving": r.get("nutrition_per_serving", {}),
                "serving_grams": r.get("serving_grams"), "fsa_score": r.get("fsa_score"),
                "diet": r["diet"], "allergens": r["allergens"],
                "allergens_complete": r.get("allergens_complete", False),
                "health_grade": health_grade(x["scores"].get("h")),
                "per_serving": {k: (None if per_serving(r, k) is None else round(per_serving(r, k), 1))
                                for k in ("protein_g", "sugars_g", "fibre_g", "sat_fat_g")},
                "scores": x["scores"], "raw": raw_values(r), "breakdown": bd, "utility": x["utility"],
                "utility_rank": x["utility_rank"], "final_rank": x["final_rank"],
                "effective_weights": x["effective_weights"], "contributions": x["contributions"],
                "missing_criteria": x["missing_criteria"], "p_info": x["p_info"],
                "missing_info": _missing_info(x, bd),
                "repetition": ({"penalty": x["repetition_penalty"], "last_eaten_ts": x["last_eaten_ts"],
                                "utility_before_penalty": x["utility_before_penalty"]}
                               if x["repetition_penalty"] else None),
                "evidence": ev, "reasons": _with_repetition(template_reasons(ev), x, now),
            })
    if pipeline["narrative_allowed"] and narrator is not None:
        with tele.stage("narrative"):
            pack = make_pack(evs, [r["title"] for r in catalogue.recipes],
                             {"weights": weights, "diversity": cfg["diversity"], "mode": cfg["mode"]})
            out["narrative"] = ground_narrative(pack, narrator, tele)
    out["telemetry"] = tele.summary()
    return out


def _missing_info(x, bd):
    """What is unknown about this recipe and how the ranking handled it (shown on the card)."""
    out = []
    h = bd["h"]
    if h.get("below_min_coverage"):
        out.append({"criterion": "h", "text": (f"Healthiness not scored: only {round(100 * h['coverage'])}% of the "
                                                f"nutrient data is known (minimum {round(100 * h['min_coverage'])}%). "
                                                f"Missing: {', '.join(h['missing']).lower()}.")})
    elif h["missing"]:
        out.append({"criterion": "h", "text": (f"Healthiness uses {round(100 * h['coverage'])}% of the nutrient data; "
                                                f"missing: {', '.join(h['missing']).lower()}.")})
    for k in x["missing_criteria"]:
        if k in ("s", "t"):
            out.append({"criterion": k, "text": f"{CRITERION_LABELS[k]} could not be scored."})
    if any(k != "p" for k in x["missing_criteria"]) and x["missing_policy"] == "renormalize":
        out.append({"criterion": None, "text": ("Its other values were weighted up to fill the gap; taste keeps "
                                                "its fixed share.")})
    return out


def _with_repetition(reasons, x, now):
    if x["repetition_penalty"]:
        days = max(0.0, ((now or 0.0) - (x["last_eaten_ts"] or 0.0)) / 86400.0)
        when = "today" if days < 1 else f"{int(days)} day{'s' if int(days) != 1 else ''} ago"
        reasons = dict(reasons)
        reasons["ranked_here"] = list(reasons.get("ranked_here", [])) + [
            f"you had it {when}, so it carries a temporary repetition penalty of "
            f"{x['repetition_penalty']:.2f} (utility {x['utility_before_penalty']:.2f} before it)"]
    return reasons


def public(result):
    """Strip internal-only fields before returning a result to a client."""
    return {k: v for k, v in result.items() if not k.startswith("_")}
