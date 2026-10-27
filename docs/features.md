# Feature and parameter definitions

## Criteria (`features.py`)

All scores are in [0, 1], and higher is always better. Each is a fixed formula with published
anchors (no per-catalogue rescaling), so `breakdown(recipe, goal)` reproduces it factor by factor.

| Key | Criterion | Raw value | Score |
| --- | --- | --- | --- |
| `h` | Healthiness | nine factors per 100 g | goal-weighted mean of linear factor scores |
| `s` | Sustainability | kg CO₂e per kg of dish (water excluded) | (ln 30 − ln x) / (ln 30 − ln 0.5), clipped |
| `t` | Convenience | number of ingredients (`ingredients_title`) | (15 − n) / 12, clipped |
| `p` | Taste match | taste model (lightweight) or taste model + history (extended) | model estimate |

Health factors (good → poor anchor): energy 125 → 400 kcal (WCRF), fat 3 → 17.5 g, saturated
fat 1.5 → 5 g, sugars 5 → 22.5 g, sodium 120 → 600 mg (FSA traffic lights), cholesterol 20 →
100 mg, fibre 6 → 0 g, protein 20 → 0 % of energy (EU claims), FSA score 1 → 0 (HUMMUS).
Health goals re-weight them (`GOALS`); *gain weight* reverses energy. Grade A ≥ 0.80, B ≥ 0.65,
C ≥ 0.50, D ≥ 0.35, else E.

Utility: `U = 0.25 · p + 0.75 · (w_h·h + w_s·s + w_t·t)` with `w ≥ 0`, `Σ w = 1`. Reference:
w = 0.5/0.3/0.2, scores p/h/s/t = 0.8/0.9/0.55/0.85 → 0.78875 (`tests/test_reference.py`).

**Missing values** (decided before ranking): `renormalize` (default; drop the criterion for
that recipe and rescale), `zero`, or `exclude`. A missing value is never replaced by an average.

## Mandatory rules (`constraints.py`)

* Declaring any allergy excludes recipes with incomplete allergen data (fail closed).
* A diet flag that is `None` fails the diet.
* A strict ingredient limit excludes recipes above it; as a preference it is a soft target.
* Rules are never relaxed automatically; `diagnose()` reports how many recipes each rule removes.

**Request-only filters** (from a chat message) narrow one request, are shown with the result
and are never saved: `protein_min`, `sugars_max`, `kcal_max` (per serving), `max_ingredients`,
`co2e_max` (kg CO₂e per kg), `cuisine`, `meal` (also the Discover meal selector), `ingredient`
(`has`/`lacks`). Unknown values fail closed.

## Diversity (`diversity.py`)

Greedy MMR: pick `argmax (1−d)·U(i) − d·max_{j∈S} sim(i, j)`. Similarity is the cosine
between vectors of cuisine (0.8), ingredient gram shares and categories (0.4/√n), with
seasonings excluded. First item = highest utility. Ties go to higher utility, then lower list
position. `d = 0` reproduces utility order. Utility rank and final rank are both logged.

## User model (`learner.py`)

Taste: `(1 + likes) / (2 + likes + dislikes)` per concept. Values: observed weights updated
multiplicatively from choices, reasoned rejections and answers, anchored 5 % to the goal.
Full explanation, worked example and parameter table: [user-model.md](user-model.md).

## Computation modes (`router.py`)

Both modes share rules, candidate set, criteria, scaler, weights and diversity. Only `p`
differs. Lightweight uses the cached taste model. Extended uses
`(1−γ)·P_concept + γ·K`, where K is a similarity-weighted average over the like/save/choose/reject
history (γ = n/(n+4)). Neither mode calls a language model. Telemetry records per-stage wall
time, CPU time, RAPL energy where available, and LLM calls/tokens.
