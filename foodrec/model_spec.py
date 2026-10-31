"""The criteria and interaction options, as configured (sections "criteria" and "interactions"
of foodrec.config.json). Read ONCE when the package is imported, so every module sees plain
tuples (VALUE_CRITERIA ...) exactly as before; changing these sections restarts the app.

criteria
    taste_share   fixed share of the utility for learned taste (0-0.9)
    items         the value criteria the user weighs. Built-in kinds keep their ids:
                    healthiness (h)     goal-weighted nutrient score, with health goals
                    sustainability (s)  log-scaled kg CO2e per kg of dish
                    convenience (t)     number of ingredients
                  and any number of "field" criteria scored from one numeric recipe field:
                    {"id": "e", "kind": "field", "label": "Low energy",
                     "field": "nutrition_per_100g.kcal", "direction": "lower",
                     "best": 80, "worst": 400, "scale": "linear", "unit": "kcal/100 g"}
                  Each item: enabled, default_weight (the starting slider value),
                  description, keywords (words that name it in chat), color.
interactions
    feedback_strength   how strongly like / save / choose count as a taste like
    choose_reasons      "Why this one?" options: criterion (or null), weight (size of the
                        nudge toward that criterion), taste_bonus
    reject_reasons      "What is wrong with it?" options: criterion (or null), weight,
                        taste_strength (dislike strength when no criterion), effect "recency"
    learning            eta, nudge, anchor, floor, today_nudge, conf_k (see learner.py)

stdlib only: this module is imported by foodrec/__init__.py before anything else.
"""
import copy
import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("foodrec.model_spec")

ROOT = Path(__file__).resolve().parent.parent
BUILTIN_KINDS = {"healthiness": "h", "sustainability": "s", "convenience": "t"}
KINDS = tuple(BUILTIN_KINDS) + ("field",)
RESERVED_IDS = {"p"}
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,11}$")
PALETTE = ["#b5651d", "#8a4fb0", "#c0392b", "#16808a", "#7a8a1e", "#b0407a", "#4a6fa5", "#9a7b16"]

DEFAULT_CRITERIA = {
    "taste_share": 0.25,
    "items": [
        {"id": "h", "kind": "healthiness", "label": "Healthiness", "enabled": True, "default_weight": 0.40,
         "description": "Nine nutrient factors per 100 g (energy, fats, sugars, sodium, cholesterol, fibre, protein, "
                        "FSA score), weighted by the user's health goal.",
         "keywords": ["health", "healthy", "healthier", "nutritious", "nutrition", "nutri"], "color": None},
        {"id": "s", "kind": "sustainability", "label": "Sustainability", "enabled": True, "default_weight": 0.30,
         "description": "Estimated kg CO2e per kg of dish from ingredient amounts and food-group factors (log scale).",
         "keywords": ["sustainab", "climate", "co2", "carbon", "green", "environment", "planet", "footprint"],
         "color": None},
        {"id": "t", "kind": "convenience", "label": "Convenience", "enabled": True, "default_weight": 0.30,
         "description": "Fewer ingredients score higher (3 or fewer = 1, 15 or more = 0).",
         "keywords": ["convenien", "easy", "easier", "simple", "simpler", "fewer ingredients", "effort", "quick"],
         "color": None},
    ],
}
DEFAULT_INTERACTIONS = {
    "feedback_strength": {"like": 1.0, "save": 0.6, "choose": 0.8},
    "choose_reasons": [
        {"id": "taste", "label": "It sounds tasty", "criterion": None, "taste_bonus": 0.2},
        {"id": "health", "label": "It is healthy", "criterion": "h", "weight": 1.0},
        {"id": "climate", "label": "It is climate-friendly", "criterion": "s", "weight": 1.0},
        {"id": "easy", "label": "It is easy to make", "criterion": "t", "weight": 1.0},
    ],
    "reject_reasons": [
        {"id": "not_my_taste", "label": "Not my taste", "criterion": None, "taste_strength": 1.0},
        {"id": "not_healthy_enough", "label": "Not healthy enough", "criterion": "h", "weight": 1.0},
        {"id": "not_sustainable", "label": "Not sustainable enough", "criterion": "s", "weight": 1.0},
        {"id": "too_complicated", "label": "Too many ingredients", "criterion": "t", "weight": 1.0},
        {"id": "had_recently", "label": "Had it recently", "criterion": None, "effect": "recency"},
        {"id": "other", "label": "Something else", "criterion": None, "taste_strength": 0.5, "show": False},
    ],
    "learning": {"eta": 1.0, "nudge": 0.2, "anchor": 0.05, "floor": 0.02, "today_nudge": 0.10, "conf_k": 5},
}
LEARNING_RANGES = {"eta": (0.0, 5.0), "nudge": (0.0, 1.0), "anchor": (0.0, 1.0), "floor": (0.0, 0.3),
                   "today_nudge": (0.0, 0.5), "conf_k": (1, 100)}


def _num(x, lo=None, hi=None):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f"expected a number, got {x!r}")
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        raise ValueError(f"{x} is outside {lo}..{hi}")
    return float(x)


