# Feature and parameter definitions

## Criteria

All scores are in [0, 1], and higher is always better. They are scaled with the 5th–95th percentile
of the frozen catalogue (`ReferenceScaler`, fitted once and never refitted per request).

| Key | Criterion | Raw value | Direction |
| --- | --- | --- | --- |
| `p` | Preference match | Model estimate from the taste model (lightweight) or taste model + history kernel (extended) | higher |
| `h` | Healthiness | −(Nutri-Score points), 2015 HCSP solid-foods tables, per 100 g of the dish | higher |
| `s` | Sustainability | ln(1 + kg CO₂e per serving) | lower |
| `c` | Affordability | EUR per serving | lower |
| `t` | Convenience | preparation minutes | lower |

Utility: `U = Σ_k w_k · score_k` with `w ≥ 0`, `Σ w = 1`. Reference: 0.30/0.25/0.20/0.15/0.10 ×
0.80/0.90/0.55/0.70/0.85 = 0.765 (`tests/test_reference.py::test_01`).

**Missing values** (decided before ranking): `renormalize` (default; drop the criterion for
that recipe and rescale), `zero`, or `exclude`. A missing value is never replaced by an average.

## Mandatory rules (`constraints.py`)

* Declaring any allergy excludes recipes with incomplete allergen data (fail closed).
* A diet flag that is `None` fails the diet.
* Strict time or cost limits exclude recipes whose value is unknown.
* Rules are never relaxed automatically; `diagnose()` reports how many recipes each rule removes.

**Request-only filters** (from a chat message) narrow one request, are shown with the result
and are never saved: `protein_g`, `sugars_g`, `fibre_g` (per serving), `kcal`, `prep_minutes`,
`cost_eur`, `cuisine`, `meal`, `ingredient` (`has`/`lacks`). Unknown values fail closed.

## Diversity (`diversity.py`)

Greedy MMR: pick `argmax (1−d)·U(i) − d·max_{j∈S} sim(i, j)`. Similarity is the cosine
between vectors of cuisine (0.8), ingredient gram shares and categories (0.4/√n), with
seasonings excluded. First item = highest utility. Ties go to higher utility, then lower list
position. `d = 0` reproduces utility order. Utility rank and final rank are both logged.

## Learner parameters (`learner.py: PARAMS`)

| Parameter | Value | Role |
| --- | --- | --- |
| `taste_k` | 3.0 | Pseudo-count mass of one full-strength observation |
| `taste_half_life_days` | 90 | Evidence decays toward the prior |
| `beta0` | 10 | Scale linking simplex weights to logit-scale θ |
| `prior_sd` | 1.5 | Prior SD of θ around β₀·w_stated |
| `statement_sd` | 1.0 | Noise of an explicit goal statement |
| `process_noise_per_day` | 0.004 | Preferences may drift |
| `kappa_choice` | 0.7 | Tempering of a slate choice (conditional logit) |
| `kappa_reason`, `reason_gain` | 1.0, 2.0 | Weight of a stated reason / rejection reason / trade-off answer |
| `discrepancy_delta` | 0.08 | Weight difference that counts as a discrepancy |

Feedback strengths for the taste model: like 1.0, choose 0.8, save 0.6, reject "not my
taste" 1.0, other rejections 0.5 (criterion-specific rejections update the choice model
instead). Onboarding priors: declared cuisine or liked ingredient Beta(3.5, 1.5), soft dislike
Beta(1, 3). A user correction replaces the belief with a confident one (strength 6).

## Computation modes (`router.py`)

Both modes share rules, candidate set, criteria, scaler, weights and diversity. Only `p`
differs. Lightweight uses the cached taste model. Extended uses
`(1−γ)·P_concept + γ·K`, where K is a similarity-weighted average over the like/save/choose/reject
history (γ = n/(n+4)). Neither mode calls a language model. Telemetry records per-stage wall
time, CPU time, RAPL energy where available, and LLM calls/tokens.
