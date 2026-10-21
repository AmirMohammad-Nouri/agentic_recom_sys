"""Evidence records, template reasons and grounded-narrative validation.

One structured evidence record is built per result. Template text AND any optional
narrative must use only that record. A narrative containing an unsupported number,
an unknown recipe, an unsupported comparison or a banned claim is rejected and the
template is used instead. Contributions describe the behaviour of the implemented
model; they are not evidence of a causal effect on what the user would choose or on health.
"""
import re
import statistics

from . import CRITERIA, CRITERION_LABELS

GAP_TEXT = {
    "cost": "cost is unavailable for this recipe",
    "sustainability": "sustainability data is unavailable for this recipe",
    "allergens": "allergen data is incomplete",
    "animal_origin": "the animal origin of one ingredient is unknown",
}
PLACEHOLDER_TEXT = "demo placeholder value, not sourced data"


def _f2(x):
    return f"{x:.2f}"


def _pct(x):
    return f"{round(x * 100):d}%"


def build_evidence(item, ranked, declared, support, nutri):
    """item/ranked: engine result rows. Returns the evidence record for one result."""
    r = item["recipe"]
    scores, eff = item["scores"], item["effective_weights"]
    ev = {"recipe_id": r["id"], "title": r["title"]}

    # ---- why it fits the user ---------------------------------------------------
    fit = []
    for code, label in item["constraint_labels"]:
        fit.append({"type": "constraint", "code": code, "text": f"meets your mandatory rule: {label}"})
    for s in support:
        if s["mean"] >= 0.6 and s["evidence"] > 0:
            fit.append({"type": "preference", "concept": s["concept"], "mean": s["mean"],
                        "text": f"matches a taste you showed: {s['concept'].split(':', 1)[1]} "
                                f"(belief {_f2(s['mean'])})"})
    t = declared.get("max_prep_minutes")
    if t and not t.get("strict") and r["prep_minutes"] <= t["value"]:
        fit.append({"type": "soft_target", "text": f"takes {r['prep_minutes']} min, within your preferred {t['value']} min"})
    c = declared.get("max_cost_eur")
    if c and not c.get("strict") and r.get("cost_eur_per_serving") is not None and r["cost_eur_per_serving"] <= c["value"]:
        fit.append({"type": "soft_target", "text": f"costs {_f2(r['cost_eur_per_serving'])} EUR, within your preferred {c['value']} EUR"})
    ev["fit"] = fit

    # ---- why it ranked here -----------------------------------------------------
    contrib = {}
    for k in CRITERIA:
        if scores.get(k) is not None:
            contrib[k] = {"weight": eff[k], "score": scores[k], "contribution": eff[k] * scores[k]}
    top = max(contrib, key=lambda k: contrib[k]["contribution"]) if contrib else None
    ev["ranking"] = {
        "utility": item["utility"], "utility_rank": item["utility_rank"], "final_rank": item["final_rank"],
        "contributions": contrib, "top_contributor": top,
        "diversity": {
            "promoted": item["promoted_by_diversity"], "demoted": item["demoted_by_diversity"],
            "max_similarity_to_earlier": item["max_similarity_to_earlier"],
            "most_similar_earlier": item["most_similar_earlier"],
            "passed_over": item["passed_over"],
        },
    }

    # ---- compromises and comparison with a neighbouring result --------------------
    meds = {}
    for k in CRITERIA:
        vals = [x["scores"][k] for x in ranked if x["scores"].get(k) is not None]
        meds[k] = statistics.median(vals) if vals else None
    comp = []
    for k in CRITERIA:
        v, m = scores.get(k), meds[k]
        if v is not None and m is not None and (m - v) >= 0.15 and eff[k] > 0:
            comp.append({"criterion": k, "score": v, "list_median": m,
                         "text": f"{CRITERION_LABELS[k]} is lower than the other results "
                                 f"({_f2(v)} vs median {_f2(m)})"})
    ev["compromises"] = comp
    pos = item["final_rank"] - 1
    other = ranked[pos - 1] if pos > 0 else (ranked[1] if len(ranked) > 1 else None)
    if other is not None:
        deltas = {}
        for k in CRITERIA:
            a, b = scores.get(k), other["scores"].get(k)
            deltas[k] = None if a is None or b is None else a - b
        ev["comparison"] = {"vs": other["recipe"]["id"], "vs_title": other["recipe"]["title"],
                            "vs_final_rank": other["final_rank"], "deltas": deltas}
    else:
        ev["comparison"] = None

    # ---- uncertainty -------------------------------------------------------------
    unc = []
    for m in r.get("missing", []):
        unc.append({"kind": "missing", "field": m, "text": GAP_TEXT.get(m, f"{m} unavailable")})
    if r.get("cost_provenance") == "fixture_placeholder":
        unc.append({"kind": "placeholder", "field": "cost", "text": f"cost: {PLACEHOLDER_TEXT}"})
    if r.get("co2e_provenance") == "fixture_placeholder" and r.get("co2e_kg_per_serving") is not None:
        unc.append({"kind": "placeholder", "field": "sustainability", "text": f"sustainability: {PLACEHOLDER_TEXT}"})
    unc.append({"kind": "estimate", "field": "p",
                "text": "preference match is a model estimate, not an observed rating"})
    if nutri is not None:
        unc.append({"kind": "measure", "field": "h",
                    "text": f"healthiness is a Nutri-Score based operational measure (grade {nutri['grade']}), not a clinical assessment"})
    if item["missing_criteria"]:
        names = ", ".join(CRITERION_LABELS[k] for k in item["missing_criteria"])
        unc.append({"kind": "scoring", "field": "missing",
                    "text": f"{names} could not be scored; remaining weights were renormalised for this recipe"
                    if item["missing_policy"] == "renormalize" else f"{names} missing"})
    ev["uncertainty"] = unc
    ev["claims_allowed"] = sorted(claims_allowed(ev))
    return ev


