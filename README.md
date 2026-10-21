# agentic_recom_sys
# foodrec: controllable multi-criteria food recommendation demo

A runnable research demo built from the project brief ("Controllable multi-criteria food
recommendation"). Users set five food priorities, a diversity level and a computation
mode, then inspect why five recipes were recommended and what changed when a setting
changed. A Bayesian learner estimates what the user actually prefers, and a supervisor
agent reports conflicts with the user's saved goals. Neither ever rewrites a saved goal
or a mandatory restriction by itself.

> **Data warning.** `data/fixture/` is a SYNTHETIC catalogue (54 recipes). Nutrient values
> are rounded approximations; prices and CO2e values are placeholders
> (`provenance: fixture_placeholder`). Do not cite or report results from it. Swap in a real
> catalogue (see "Real data" below) before any evaluation.

## Requirements

Python 3.11+ and `numpy`. Nothing else. No LLM provider is needed: lightweight mode never
calls one, and extended mode only calls one if you plug a narrator in.

## Quick start

```bash
cd foodrec
python3 data/build_fixture.py          # (re)builds data/fixture/*.json and VERSION
python3 -m unittest discover -s tests  # reference tests
python3 -m foodrec.server              # web UI + JSON API on http://127.0.0.1:8000
```

Use the engine directly from Python:

```python
from foodrec.catalogue import Catalogue
from foodrec.features import ReferenceScaler
from foodrec.learner import Learner
from foodrec.engine import recommend, public
from foodrec.ranker import DEFAULT_WEIGHTS

cat = Catalogue.load("data/fixture/recipes.json")
scaler = ReferenceScaler.fit(cat)                      # frozen reference scales
declared = {"allergens": ["peanuts"], "diets": ["vegetarian"],
            "preferred_cuisines": ["Italian"], "liked_recipes": ["r001"]}
learner = Learner(cat)
learner.apply({"event_id": "e1", "type": "profile_created", "ts": 0.0,
               "payload": {"declared": declared, "weights": DEFAULT_WEIGHTS}})
result = public(recommend(cat, scaler, learner, declared,
                          {"mode": "lightweight", "weights": DEFAULT_WEIGHTS, "diversity": 0.3},
                          now=0.0))
for r in result["results"]:
    print(r["final_rank"], r["title"], round(r["utility"], 3), r["reasons"]["ranked_here"][:1])
```

## Layout

| File | Role |
| --- | --- |
| `data/build_fixture.py` | Builds the synthetic catalogue from an ingredient table; computes recipe values and flags missing data |
| `foodrec/catalogue.py` | Loads the frozen catalogue, hashes its version, builds concept and similarity vectors |
| `foodrec/features.py` | Nutri-Score healthiness (2015 HCSP solid-foods tables), raw criteria, frozen `ReferenceScaler` |
| `foodrec/constraints.py` | Mandatory filter: allergens, diets, never-include, strict time and cost; unknown data fails closed; empty-result diagnosis |
| `foodrec/ranker.py` | Weight normalisation, weighted utility, missing-value policy |
| `foodrec/diversity.py` | Greedy maximal-marginal-relevance reranker with promotion records |
| `foodrec/preference.py` | Preference estimate P: lightweight concept scorer and extended history-kernel scorer |
| `foodrec/learner.py` | Bayesian learner: Beta taste model and Laplace choice model with provenance |
| `foodrec/explain.py` | Evidence records, template reasons, grounded-narrative validator |
| `foodrec/router.py` | Deterministic computation-mode router |
| `foodrec/telemetry.py` | Per-stage latency, CPU time, optional RAPL energy, LLM calls and tokens |
| `foodrec/engine.py` | Full pipeline: filter, candidates, scoring, rerank, evidence |
| `foodrec/compare.py` | Before/after comparison built only from two recorded results |

## Controls (what each one does)

* **Mandatory constraints** filter eligibility before any ranking. Missing allergen data is
  never treated as safe. They are never relaxed automatically.
* **Food weights** (preference match, healthiness, sustainability, affordability,
  convenience) are normalised to sum to 1 and score each recipe: `U = sum(w_k * score_k)`.
  The reference case 0.30/0.25/0.20/0.15/0.10 with scores 0.80/0.90/0.55/0.70/0.85 gives 0.765.
* **Diversity** (0 to 1) reranks the list with MMR; 0 reproduces pure utility order.
* **Computation mode**: `lightweight` (cached scores, no AI call) or `extended` (extra pass
  over your like/save/reject history for the preference estimate, same final weighting).

## Honest limitations

* Healthiness is a Nutri-Score based operational measure applied to composed dishes. The
  2023 Nutri-Score update is not implemented. It is not a clinical assessment.
* Preference match is a model estimate, not an observed rating or a calibrated probability.
* No LLM provider is bundled or tested. The narrator hook, validator and template fallback
  are implemented and tested with stand-in narrators only.
* Energy readings exist only where the machine exposes a RAPL counter; otherwise they are null.

## Real data

The loader expects the same recipe record shape as `data/fixture/recipes.json`. See
`docs/` (added with the remaining milestones) for the field list and the M1 audit notes on
XFoodRec, WiseFood and FoodNexus.
