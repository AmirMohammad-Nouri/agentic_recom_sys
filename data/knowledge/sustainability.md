# How sustainability, affordability and convenience are scored

## Sustainability
The raw value is ln(1 + kg CO2e per serving), where the footprint is the sum over ingredients of grams times a kg CO2e per kg factor. Lower is better. In the demo catalogue every factor is a rounded placeholder (provenance fixture_placeholder) and must not be cited. A recipe with an unknown factor has no sustainability score; it is never given an average.

## Affordability
The raw value is the cost in EUR per serving (ingredient price per kg times grams). Lower is better. Demo prices are placeholders with no date or shop.

## Convenience
The raw value is the preparation time in minutes. Lower is better.

## Scaling
Each raw value is mapped to 0-1 with the 5th-95th percentile range of the frozen catalogue and clipped; higher is always better after scaling. Food footprint and computing energy are never added together.