def claims_allowed(ev):
    """(criterion, direction) pairs a narrative may assert for this recipe."""
    out = set()
    cmp_ = ev.get("comparison")
    if cmp_:
        for k, d in cmp_["deltas"].items():
            if d is not None and abs(d) >= 0.05:
                out.add(f"{k}:{'better' if d > 0 else 'worse'}")
    for c in ev["compromises"]:
        out.add(f"{c['criterion']}:worse")
    for k, v in ev["ranking"]["contributions"].items():
        if v["score"] >= 0.65:
            out.add(f"{k}:good")
    return out


# ---- template rendering (only evidence fields are used) ---------------------------
def template_reasons(ev):
    fits = [f["text"] for f in ev["fit"]] or ["passes all your mandatory rules"]
    rk = ev["ranking"]
    ranked = []
    if rk["top_contributor"]:
        k = rk["top_contributor"]
        v = rk["contributions"][k]
        ranked.append(f"{CRITERION_LABELS[k]} contributes most: weight {_pct(v['weight'])} x score "
                      f"{_f2(v['score'])} = {_f2(v['contribution'])} of utility {_f2(rk['utility'])}")
    d = rk["diversity"]
    if d["promoted"] and d["passed_over"]:
        ranked.append(f"moved up from utility rank {rk['utility_rank']} to {rk['final_rank']} by the diversity "
                      f"setting, because higher-utility {d['passed_over']['id']} is more similar to earlier picks "
                      f"({_f2(d['passed_over']['similarity_to_selected'])})")
    elif d["demoted"]:
        ranked.append(f"moved down from utility rank {rk['utility_rank']} to {rk['final_rank']} by the diversity setting")
    cmp_ = ev["comparison"]
    if cmp_:
        parts = []
        for k, dl in cmp_["deltas"].items():
            if dl is not None and abs(dl) >= 0.05:
                parts.append(f"{CRITERION_LABELS[k].lower()} {'higher' if dl > 0 else 'lower'} by {_f2(abs(dl))}")
        if parts:
            ranked.append(f"compared with {cmp_['vs_title']} (rank {cmp_['vs_final_rank']}): " + ", ".join(parts))
    return {
        "fits": fits,
        "ranked_here": ranked,
        "compromises": [c["text"] for c in ev["compromises"]],
        "gaps": [u["text"] for u in ev["uncertainty"]],
    }


# ---- grounded narrative -------------------------------------------------------------
BANNED = re.compile(r"\b(cure|cures|prevent|prevents|treat|treats|clinical(ly)?|doctor|diagnos\w*|"
                    r"guarantee\w*|proven|will lower|will improve|medical advice)\b", re.I)
