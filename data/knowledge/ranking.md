# How recipes are ranked

## Mandatory rules first
Allergies, diets, never-include ingredients and strict time or cost limits filter eligibility before any ranking. Missing allergen data is never treated as safe: if you declare any allergy, recipes with incomplete allergen data are excluded. A diet that cannot be verified fails. Rules are never relaxed automatically; when nothing is eligible the system shows which rule removes the most recipes.

## Request-only filters
The assistant can apply filters from one message, such as "at least 25 g protein" or "under 20 minutes". They are shown with the result, are not saved, and do not change your weights.

## Weighted utility
Utility U = sum of weight times score over preference match, healthiness, sustainability, affordability and convenience. The five weights are normalised to sum to 1. Reference check: weights 0.30/0.25/0.20/0.15/0.10 and scores 0.80/0.90/0.55/0.70/0.85 give 0.765.

## Missing values
The default policy is renormalise: a criterion without evidence is dropped for that recipe and its remaining weights are rescaled. The policy is disclosed in each recipe's evidence.

## Diversity
A greedy maximal marginal relevance reranker builds the list of five. With diversity d, each step picks the recipe maximising (1 - d) * U - d * (highest similarity to recipes already chosen). Similarity is the cosine between cuisine, ingredient-share and category vectors. d = 0 reproduces pure utility order. Each recipe records both its utility rank and its final rank.

## Computation modes
Lightweight uses cached features and the concept-level taste model. Extended adds a similarity-weighted pass over your like, save, choose and reject history for the preference estimate. Both modes use the same rules, scores, weights and candidate set; extended mode does not guarantee better results.
