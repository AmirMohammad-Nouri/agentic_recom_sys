"""Greedy maximal-marginal-relevance reranker.

Definitions (documented in docs/features.md):
* similarity: cosine between recipe vectors (cuisine, ingredient shares, categories),
  see Catalogue.vector.
* diversity setting d in [0, 1]: lambda = 1 - d. d = 0 reproduces utility order.
* first item: the highest-utility recipe (unless recent lists are given, see below).
* selection step: maximise  lambda * U(i) - (1 - lambda) * max_{j in S} sim(i, j).
* variety across lists: with `history` (recipes of the user's recent lists), the similarity
  term is max(max_{j in S} sim(i, j), HISTORY_WEIGHT * max_{h in history} sim(i, h)), so a new
  list also keeps away from what was just shown. d = 0 still reproduces utility order.
* tie breaking: higher utility, then lexicographically smaller recipe id.
Utility rank and final rank are both returned.
"""


HISTORY_WEIGHT = 0.5  # recent lists count half as much as the dishes already in this list


def rerank(candidates, utilities, similarity, k=5, diversity=0.3, history=None):
    """candidates: list of recipe dicts; utilities: {id: U}; similarity(a, b) -> [0, 1];
    history: recipe dicts shown in recent lists (variety across lists).

    Returns a list of dicts (final order) with utility_rank, final_rank and diversity info.
    """
    if not 0.0 <= diversity <= 1.0:
        raise ValueError("diversity must be in [0, 1]")
    lam = 1.0 - diversity
    order = sorted(candidates, key=lambda r: (-utilities[r["id"]], r["id"]))
    urank = {r["id"]: i + 1 for i, r in enumerate(order)}
    opos = {r["id"]: i for i, r in enumerate(order)}  # utility order, ties already by id
    chosen, remaining = [], list(order)
    infos = []
    while remaining and len(chosen) < k:
        best, best_val, best_sim, best_near = None, None, 0.0, None
        for r in remaining:
            sims = [(similarity(r, c), c["id"]) for c in chosen]
            if history and diversity > 0:
                sims += [(HISTORY_WEIGHT * similarity(r, h), "shown:" + h["id"]) for h in history]
            if sims:
                msim, near = max(sims, key=lambda x: (x[0], x[1]))
            else:
                msim, near = 0.0, None
            val = lam * utilities[r["id"]] - (1.0 - lam) * msim
            key = (val, utilities[r["id"]], -opos[r["id"]])
            if best is None or key > best_val:
                best, best_val, best_sim, best_near = r, key, msim, near
        remaining.remove(best)
        chosen.append(best)
        infos.append({"id": best["id"], "mmr_value": best_val[0], "max_similarity": best_sim,
                      "nearest_selected": best_near})
    out = []
    final_ids = [c["id"] for c in chosen]
    for pos, (c, info) in enumerate(zip(chosen, infos), start=1):
        # recipes with higher utility that were passed over before this position
        skipped = [r for r in order
                   if urank[r["id"]] < urank[c["id"]] and
                   (r["id"] not in final_ids or final_ids.index(r["id"]) + 1 > pos)]
        skipped_info = None
        if skipped:
            s = skipped[0]
            skipped_info = {"id": s["id"], "utility": utilities[s["id"]],
                            "similarity_to_selected": max(
                                (similarity(s, x) for x in chosen[:pos - 1]), default=0.0)}
        out.append({
            "recipe": c, "utility_rank": urank[c["id"]], "final_rank": pos,
            "max_similarity_to_earlier": info["max_similarity"],
            "most_similar_earlier": info["nearest_selected"],
            "promoted_by_diversity": pos < urank[c["id"]],
            "demoted_by_diversity": pos > urank[c["id"]],
            "passed_over": skipped_info,
        })
    return out