def normalize(criteria=None, interactions=None):
    """Validate and complete the two sections. Returns (spec, errors); on errors the
    defaults are used for the broken section, so the app always starts."""
    errors = []
    crit = _criteria(copy.deepcopy(criteria if criteria is not None else DEFAULT_CRITERIA), errors)
    if crit is None:
        crit = _criteria(copy.deepcopy(DEFAULT_CRITERIA), [])
    inter = _interactions(copy.deepcopy(interactions if interactions is not None else DEFAULT_INTERACTIONS),
                          crit, errors)
    if inter is None:
        inter = _interactions(copy.deepcopy(DEFAULT_INTERACTIONS), crit, [])
    return {"criteria": crit, "interactions": inter}, errors


def _criteria(c, errors):
    start = len(errors)
    if not isinstance(c, dict):
        errors.append("criteria: must be an object with taste_share and items")
        return None
    try:
        share = _num(c.get("taste_share", 0.25), 0.0, 0.9)
    except ValueError as e:
        errors.append(f"criteria.taste_share: {e}")
        share = 0.25
    items, seen, palette = [], set(), iter(PALETTE)
    for i, it in enumerate(c.get("items") or [], 1):
        where = f"criteria.items[{i}]"
        if not isinstance(it, dict):
            errors.append(f"{where}: must be an object")
            continue
        kind = it.get("kind", "field")
        if kind not in KINDS:
            errors.append(f"{where}: kind must be one of {', '.join(KINDS)}")
            continue
        cid = BUILTIN_KINDS.get(kind) or it.get("id")
        if kind in BUILTIN_KINDS and it.get("id") not in (None, cid):
            errors.append(f"{where}: the {kind} criterion always has the id '{cid}'")
        if not isinstance(cid, str) or not ID_RE.match(cid) or cid in RESERVED_IDS or \
                (kind == "field" and cid in BUILTIN_KINDS.values()):
            errors.append(f"{where}: id {cid!r} must be 1-12 lowercase letters/digits/_ and not p, h, s or t")
            continue
        if cid in seen:
            errors.append(f"{where}: duplicate id '{cid}'")
            continue
        seen.add(cid)
        out = {"id": cid, "kind": kind, "label": str(it.get("label") or cid), "enabled": bool(it.get("enabled", True)),
               "description": str(it.get("description") or ""),
               "keywords": [str(k).lower() for k in it.get("keywords") or [] if str(k).strip()],
               "color": it.get("color") or (None if kind in BUILTIN_KINDS else next(palette, "#777777"))}
        try:
            out["default_weight"] = _num(it.get("default_weight", 0.0), 0.0, 1.0)
        except ValueError as e:
            errors.append(f"{where}.default_weight: {e}")
            out["default_weight"] = 0.0
        if kind == "field":
            f = it.get("field")
            if not isinstance(f, str) or not f.strip():
                errors.append(f"{where}: a field criterion needs 'field', e.g. nutrition_per_100g.kcal")
                continue
            out["field"] = f.strip()
            out["direction"] = it.get("direction", "lower")
            if out["direction"] not in ("lower", "higher"):
                errors.append(f"{where}.direction: 'lower' or 'higher' (which values are better)")
            out["scale"] = it.get("scale", "linear")
            if out["scale"] not in ("linear", "log"):
                errors.append(f"{where}.scale: 'linear' or 'log'")
            try:
                out["best"], out["worst"] = _num(it.get("best")), _num(it.get("worst"))
                if out["best"] == out["worst"]:
                    raise ValueError("best and worst must differ")
                if out["scale"] == "log" and min(out["best"], out["worst"]) <= 0:
                    raise ValueError("a log scale needs positive best and worst")
            except ValueError as e:
                errors.append(f"{where}: best/worst anchors: {e}")
                continue
            out["unit"] = str(it.get("unit") or "")
        if not out["keywords"]:
            out["keywords"] = [w for w in re.findall(r"[a-z]{4,}", out["label"].lower())][:3]
        items.append(out)
    if not any(it["enabled"] for it in items):
        errors.append("criteria.items: at least one criterion must be enabled")
    elif sum(it["default_weight"] for it in items if it["enabled"]) <= 0:
        errors.append("criteria.items: the enabled default weights must not all be zero")
    if len(errors) > start:
        return None
    return {"taste_share": share, "items": items}


def _reason(r, where, crit_ids, errors, kind):
    if not isinstance(r, dict) or not r.get("id"):
        errors.append(f"{where}: each reason needs an id")
        return None
    rid = str(r["id"])
    if not re.match(r"^[a-z][a-z0-9_]{0,31}$", rid):
        errors.append(f"{where}: id {rid!r} must be lowercase letters/digits/_")
        return None
    out = {"id": rid, "label": str(r.get("label") or rid.replace("_", " ")), "criterion": r.get("criterion"),
           "show": bool(r.get("show", True))}
    if out["criterion"] is not None and out["criterion"] not in crit_ids:
        return "drop"  # its criterion is not enabled
    try:
        out["weight"] = _num(r.get("weight", 1.0), 0.0, 5.0)
        if kind == "choose":
            out["taste_bonus"] = _num(r.get("taste_bonus", 0.0), 0.0, 1.0)
        else:
            out["taste_strength"] = _num(r.get("taste_strength", 0.5), 0.0, 2.0)
    except ValueError as e:
        errors.append(f"{where}: {e}")
        return None
    if kind == "reject":
        out["effect"] = r.get("effect")
        if out["effect"] not in (None, "recency"):
            errors.append(f"{where}.effect: only 'recency' (or null)")
    # a reason may be lasting or "only today" unless it is about repetition
    out["ask_scope"] = bool(r.get("ask_scope", out.get("effect") != "recency"))
    return out


