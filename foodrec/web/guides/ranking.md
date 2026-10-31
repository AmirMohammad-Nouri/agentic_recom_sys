# Ranking

- **top_k**: recipes per list (default 5).
- **default_diversity** / **default_mode**: the starting goal of new users. Diversity 0 = pure utility order; higher values promote dishes that differ from earlier picks (utility rank and final rank are both recorded).
- **max_missing_share**: a recipe whose unscored criteria carry more than this share of the user's weight is not ranked; otherwise the other value weights are scaled up for that recipe and the card says so.
- **min_nutrient_coverage**: below this share of nutrient factors, healthiness counts as missing instead of being computed from a few nutrients.

Every list is stored as an immutable snapshot with the exact settings, so changing these values never changes past results in the export.
