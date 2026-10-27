"""Controllable multi-criteria food recommendation demo."""

__version__ = "0.3.0"

# The user sets three values: healthiness, sustainability and convenience. Ranking adds a
# fixed share for personal taste (learned from likes and rejections), so the utility is
#   U = TASTE_SHARE * taste + (1 - TASTE_SHARE) * (w_h * h + w_s * s + w_t * t).
VALUE_CRITERIA = ("h", "s", "t")
CRITERIA = ("p",) + VALUE_CRITERIA
TASTE_SHARE = 0.25
CRITERION_LABELS = {
    "p": "Taste match",
    "h": "Healthiness",
    "s": "Sustainability",
    "t": "Convenience",
}
