# How sustainability and convenience are scored

## How sustainability is measured
Sustainability is the estimated climate footprint of the dish in kg CO2e per kg of food. Every ingredient line is converted to grams (amounts, units and pack sizes are parsed; when no amount is given a typical portion for that food group is used). Each ingredient is assigned to a food group (beef, poultry, cheese, pulses, vegetables, ...) and the group's emission factor from Poore and Nemecek (2018), as published by Our World in Data, is applied. Footprint = sum of grams x factor / total grams. Water is left out, because cooking water is mostly drained or evaporated.

## From footprint to score
The score uses a log scale because footprints range from about 0.5 to over 30 kg CO2e per kg: 0.5 or less scores 1, 30 or more scores 0, and in between score = (ln 30 - ln x) / (ln 30 - ln 0.5). Halving a footprint always gains the same amount of score. The recipe card shows which ingredients cause most of the footprint.

## Limits of the estimate
Factors are global averages for a food group, not for a brand or a farm. Salt and spices count with their own small factors. The estimate ranks dishes against each other; it is not a life-cycle assessment.

## Convenience
Convenience is the number of ingredients (from the ingredients_title column of the HUMMUS data, after removing notes and duplicates). 3 or fewer ingredients score 1, 15 or more score 0, linear in between: score = (15 - n) / 12.
