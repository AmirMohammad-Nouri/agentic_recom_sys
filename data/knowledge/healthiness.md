# How healthiness is scored

## Nutri-Score points
Healthiness uses the Nutri-Score points for solid foods (2015 HCSP version), computed per 100 g of the composed dish. Negative points come from energy (one point per 335 kJ above 335 kJ, up to 10), sugars (thresholds every 4.5 g), saturated fat (every 1 g) and sodium (every 90 mg). Positive points come from the share of fruit, vegetables, legumes and nuts (1 point above 40 %, 2 above 60 %, 5 above 80 %), fibre (thresholds every 0.7 g, up to 5) and protein (thresholds every 1.6 g, up to 5).

## Protein rule
Protein points are not counted when negative points reach 11 or more and the fruit and vegetable share earns fewer than 5 points. This is part of the official algorithm.

## From points to a score
The raw criterion is the negative Nutri-Score total (higher is better). It is scaled to 0-1 using the 5th and 95th percentile of the frozen catalogue, so the scale never depends on the current result list. Grades: A up to -1, B up to 2, C up to 10, D up to 18, E above.

## Limits
This is an operational measure applied to dishes. It is not a clinical assessment, it does not implement the 2023 Nutri-Score update, and it cannot judge portion size or a whole diet. Olive oil is not counted in the fruit and vegetable share; legumes are counted with vegetables.
