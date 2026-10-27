"""Turn a chat message into a typed Intent.

Two parsers produce the same structure:
  rules  deterministic keyword/regex parser; always runs; the fallback.
  model  the supervisor LLM fills a JSON schema (Ollama structured output). Its output is
         validated field by field: unknown cuisines/ingredients/recipes are dropped and the
         drop is reported in the trace.
"""
import re

from .. import CRITERIA

INTENTS = {
    "recommend": "suggest recipes now (optionally with request filters such as few ingredients, high protein, a cuisine)",
    "explain_recipe": "why a specific recipe was recommended or how it scores",
    "compare_recipes": "compare two recipes",
    "what_if": "what would change if a priority were higher/lower (counterfactual, nothing saved)",
    "why_preference": "what the system believes about the user and why (provenance of a preference)",
    "history": "what the user did/was recommended in the past, filtered by time, food or ingredient",
    "knowledge": "a factual question about ingredients, recipes, nutrition values or how the system works",
    "update_profile": "add/remove an allergy, diet or disliked ingredient (needs the user's confirmation)",
    "review": "check whether the user's goal and behaviour are consistent",
    "help": "greeting or what the assistant can do",
}
PROFILE_OPS = ("add_allergen", "remove_allergen", "add_diet", "remove_diet", "add_dislike", "remove_dislike")
FILTER_KEYS = {"protein_min": ("protein_g", "min", 0, 150), "sugars_max": ("sugars_g", "max", 0, 200),
               "sugars_min": ("sugars_g", "min", 0, 200), "fibre_min": ("fibre_g", "min", 0, 60),
               "kcal_max": ("kcal", "max", 50, 3000), "max_ingredients": ("n_ingredients", "max", 2, 40),
               "co2e_max": ("co2e_kg", "max", 0.1, 60)}
MEALS = ("breakfast", "main", "side", "salad", "soup", "dessert", "snack", "drink", "sauce")
CRITERION_WORDS = {
    "p": ("taste", "tasty", "flavour", "flavor", "preference match", "like more"),
    "h": ("health", "healthy", "healthier", "nutritious", "nutrition", "nutri"),
    "s": ("sustainab", "climate", "co2", "carbon", "green", "environment", "planet", "footprint"),
    "t": ("convenien", "easy", "easier", "simple", "simpler", "fewer ingredients", "effort", "quick"),
}
SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "recipe_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "criterion": {"type": ["string", "null"], "enum": list(CRITERIA) + [None]},
        "direction": {"type": ["string", "null"], "enum": ["more", "less", None]},
        "filters": {"type": "object", "properties": {
            **{k: {"type": ["number", "null"]} for k in FILTER_KEYS},
            "cuisine": {"type": ["string", "null"]},
            "meal": {"type": ["string", "null"], "enum": list(MEALS) + [None]},
            "include_ingredient": {"type": ["string", "null"]},
            "exclude_ingredient": {"type": ["string", "null"]}}},
        "concept": {"type": ["string", "null"]},
        "time_window_days": {"type": ["integer", "null"]},
        "history_types": {"type": "array", "items": {"type": "string", "enum": ["like", "save", "choose", "reject"]}},
        "profile_change": {"type": ["object", "null"], "properties": {
            "op": {"type": "string", "enum": list(PROFILE_OPS)}, "value": {"type": "string"}}},
        "query": {"type": ["string", "null"]},
    },
    "required": ["intent"],
}


def empty_intent(intent="help"):
    return {"intent": intent, "recipe_refs": [], "criterion": None, "direction": None, "filters": {},
            "concept": None, "time_window_days": None, "history_types": [], "profile_change": None,
            "query": None}


# ---- rules ------------------------------------------------------------------------------
def _has(t, *words):
    return any(w in t for w in words)


def _criterion(t):
    best, pos = None, 10 ** 9
    for k, words in CRITERION_WORDS.items():
        for w in words:
            i = t.find(w)
            if 0 <= i < pos:
                best, pos = k, i
    return best


