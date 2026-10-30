"""Label ingredients and recipes with the local supervisor model (Qwen via Ollama).

Two labelling jobs, both batched and constrained by a JSON schema with fixed options:
  ingredients  each unique ingredient name -> one food group key (foodgroups.GROUPS)
  recipes      each recipe title (+ main ingredients) -> cuisine and meal type
Every answer is validated; an invalid or missing label falls back to the keyword rules
and is marked source="rules". Labels are cached in a JSON file after every batch, so a run
can be stopped and resumed, and the cache can be copied to another device to skip the
model entirely.
"""
import json
import logging
import time
from pathlib import Path

from .foodgroups import GROUPS, UNCLASSIFIED, classify_rule
from .parse import CUISINES, MEALS, cuisine_rule, meal_rule

log = logging.getLogger("foodrec.ingest")
GROUP_KEYS = list(GROUPS) + [UNCLASSIFIED]
GROUP_HELP = "; ".join(f"{k}={v[0]}" for k, v in GROUPS.items())


class LabelCache:
    def __init__(self, path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"ingredients": {}, "recipes": {}}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=0, sort_keys=True))
        tmp.replace(self.path)


def _batches(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _ask(llm, system, lines, item_schema, max_tokens):
    schema = {"type": "object", "properties": {"items": {"type": "array", "items": item_schema}},
              "required": ["items"]}
    out = llm.chat([{"role": "system", "content": system},
                    {"role": "user", "content": "\n".join(f"{i}. {x}" for i, x in enumerate(lines))}],
                   schema=schema, think=False, max_tokens=max_tokens)
    return (out.get("data") or {}).get("items") or []


def label_ingredients(names, cache, llm=None, batch=40, progress=None):
    """names: unique cleaned ingredient names. Fills cache.data['ingredients']."""
    store = cache.data["ingredients"]
    todo = sorted(n for n in names if n not in store)
    system = ("Assign each numbered food ingredient to exactly one food group. Return JSON "
              "{items:[{i, group}]}, one entry per line, using only these group keys: " + GROUP_HELP +
              f"; {UNCLASSIFIED}=not a food or impossible to tell. Choose by what the ingredient mostly is: "
              "'peanut butter'=peanuts, 'chicken broth'=broth, 'eggplant'=vegetables, 'garlic powder'=spices, "
              "'coconut milk'=fruit, 'cream cheese'=cheese.")
    item = {"type": "object", "properties": {"i": {"type": "integer"}, "group": {"type": "string", "enum": GROUP_KEYS}},
            "required": ["i", "group"]}
    done = 0
    for chunk in _batches(todo, batch):
        got = {}
        if llm is not None:
            try:
                for row in _ask(llm, system, chunk, item, 30 * len(chunk) + 50):
                    i, g = row.get("i"), row.get("group")
                    if isinstance(i, int) and 0 <= i < len(chunk) and g in GROUP_KEYS:
                        got[i] = g
            except Exception as e:  # noqa: BLE001 - any model failure falls back to rules
                log.warning("model batch failed (%s); using rules for %d names", e, len(chunk))
        for i, name in enumerate(chunk):
            rule = classify_rule(name)
            if i in got:
                store[name] = {"group": got[i], "source": "model", "rule": rule}
            else:
                store[name] = {"group": rule, "source": "rules", "rule": rule}
        done += len(chunk)
        cache.save()
        if progress:
            progress("ingredients", done, len(todo))


def label_recipes(rows, cache, llm=None, batch=25, progress=None):
    """rows: [(recipe_id, title, [ingredient names])]. Fills cache.data['recipes']."""
    store = cache.data["recipes"]
    todo = [r for r in rows if str(r[0]) not in store]
    system = ("For each numbered recipe give its cuisine and meal type. Return JSON {items:[{i, cuisine, meal}]}, "
              f"one entry per line. cuisine is one of: {', '.join(CUISINES)}. meal is one of: {', '.join(MEALS)} "
              "(main = a main course; sauce = sauces, dressings, dips, spreads, seasonings; non_food = not "
              "meant for people to eat, e.g. cleaners, soap, pet food, play dough, cosmetics).")
    item = {"type": "object", "properties": {"i": {"type": "integer"}, "cuisine": {"type": "string", "enum": CUISINES},
                                             "meal": {"type": "string", "enum": MEALS}},
            "required": ["i", "cuisine", "meal"]}
    done = 0
    for chunk in _batches(todo, batch):
        lines = [f"{t} | {', '.join(ings[:6])}" for _, t, ings in chunk]
        got = {}
        if llm is not None:
            try:
                for row in _ask(llm, system, lines, item, 40 * len(chunk) + 50):
                    i = row.get("i")
                    if isinstance(i, int) and 0 <= i < len(chunk) and row.get("cuisine") in CUISINES and row.get("meal") in MEALS:
                        got[i] = row
            except Exception as e:  # noqa: BLE001
                log.warning("model batch failed (%s); using rules for %d recipes", e, len(chunk))
        for i, (rid, title, ings) in enumerate(chunk):
            if i in got:
                store[str(rid)] = {"cuisine": got[i]["cuisine"], "meal": got[i]["meal"], "source": "model"}
            else:
                store[str(rid)] = {"cuisine": cuisine_rule(title, ings), "meal": meal_rule(title), "source": "rules"}
        done += len(chunk)
        cache.save()
        if progress:
            progress("recipes", done, len(todo))


def printer(stage, done, total, _t0=[time.time()]):
    if total:
        el = time.time() - _t0[0]
        print(f"\r  {stage}: {done}/{total} ({100 * done / total:.0f}%, {el:.0f}s)", end="", flush=True)
        if done >= total:
            print()
