"""Controllable multi-criteria food recommendation demo."""

__version__ = "0.1.0"

# Criterion keys and the order used everywhere: preference match, healthiness,
# sustainability, affordability (cost), convenience (preparation time).
CRITERIA = ("p", "h", "s", "c", "t")
CRITERION_LABELS = {
    "p": "Preference match",
    "h": "Healthiness",
    "s": "Sustainability",
    "c": "Affordability",
    "t": "Convenience",
}