def recipe_mentions(text, catalogue):
    t = text.lower()
    refs = [f"#{m}" for m in re.findall(r"(?:#|number |no\.? ?|rank )([1-5])\b", t)]
    ordinals = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "top one": 1, "last one": 5}
    refs += [f"#{v}" for w, v in ordinals.items() if re.search(rf"\b(the )?{w}\b( one| recipe| dish)?", t)
             and not re.search(rf"{w} (time|week|day)", t)]
    words = set(re.findall(r"[a-z]+", t))
    for r in catalogue.recipes:
        tw = [w for w in re.findall(r"[a-z]+", r["title"].lower()) if len(w) > 2 and w not in ("and", "with", "the")]
        if tw and sum(w in words for w in tw) / len(tw) >= 0.6:
            refs.append(r["id"])
    return list(dict.fromkeys(refs))


def _window(t):
    m = re.search(r"last (\d+) days?", t)
    if m:
        return int(m.group(1))
    for words, d in ((("today",), 1), (("yesterday",), 2), (("this week", "last week", "past week"), 7),
                     (("this month", "last month", "past month"), 30), (("this year", "last year"), 365)):
        if _has(t, *words):
            return d
    return None


def _filters(t, vocab):
    f = {}
    if re.search(r"(high|more|lots of|rich in|extra)[ -]protein|protein[- ](rich|packed)", t):
        f["protein_min"] = 25
    if re.search(r"(low|less|little)[ -]sugar|sugar[- ]free", t):
        f["sugars_max"] = 8
    elif re.search(r"(more|high|extra)[ -]sugar|sweet", t):
        f["sugars_min"] = 10
    if re.search(r"(high|more|rich in)[ -]fib(re|er)", t):
        f["fibre_min"] = 8
    if re.search(r"low[ -]cal|light meal|few calories|under \d+ ?kcal", t):
        m = re.search(r"under (\d+) ?kcal", t)
        f["kcal_max"] = int(m.group(1)) if m else 550
    m = re.search(r"(?:under|less than|at most|max(?:imum)?|only|just) (\d+) ingredients?", t)
    if m:
        f["max_ingredients"] = int(m.group(1))
    elif _has(t, "few ingredients", "fewer ingredients", "quick", "in a hurry", "simple meal", "easy meal"):
        f["max_ingredients"] = 7
    if re.search(r"low[ -](carbon|co2|footprint|emission)", t):
        f["co2e_max"] = 2.0
    for c in vocab["cuisines"]:
        if re.search(rf"\b{re.escape(c.lower())}\b", t):
            f["cuisine"] = c
    for meal, words in (("breakfast", ("breakfast", "brunch")), ("dessert", ("dessert", "something sweet")),
                        ("soup", ("soup",)), ("salad", ("salad",)), ("snack", ("snack",)), ("drink", ("drink", "smoothie"))):
        if _has(t, *words):
            f["meal"] = meal
            break
    for ing in sorted(vocab["ingredients"], key=len, reverse=True):
        if re.search(rf"\b(without|no|not|skip|avoid) {re.escape(ing)}\b", t):
            f["exclude_ingredient"] = ing
            break
        if re.search(rf"\bwith {re.escape(ing)}\b", t):
            f["include_ingredient"] = ing
            break
    return f


def _profile_change(t, vocab):
    for a in vocab["allergens"]:
        stem = a.rstrip("s")
        if re.search(rf"allerg\w* (to )?{stem}", t) or re.search(rf"{stem}\w* allerg", t):
            op = "remove_allergen" if _has(t, "not allergic", "no longer", "remove", "anymore") else "add_allergen"
            return {"op": op, "value": a}
    for d in vocab["diets"]:
        name = d.replace("_", " ")
        if re.search(rf"\b(i am|i'm|im|became|going|now|switch(ed)? to|make me|set me to) (a )?{name}", t):
            return {"op": "add_diet", "value": d}
        if re.search(rf"(no longer|not|stop being|stopped being) (a )?{name}", t):
            return {"op": "remove_diet", "value": d}
    for ing in sorted(vocab["ingredients"], key=len, reverse=True):
        if re.search(rf"(never (give|show|recommend) me|i (hate|can't stand|don't eat|do not eat)|exclude) {re.escape(ing)}", t):
            return {"op": "add_dislike", "value": ing}
    return None