COMPARATIVES = [
    (re.compile(r"\b(cheaper|more affordable|less expensive|lower cost)\b", re.I), "c:better"),
    (re.compile(r"\b(pricier|more expensive|costlier|higher cost)\b", re.I), "c:worse"),
    (re.compile(r"\b(healthier|more nutritious|better nutrition)\b", re.I), "h:better"),
    (re.compile(r"\b(less healthy|unhealthier)\b", re.I), "h:worse"),
    (re.compile(r"\b(greener|more sustainable|lower footprint|lower emissions)\b", re.I), "s:better"),
    (re.compile(r"\b(less sustainable|higher footprint|higher emissions)\b", re.I), "s:worse"),
    (re.compile(r"\b(quicker|faster|less time)\b", re.I), "t:better"),
    (re.compile(r"\b(slower|more time|takes longer)\b", re.I), "t:worse"),
]
_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?%?")


def _collect_numbers(obj, out):
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)
    elif isinstance(obj, str):
        for m in _NUM.findall(obj):
            try:
                out.add(float(m.rstrip("%")))
            except ValueError:
                pass


def make_pack(evidences, catalogue_titles, request_summary):
    """The only material a narrator may use."""
    nums = set()
    for e in evidences:
        _collect_numbers(e, nums)
    _collect_numbers(request_summary, nums)
    claims = set()
    for e in evidences:
        for c in e["claims_allowed"]:
            claims.add((e["recipe_id"], c))
    return {"items": evidences, "request": request_summary, "numbers": sorted(nums),
            "titles_in_list": [e["title"] for e in evidences],
            "all_titles": sorted(catalogue_titles), "claims": sorted(claims)}


def validate_narrative(text, pack):
    """Return (ok, reason). Rejects unsupported numbers, titles, comparisons and banned claims."""
    if BANNED.search(text):
        return False, "banned claim: " + BANNED.search(text).group(0)
    in_list = set(pack["titles_in_list"])
    for t in pack["all_titles"]:
        if t not in in_list and t.lower() in text.lower():
            return False, f"mentions a recipe that is not in the list: {t}"
    allowed = pack["numbers"]
    for tok in _NUM.findall(text):
        is_pct = tok.endswith("%")
        raw = tok.rstrip("%")
        x = float(raw)
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        ok = (not is_pct and decimals == 0 and 1 <= x <= 5 and x == int(x))  # rank / count words
        for a in allowed:
            if ok:
                break
            if is_pct:
                ok = abs(x - a * 100.0) <= tol or abs(x - a) <= tol
            else:
                ok = abs(x - a) <= tol
        if not ok:
            return False, f"unsupported number: {tok}"
    claim_set = {c for _, c in pack["claims"]}
    for rx, claim in COMPARATIVES:
        if rx.search(text) and claim not in claim_set:
            return False, f"unsupported comparison: {rx.search(text).group(0)}"
    return True, None


class RuleBasedNarrator:
    """Deterministic, LLM-free narrator. Useful as a reference and as a test double."""
    name = "rule_based"

    def __call__(self, pack):
        items = pack["items"]
        top = items[0]
        k = top["ranking"]["top_contributor"]
        lines = [f"Your top result is {top['title']}."]
        if k:
            lines.append(f"{CRITERION_LABELS[k]} contributes most to its score "
                         f"({_f2(top['ranking']['contributions'][k]['contribution'])}).")
        if top["compromises"]:
            lines.append(top["compromises"][0]["text"] + ".")
        if top["uncertainty"]:
            lines.append("Note: " + top["uncertainty"][0]["text"] + ".")
        return " ".join(lines)


def ground_narrative(pack, narrator, tele=None):
    """Run a narrator, validate, fall back to template. Returns a dict."""
    fallback = " ".join(template_reasons(pack["items"][0])["fits"][:1] +
                        template_reasons(pack["items"][0])["ranked_here"][:1]) if pack["items"] else ""
    if narrator is None:
        return {"text": None, "method": "none", "rejected": None}
    out = narrator(pack)
    text, usage = (out if isinstance(out, tuple) else (out, None))
    if tele is not None and getattr(narrator, "name", "") != "rule_based":
        tele.record_llm(*(usage or (0, 0)))
    ok, reason = validate_narrative(text, pack)
    if ok:
        return {"text": text, "method": getattr(narrator, "name", "narrator"), "rejected": None}
    return {"text": fallback, "method": "template_fallback", "rejected": reason}
