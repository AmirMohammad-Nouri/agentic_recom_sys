"""Controllable multi-criteria food recommendation demo."""

from . import model_spec as _model_spec

__version__ = "0.4.0"

# The user weighs the value criteria (by default healthiness, sustainability and convenience;
# configurable in the "criteria" section of foodrec.config.json, see model_spec.py). Ranking
# adds a fixed share for personal taste (learned from likes and rejections), so the utility is
#   U = TASTE_SHARE * taste + (1 - TASTE_SHARE) * sum_k w_k * score_k.
# The spec is read once at import; changing it restarts the app.
MODEL_SPEC, MODEL_SPEC_ERRORS, MODEL_SPEC_SOURCE = _model_spec.load()
CRITERION_SPECS = {c["id"]: c for c in MODEL_SPEC["criteria"]["items"] if c["enabled"]}
VALUE_CRITERIA = tuple(CRITERION_SPECS)
CRITERIA = ("p",) + VALUE_CRITERIA
TASTE_SHARE = MODEL_SPEC["criteria"]["taste_share"]
CRITERION_LABELS = {"p": "Taste match", **{k: c["label"] for k, c in CRITERION_SPECS.items()}}
INTERACTIONS = MODEL_SPEC["interactions"]