def _interactions(x, crit, errors):
    start = len(errors)
    if not isinstance(x, dict):
        errors.append("interactions: must be an object")
        return None
    ids = {c["id"] for c in crit["items"] if c["enabled"]}
    out = {"feedback_strength": {}, "choose_reasons": [], "reject_reasons": [], "learning": {}}
    fs = {**DEFAULT_INTERACTIONS["feedback_strength"], **(x.get("feedback_strength") or {})}
    for k in ("like", "save", "choose"):
        try:
            out["feedback_strength"][k] = _num(fs[k], 0.0, 3.0)
        except ValueError as e:
            errors.append(f"interactions.feedback_strength.{k}: {e}")
    unknown = set(fs) - {"like", "save", "choose"}
    if unknown:
        errors.append(f"interactions.feedback_strength: unknown type(s) {', '.join(sorted(unknown))}")
    for kind in ("choose", "reject"):
        seen = set()
        for i, r in enumerate(x.get(f"{kind}_reasons", DEFAULT_INTERACTIONS[f"{kind}_reasons"]) or [], 1):
            rr = _reason(r, f"interactions.{kind}_reasons[{i}]", ids, errors, kind)
            if rr in (None, "drop"):
                continue
            if rr["id"] in seen:
                errors.append(f"interactions.{kind}_reasons: duplicate id '{rr['id']}'")
                continue
            seen.add(rr["id"])
            out[f"{kind}_reasons"].append(rr)
    # every enabled criterion gets a choose and a reject reason (the supervisor asks about them)
    for c in crit["items"]:
        if not c["enabled"]:
            continue
        if not any(r["criterion"] == c["id"] for r in out["choose_reasons"]):
            out["choose_reasons"].append({"id": f"good_{c['id']}", "label": f"Good {c['label'].lower()}",
                                          "criterion": c["id"], "weight": 1.0, "taste_bonus": 0.0, "show": True,
                                          "ask_scope": True, "auto": True})
        if not any(r["criterion"] == c["id"] for r in out["reject_reasons"]):
            out["reject_reasons"].append({"id": f"poor_{c['id']}", "label": f"Poor {c['label'].lower()}",
                                          "criterion": c["id"], "weight": 1.0, "taste_strength": 0.5,
                                          "effect": None, "show": True, "ask_scope": True, "auto": True})
    lr = {**DEFAULT_INTERACTIONS["learning"], **(x.get("learning") or {})}
    for k, (lo, hi) in LEARNING_RANGES.items():
        try:
            out["learning"][k] = _num(lr[k], lo, hi)
        except ValueError as e:
            errors.append(f"interactions.learning.{k}: {e}")
    unknown = set(lr) - set(LEARNING_RANGES)
    if unknown:
        errors.append(f"interactions.learning: unknown parameter(s) {', '.join(sorted(unknown))}")
    if len(errors) > start:
        return None
    return out


def _config_file():
    p = os.environ.get("FOODREC_CONFIG")
    if p is None:
        return ROOT / "foodrec.config.json"
    if p.strip().lower() in ("", "none"):
        return None
    p = Path(p).expanduser()
    return p if p.is_absolute() else ROOT / p


def load():
    """(spec, errors, source). FOODREC_MODEL_SPEC (JSON with "criteria"/"interactions") wins
    over the config file; it is how scenario runs with other criteria start their subprocess."""
    raw, source = {}, "defaults"
    env = os.environ.get("FOODREC_MODEL_SPEC")
    if env:
        try:
            raw, source = json.loads(env), "FOODREC_MODEL_SPEC"
        except json.JSONDecodeError as e:
            return normalize()[0], [f"FOODREC_MODEL_SPEC is not valid JSON ({e})"], "defaults"
    else:
        path = _config_file()
        if path is not None and path.exists():
            try:
                raw, source = json.loads(path.read_text(encoding="utf-8")), str(path)
            except (OSError, json.JSONDecodeError):
                raw = {}  # appconfig reports the broken file
    spec, errors = normalize(raw.get("criteria"), raw.get("interactions"))
    for e in errors:
        log.warning("model spec: %s (using defaults for that section)", e)
    return spec, errors, source


def fingerprint(spec):
    return json.dumps(spec, sort_keys=True)


def extract(path, keys):
    """Read a dotted path like 'nutrition_per_100g.kcal' from a recipe."""
    cur = path
    for k in keys.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None
