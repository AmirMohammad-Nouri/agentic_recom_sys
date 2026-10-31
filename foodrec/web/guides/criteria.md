# Criteria: what users weigh

During onboarding every user sets weights for the **criteria** (by default healthiness, sustainability and convenience). The ranking is

```
utility = taste_share × taste + (1 − taste_share) × Σ weight_k × score_k
```

Each score runs from 0 (worst) to 1 (best). **Taste** is not a criterion: it keeps the fixed `taste_share` and is learned from likes and rejections.

## Built-in criteria

| Id | Kind | Score |
|---|---|---|
| `h` | healthiness | nine nutrient factors per 100 g, weighted by the user's health goal |
| `s` | sustainability | kg CO₂e per kg of dish, log scale (0.5 → 1, 30 → 0) |
| `t` | convenience | number of ingredients (3 → 1, 15 → 0) |

A built-in can be **switched off** and relabelled, but its formula is fixed. Switching off healthiness also removes the health-goal step of onboarding.

## Adding a criterion (field criteria)

A field criterion scores one numeric recipe field between two anchors:

```json
{"id": "e", "kind": "field", "label": "Low energy",
 "field": "nutrition_per_100g.kcal", "direction": "lower",
 "best": 80, "worst": 400, "scale": "linear", "unit": "kcal/100 g",
 "default_weight": 0.25, "keywords": ["energy", "calorie", "light"],
 "description": "Energy density: lighter dishes score higher."}
```

- **field**: a dotted path into the recipe. The list offers the numeric fields of the loaded catalogue, e.g. `nutrition_per_100g.protein_g`, `nutrition_per_serving.kcal`, `footprint.kg_co2e_per_serving`, `n_ingredients`, and your dataset's `extra.<name>` values (price, prep time ...).
- **direction**: `lower` if smaller values are better (energy, price, sugar), `higher` if larger are better (protein, fibre, rating).
- **best / worst**: the values that score 1 and 0; outside them the score is clipped. **Set anchors from the data** uses the 10% best and 10% worst values of the catalogue. Tight anchors spread recipes out; wide anchors make most recipes score alike, so the criterion matters less.
- **scale**: `linear`, or `log` for values that span orders of magnitude (footprints, prices).
- **keywords**: words that name it in chat ("what if I cared more about energy?").
- **default_weight**: the starting slider value at onboarding (weights are normalised).

Recipes without the field are handled like any missing score: the other weights are scaled up for that recipe, or the recipe is not ranked if too much weight is missing (Ranking → max missing share).

## Replacing a criterion

Switch the old one off and add the new one. Example: replace healthiness with protein:

1. Healthiness → enabled off.
2. Add `{"id": "pr", "kind": "field", "label": "Protein", "field": "nutrition_per_100g.protein_g", "direction": "higher", "best": 15, "worst": 2}`.
3. Check the Interactions tab: reasons about healthiness disappear; protein gets reasons (yours or automatic ones).

## How this affects results and a study

- **Lists**: utility is a weighted sum, so a new criterion with weight w changes rankings by up to w × (1 − taste_share). The supervisor, what-if previews, goal options, comparisons and the export all use the configured set.
- **Existing users** keep their saved weights. A removed criterion is dropped, a new one starts at **0** for them, and the rest is rescaled. Users who should weigh the new criterion must change their sliders. For a clean study, change criteria **before** participants start, and use a new data folder for each configuration.
- **Learning**: the revealed weights only learn about criteria that differ between the dishes shown, and reasons (Interactions) give the strongest signal. Give every criterion at least one choose and one reject reason.
- **Roles**: roles that show "every criterion" show the new one; roles with a list do not until you add it (Access roles).
- **Explanations** describe field criteria with their field, anchors and value; the assistant gets a method note for each one.
- Changing criteria **restarts the app**. Scenario files can try other criteria in their `config`; they run in a separate process automatically.
