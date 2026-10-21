"""Deterministic computation router. The requested mode always selects the same pipeline;
there is no fallback to an LLM and no silent mode switching."""

MODES = {
    "lightweight": {
        "label": "Lightweight",
        "description": ("Uses cached scores and your stored taste beliefs. Fast, no AI model "
                        "call, explanations from templates."),
        "preference_scorer": "concept",
        "uses_llm": False,
        "narrative_allowed": False,
    },
    "extended": {
        "label": "Extended",
        "description": ("Adds a second look at your past likes, saves and rejections for every "
                        "candidate. Slower; may add a short written summary if a text model is "
                        "configured. It does not guarantee better results."),
        "preference_scorer": "concept+history_kernel",
        "uses_llm": False,          # the scorer itself never calls an LLM
        "narrative_allowed": True,  # optional, grounded, validated narrative
    },
}


def route(mode):
    if mode not in MODES:
        raise ValueError(f"unknown computation mode '{mode}'; expected one of {sorted(MODES)}")
    return MODES[mode]