def parse_rules(text, catalogue, vocab):
    t = " " + text.lower().strip() + " "
    it = empty_intent()
    it["recipe_refs"] = recipe_mentions(text, catalogue)
    it["criterion"] = _criterion(t)
    it["direction"] = "less" if _has(t, " less ", "lower", "reduce", "don't care", "not care") else \
        ("more" if it["criterion"] else None)
    it["time_window_days"] = _window(t)
    it["filters"] = _filters(t, vocab)
    pc = _profile_change(t, vocab)
    for ing in vocab["ingredients"]:
        if re.search(rf"\b{re.escape(ing)}\b", t):
            it["concept"] = f"ingredient:{ing}"
            break
    for c in vocab["cuisines"]:
        if re.search(rf"\b{re.escape(c.lower())}\b", t) and not it["concept"]:
            it["concept"] = f"cuisine:{c}"
    if pc:
        it["intent"], it["profile_change"] = "update_profile", pc
    elif _has(t, "compare", " vs ", "versus", "difference between"):
        it["intent"] = "compare_recipes"
    elif _has(t, "what if", "what would happen", "what would change", "if i cared", "if i gave", "if i prioriti"):
        it["intent"] = "what_if"
    elif _has(t, "why do you think", "why do you believe", "what do you know about me", "my preferences",
              "what do you think i", "how do you know i", "what have you learned"):
        it["intent"] = "why_preference"
    elif _has(t, "conflict", "consistent", "check my goal", "review my", "am i acting"):
        it["intent"] = "review"
    elif _has(t, "what did i", "did i ", "have i ", "history", "my past", "i rejected", "i liked", "i chose",
              "recommended to me", "you recommended") or (it["time_window_days"] and _has(t, " i ")):
        it["intent"] = "history"
        it["history_types"] = [x for x, w in (("like", "like"), ("save", "save"), ("choose", "chose"),
                                              ("reject", "reject")) if w in t]
    elif (it["recipe_refs"] and _has(t, "why", "explain", "how does", "score", "tell me about", "details")):
        it["intent"] = "explain_recipe"
    elif _has(t, "recommend", "suggest", "what should i eat", "what to eat", "dinner", "lunch", "breakfast",
              "hungry", "meal", "cook tonight", "something", "ideas") or it["filters"]:
        it["intent"] = "recommend"
    elif _has(t, "?", "how ", "what ", "which ", "does ", "is ", "nutri", "contain"):
        it["intent"] = "knowledge"
    elif _has(t, "help", "hello", "hi ", "hey", "what can you"):
        it["intent"] = "help"
    else:
        it["intent"] = "recommend" if it["recipe_refs"] == [] and len(t.split()) <= 4 else "knowledge"
    if it["intent"] == "knowledge":
        it["query"] = text.strip()
    return it


# ---- model -------------------------------------------------------------------------------
def model_prompt(text, context, vocab):
    intents = "\n".join(f"- {k}: {v}" for k, v in INTENTS.items())
    return [
        {"role": "system", "content": (
            "You classify one message sent to a food recommendation assistant and extract fields. "
            "Return JSON only, matching the schema. Use null when a field is not mentioned. Never invent "
            "values. Criteria codes: p=taste match, h=healthiness, s=sustainability (CO2 footprint), "
            "t=convenience (few ingredients).\nIntents:\n" + intents + "\n"
            "recipe_refs: recipe titles exactly as written by the user, or '#n' for 'number n' in the "
            "current list. filters only for this request (protein_min grams per serving, max_ingredients, "
            "kcal_max per serving, co2e_max kg CO2e per kg, cuisine, meal, include_ingredient, exclude_ingredient). "
            "profile_change only when the user states an allergy, diet or a food to never include.\n"
            f"Known cuisines: {', '.join(vocab['cuisines'])}.")},
        {"role": "user", "content": f"Current list: {context or 'none'}\nMessage: {text}"},
    ]


