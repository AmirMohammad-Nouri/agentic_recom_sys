# Dataset: use your own recipes

By default the app ranks the 9,891 HUMMUS recipes (food.com). For a study on your own, private recipes, build a catalogue file from them and point the config at it.

## 1. Write the recipes in the template

Download the template (button on the Settings tab, or `foodrec/web/guides/recipes_template.json`). It is a JSON list, or `{"recipes": [...]}`:

```json
{"recipes": [
  {"id": "1",
   "title": "Chickpea and Spinach Curry",
   "ingredients": [
     {"name": "chickpeas", "amount": "2 cups", "text": "2 cups cooked chickpeas"},
     {"name": "spinach", "amount": "200 g"},
     "onion"],
   "nutrition_per_100g": {"kcal": 118, "fat_g": 5.1, "sat_fat_g": 3.6, "sugars_g": 2.1,
                          "sodium_mg": 160, "cholesterol_mg": 0, "fibre_g": 4.2,
                          "protein_g": 4.6, "carbs_g": 13.0},
   "nutrition_per_serving": {"kcal": 330},
   "tags": ["Vegetarian", "Vegan"],
   "url": "https://...", "image_url": "https://...",
   "fsa_score": 0.62,
   "extra": {"price_eur": 2.1, "prep_minutes": 35}}
]}
```

| Field | Required | Notes |
|---|---|---|
| `title` | yes | Shown to users; also used for cuisine and meal labels |
| `ingredients` | yes | Strings (`"onion"`) or objects with `name`, `amount` (`"2 cups"`, `"200 g"`, `"1"`), `text` |
| `nutrition_per_100g.kcal` | yes | Recipes without energy are dropped |
| other `nutrition_per_100g` keys | recommended | `fat_g`, `sat_fat_g`, `sugars_g`, `sodium_mg`, `cholesterol_mg`, `fibre_g`, `protein_g`, `carbs_g`. Missing ones lower the healthiness coverage; below the minimum coverage healthiness counts as missing |
| `nutrition_per_serving` | optional | Same keys; gives the serving size (kcal per serving / kcal per 100 g) |
| `tags` | optional | `Vegetarian`, `Vegan`, `Contains Gluten`, `Contains Pork`, `Shellfish` help the diet and allergen rules |
| `id` | optional | Defaults to the position; recipe ids become `r<id>` |
| `fsa_score` | optional | 0-1, 1 = healthiest; one of the nine healthiness factors |
| `extra` | optional | Any **numbers** (price, prep time, a rating ...). They become fields `extra.<name>` that you can use as criteria (Criteria tab) |

Amounts are converted to grams from units (cup, tbsp, g, ml, oz ...) and food-group densities. A spreadsheet in the original HUMMUS column layout also works (`.xlsx` or `.csv`; see `hummus_columns.csv` for the columns): use `--xlsx` instead of `--recipes`.

## 2. Build the catalogue

```
python -m foodrec.ingest --recipes my_recipes.json --name my_study \
       --out data/my_study/catalogue.json.gz
```

With Ollama running, a local model labels food groups, cuisine and meal type; add `--no-llm` for keyword rules only. The build derives CO₂e footprints, allergens and diets from the ingredient names, and reports how many recipes were dropped and why.

## 3. Point the config at it

- **catalogue_path**: `data/my_study/catalogue.json.gz`
- **data_dir**: a **new** folder, e.g. `data/my_study/runtime`, so the users, events and search index of this study stay separate from other datasets.

Save: the app restarts with the new dataset. `data/*/runtime/` is ignored by git. If the recipes are confidential, add the catalogue path to `.gitignore` as well.

## Checks

- **Allergens and diets fail closed**: an ingredient the classifier does not know marks the recipe's allergen data as incomplete, and users with allergy rules will not see it. Use common ingredient names.
- The **method notes folder** (`knowledge_dir`) holds markdown files the assistant cites ("how is healthiness calculated?"). Copy `data/knowledge` and edit it if your criteria or data differ.
