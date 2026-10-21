"""Recommendation pipeline: filter -> candidates -> scoring -> rerank -> evidence.

Eligibility rules, criterion definitions, normalisation, weighting and diversity are
identical for both computation modes. Only the preference estimate P differs. Both modes
rank the SAME candidate set (controlled comparison).
"""
import math

from . import CRITERIA, CRITERION_LABELS
from .constraints import active_constraints, diagnose, eligible
from .diversity import rerank
from .explain import build_evidence, ground_narrative, make_pack, template_reasons
from .features import nutriscore, raw_values
from .preference import concept_preference, concept_support, extended_preference
from .ranker import DEFAULT_WEIGHTS, resolve_weights, utility
from .router import route
from .telemetry import Telemetry

DEFAULT_CONFIG = {"weights": dict(DEFAULT_WEIGHTS), "diversity": 0.3, "mode": "lightweight",
                  "k": 5, "missing_policy": "renormalize", "candidate_cap": 60}


def _dedupe(recipes):
    seen, keep, dropped = {}, [], []
    for r in sorted(recipes, key=lambda x: x["id"]):
        key = (r["title"].strip().lower(), r["cuisine"],
               tuple(sorted((i["name"], i["grams"]) for i in r["ingredients"])))
        if key in seen:
            dropped.append({"id": r["id"], "duplicate_of": seen[key]})
        else:
            seen[key] = r["id"]
            keep.append(r)
    return keep, dropped


def _pool(candidates, scaler, taste, catalogue, cap, now):
    """Coverage-preserving cap: best ceil(cap/5) per criterion (incl. light P), union, then fill."""
    if len(candidates) <= cap:
        return candidates, False
    per = max(1, math.ceil(cap / 5))
    chosen = set()
    cols = {"p": {r["id"]: concept_preference(catalogue, taste, r, now) for r in candidates}}
    for r in candidates:
        for k, v in scaler.scores(r).items():
            cols.setdefault(k, {})[r["id"]] = v
    for k in CRITERIA:
        ranked = sorted((rid for rid, v in cols[k].items() if v is not None),
                        key=lambda rid: (-cols[k][rid], rid))
        chosen.update(ranked[:per])
    if len(chosen) < cap:
        base = {r["id"]: sum(DEFAULT_WEIGHTS[k] * (cols[k].get(r["id"]) or 0.0) for k in CRITERIA)
                for r in candidates}
        for rid in sorted(base, key=lambda x: (-base[x], x)):
            if len(chosen) >= cap:
                break
            chosen.add(rid)
    return [r for r in candidates if r["id"] in chosen], True