def validate_model(data, text, catalogue, vocab):
    """Coerce model JSON into an Intent; return (intent, list of dropped/corrected fields)."""
    notes = []
    it = empty_intent(data.get("intent") if data.get("intent") in INTENTS else "help")
    if data.get("intent") not in INTENTS:
        notes.append(f"unknown intent {data.get('intent')!r}")
    refs = []
    for r in data.get("recipe_refs") or []:
        r = str(r).strip()
        if re.fullmatch(r"#[1-5]", r):
            refs.append(r)
            continue
        found = recipe_mentions(r, catalogue)
        if found:
            refs.extend(found)
        else:
            notes.append(f"dropped unknown recipe {r!r}")
    it["recipe_refs"] = list(dict.fromkeys(refs))
    it["criterion"] = data.get("criterion") if data.get("criterion") in CRITERIA else None
    it["direction"] = data.get("direction") if data.get("direction") in ("more", "less") else None
    f = {}
    for k, v in (data.get("filters") or {}).items():
        if v is None:
            continue
        if k in FILTER_KEYS:
            lo, hi = FILTER_KEYS[k][2:]
            try:
                v = float(v)
            except (TypeError, ValueError):
                notes.append(f"dropped non-numeric filter {k}")
                continue
            if lo <= v <= hi:
                f[k] = v
            else:
                notes.append(f"dropped out-of-range filter {k}={v}")
        elif k == "cuisine":
            m = next((c for c in vocab["cuisines"] if c.lower() == str(v).lower()), None)
            if m:
                f[k] = m
            else:
                notes.append(f"dropped unknown cuisine {v!r}")
        elif k == "meal" and v in MEALS:
            f[k] = v
        elif k in ("include_ingredient", "exclude_ingredient"):
            if str(v).lower() in vocab["ingredients"]:
                f[k] = str(v).lower()
            else:
                notes.append(f"dropped unknown ingredient {v!r}")
    it["filters"] = f
    c = data.get("concept")
    if c:
        c = str(c).lower().strip()
        kind, _, name = c.partition(":") if ":" in c else ("", "", c)
        if name in vocab["ingredients"]:
            it["concept"] = f"ingredient:{name}"
        else:
            cu = next((x for x in vocab["cuisines"] if x.lower() == name), None)
            it["concept"] = f"cuisine:{cu}" if cu else None
            if not cu:
                notes.append(f"dropped unknown concept {c!r}")
    tw = data.get("time_window_days")
    it["time_window_days"] = int(tw) if isinstance(tw, (int, float)) and 0 < tw <= 3650 else None
    it["history_types"] = [h for h in data.get("history_types") or [] if h in ("like", "save", "choose", "reject")]
    pc = data.get("profile_change")
    if pc and pc.get("op") in PROFILE_OPS and pc.get("value"):
        val = str(pc["value"]).lower().strip().replace(" ", "_" if "diet" in pc["op"] else " ")
        pool = vocab["allergens"] if "allergen" in pc["op"] else vocab["diets"] if "diet" in pc["op"] else vocab["ingredients"]
        val = next((p for p in pool if p == val or p.rstrip("s") == val.rstrip("s")), None)
        if val:
            it["profile_change"] = {"op": pc["op"], "value": val}
        else:
            notes.append(f"dropped unsupported profile change {pc}")
    if it["intent"] == "update_profile" and not it["profile_change"]:
        notes.append("update_profile without a valid change; treated as help")
        it["intent"] = "help"
    it["query"] = data.get("query") or (text.strip() if it["intent"] == "knowledge" else None)
    return it, notes


def filters_to_request(f):
    """Intent filters -> engine request filters (constraints.REQUEST_FIELDS)."""
    out = []
    for k, (field, op, _, _) in FILTER_KEYS.items():
        if f.get(k) is not None:
            v = f[k]
            out.append({"field": field, "op": op, "value": int(v) if float(v).is_integer() else v})
    if f.get("cuisine"):
        out.append({"field": "cuisine", "op": "is", "value": f["cuisine"]})
    if f.get("meal"):
        out.append({"field": "meal", "op": "is", "value": f["meal"]})
    if f.get("include_ingredient"):
        out.append({"field": "ingredient", "op": "has", "value": f["include_ingredient"]})
    if f.get("exclude_ingredient"):
        out.append({"field": "ingredient", "op": "lacks", "value": f["exclude_ingredient"]})
    return out
