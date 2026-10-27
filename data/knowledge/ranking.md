# How recipes are ranked

## Mandatory rules first
Allergies, diets, strict dislikes and a strict ingredient limit remove recipes before anything is scored. A recipe whose allergen data is incomplete is excluded for anyone with an allergy (fail closed).

## Four scores
Each remaining recipe has four scores between 0 and 1: taste match (learned from your likes and rejections), healthiness (nine nutrient factors weighted by your health goal), sustainability (kg CO2e per kg, log scale) and convenience (number of ingredients).

## Utility
You set three values: healthiness, sustainability and convenience, which sum to 1. Taste always keeps a fixed 25 % share, so U = 0.25 x taste + 0.75 x (w_h x healthiness + w_s x sustainability + w_t x convenience). For example values 0.5, 0.3, 0.2 and scores taste 0.8, healthiness 0.9, sustainability 0.55, convenience 0.85 give U = 0.78875.

## Why taste has a fixed share
Taste is not something people want to trade off on a slider; it is learned from behaviour. Keeping it at a fixed share means the three sliders mean exactly what they say, and a dish you would never eat still cannot win on numbers alone.

## Diversity
The top five are picked greedily: each next dish maximises (1 - d) x utility - d x similarity to dishes already picked. With d = 0 the list is the pure utility order. Both the utility rank and the final rank are recorded.