def recommend(catalogue, scaler, learner, declared, config, now, cache=None, tele=None,
              narrator=None):
    tele = tele or Telemetry()
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in config.items() if v is not None})
    pipeline = route(cfg["mode"])
    weights, weights_msg = resolve_weights(cfg["weights"], cfg.get("last_valid_weights") or DEFAULT_WEIGHTS)
    taste = learner.taste
    out = {"config": {"weights_requested": cfg["weights"], "weights_applied": weights,
                      "weights_message": weights_msg, "diversity": cfg["diversity"],
                      "mode": cfg["mode"], "k": cfg["k"], "missing_policy": cfg["missing_policy"],
                      "candidate_cap": cfg["candidate_cap"]},
           "pipeline": {"mode": cfg["mode"], "preference_scorer": pipeline["preference_scorer"],
                        "uses_llm": pipeline["uses_llm"]},
           "versions": {"catalogue": catalogue.version, "scaler": scaler.version},
           "results": [], "ranking_table": [], "empty": None, "narrative": None}

    # ---- 1. mandatory constraints -------------------------------------------------
    with tele.stage("filter"):
        elig, report = eligible(catalogue, declared)
    out["eligibility"] = {"eligible_count": report["eligible_count"],
                          "excluded_count": report["excluded_count"],
                          "excluded_by_code": report["excluded_by_code"]}
    out["_excluded"] = report["excluded"]
    if not elig:
        out["empty"] = diagnose(catalogue, declared)
        out["telemetry"] = tele.summary()
        return out

    # ---- 2. fixed candidate set ---------------------------------------------------
    with tele.stage("candidates"):
        cands, dropped = _dedupe(elig)
        cands, capped = _pool(cands, scaler, taste, catalogue, cfg["candidate_cap"], now)
    out["candidates"] = {"ids": [r["id"] for r in cands], "duplicates_removed": dropped,
                         "capped": capped}

    # ---- 3. scores and utility ----------------------------------------------------
    key = (catalogue.version, scaler.version, cfg["mode"], learner.n_events,
           tuple(r["id"] for r in cands), int(now // 3600) if now else 0)
    with tele.stage("scoring"):
        if cache is not None and key in cache:
            cached = cache[key]
            tele.cache = "hit"
        else:
            tele.cache = "miss" if cache is not None else "not_used"
            cached = {}
            for r in cands:
                s = scaler.scores(r)
                if cfg["mode"] == "extended":
                    p, info = extended_preference(catalogue, taste, r, learner.history, now)
                else:
                    p, info = concept_preference(catalogue, taste, r, now), None
                s["p"] = p
                cached[r["id"]] = {"scores": s, "p_info": info,
                                   "support": concept_support(catalogue, taste, r, now)}
            if cache is not None:
                cache[key] = cached
        rows = []
        for r in cands:
            c = cached[r["id"]]
            u, eff, missing, contrib = utility(weights, c["scores"], cfg["missing_policy"])
            if u is None:
                continue
            rows.append({"recipe": r, "scores": c["scores"], "utility": u, "effective_weights": eff,
                         "missing_criteria": missing, "missing_policy": cfg["missing_policy"],
                         "contributions": contrib, "p_info": c["p_info"], "support": c["support"]})
    if not rows:
        out["empty"] = {"message": ("No eligible recipe has evidence for the criteria you weighted. "
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
            ev = build_evidence(x, ranked, declared, x["support"], nutriscore(x["recipe"].get("nutrition_per_100g")))
            evs.append(ev)
            r = x["recipe"]
            out["results"].append({
                "recipe_id": r["id"], "title": r["title"], "cuisine": r["cuisine"],
                "prep_minutes": r["prep_minutes"], "cost_eur": r.get("cost_eur_per_serving"),
                "co2e_kg": r.get("co2e_kg_per_serving"), "kcal": r.get("kcal_per_serving"),
                "ingredients": [i["name"] for i in r["ingredients"]],
                "ingredients_detail": r.get("ingredients", []),
                "nutrition_per_100g": r.get("nutrition_per_100g", {}),
                "serving_grams": r.get("serving_grams"),
                "diet": r["diet"], "allergens": r["allergens"],
                "allergens_complete": r.get("allergens_complete", False),
                "nutri_grade": (nutriscore(r["nutrition_per_100g"]) or {}).get("grade"),
                "scores": x["scores"], "raw": raw_values(r), "utility": x["utility"],
                "utility_rank": x["utility_rank"], "final_rank": x["final_rank"],
                "effective_weights": x["effective_weights"], "contributions": x["contributions"],
                "missing_criteria": x["missing_criteria"], "p_info": x["p_info"],
                "evidence": ev, "reasons": template_reasons(ev),
            })
    if pipeline["narrative_allowed"] and narrator is not None:
        with tele.stage("narrative"):
            pack = make_pack(evs, [r["title"] for r in catalogue.recipes],
                             {"weights": weights, "diversity": cfg["diversity"], "mode": cfg["mode"]})
            out["narrative"] = ground_narrative(pack, narrator, tele)
    out["telemetry"] = tele.summary()
    return out


def public(result):
    """Strip internal-only fields before returning a result to a client."""
    return {k: v for k, v in result.items() if not k.startswith("_")}
