"""Builds the retrievable corpus: recipes, food groups, methodology notes and user documents.

Recipe and food-group passages are generated from the catalogue, so every number in them is a
catalogue value. Methodology notes live in data/knowledge/*.md and describe how the system
computes things; they contain no health advice. User documents are whatever the user added
and are labelled as such in every answer that cites them.
"""
import re
from collections import Counter
from pathlib import Path

from .. import CRITERION_SPECS
from ..constraints import per_serving


def _fmt(v, unit="", nd=1):
    return "unknown" if v is None else f"{round(v, nd)}{unit}"


def recipe_chunks(catalogue):
    out = []
    for r in catalogue.recipes:
        n = r.get("nutrition_per_100g") or {}
        fp = r.get("footprint") or {}
        top = ", ".join(f"{b['name']} {round(100 * b['share'])}%" for b in fp.get("breakdown", [])[:3] if b["share"] >= 0.05)
        diets = [d.replace("_", " ") for d, ok in (r.get("diet") or {}).items() if ok]
        text = (f"{r['title']} is a {r['cuisine']} {r.get('meal', 'main')} dish with {r.get('n_ingredients')} "
                f"ingredients: {', '.join(i['name'] for i in r['ingredients'])}. "
                f"Per 100 g: {_fmt(n.get('kcal'), ' kcal', 0)}, fat {_fmt(n.get('fat_g'), ' g')}, "
                f"saturated fat {_fmt(n.get('sat_fat_g'), ' g')}, sugars {_fmt(n.get('sugars_g'), ' g')}, "
                f"sodium {_fmt(n.get('sodium_mg'), ' mg', 0)}, fibre {_fmt(n.get('fibre_g'), ' g')}, "
                f"protein {_fmt(n.get('protein_g'), ' g')}. FSA score {_fmt(r.get('fsa_score'), '', 2)}. "
                f"Per serving: {_fmt(r.get('kcal_per_serving'), ' kcal', 0)}, protein "
                f"{_fmt(per_serving(r, 'protein_g'), ' g')}. "
                f"Estimated footprint {_fmt(fp.get('kg_co2e_per_kg'), ' kg CO2e per kg', 2)}"
                f"{' (mostly ' + top + ')' if top else ''}. "
                f"Suitable for: {', '.join(diets) or 'no listed diet'}. "
                f"Allergens: {', '.join(r.get('allergens') or []) or 'none detected'}"
                f"{'' if r.get('allergens_complete') else ' (allergen data incomplete)'}.")
        out.append({"id": f"recipe:{r['id']}", "doc_id": f"recipe:{r['id']}", "kind": "recipe",
                    "title": r["title"], "text": text, "meta": {"recipe_id": r["id"], "cuisine": r["cuisine"]}})
    return out


def group_chunks(catalogue):
    """One passage per food group: its CO2e factor, source and the commonest ingredients."""
    names = {}
    for r in catalogue.recipes:
        for i in r["ingredients"]:
            names.setdefault(i.get("category"), Counter())[i["name"]] += 1
    out = []
    for key, g in sorted((catalogue.groups or {}).items()):
        common = ", ".join(n for n, _ in names.get(key, Counter()).most_common(12))
        text = (f"Food group {g.get('label', key)} ({key}): {g.get('co2e')} kg CO2e per kg "
                f"({g.get('source')}). Allergens: {', '.join(g.get('allergens') or []) or 'none'}. "
                f"Common ingredients in the catalogue: {common or 'none'}.")
        out.append({"id": f"group:{key}", "doc_id": f"group:{key}", "kind": "ingredient",
                    "title": g.get("label", key), "text": text, "meta": {"group": key}})
    return out


def split_markdown(text, max_chars=900):
    """Split on headings, then pack paragraphs up to max_chars."""
    sections, title, buf = [], None, []
    for line in text.splitlines():
        m = re.match(r"^#{1,3}\s+(.*)", line)
        if m:
            if buf:
                sections.append((title, "\n".join(buf).strip()))
            title, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if buf:
        sections.append((title, "\n".join(buf).strip()))
    out = []
    for t, body in sections:
        paras, cur = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()], ""
        for p in paras:
            if cur and len(cur) + len(p) > max_chars:
                out.append((t, cur))
                cur = ""
            cur = (cur + "\n\n" + p).strip()
        if cur:
            out.append((t, cur))
    return out


def text_chunks(doc_id, title, text, kind):
    out = []
    for j, (section, body) in enumerate(split_markdown(text)):
        out.append({"id": f"{doc_id}#{j}", "doc_id": doc_id, "kind": kind,
                    "title": f"{title} - {section}" if section and section != title else title,
                    "text": body, "meta": {"section": section}})
    return out


def methodology_chunks(knowledge_dir):
    out = []
    for p in sorted(Path(knowledge_dir).glob("*.md")):
        text = p.read_text()
        m = re.match(r"^#\s+(.*)", text)
        out.extend(text_chunks(f"method:{p.stem}", m.group(1).strip() if m else p.stem, text, "method"))
    return out


def criterion_chunks():
    """A method note for every configured field criterion, so "how is X scored?" finds it."""
    out = []
    for k, c in CRITERION_SPECS.items():
        if c["kind"] != "field":
            continue
        better = "Lower" if c["direction"] == "lower" else "Higher"
        text = (f"# How {c['label'].lower()} is scored\n\n{c['description']}\n\n"
                f"{c['label']} ({k}) is scored from the recipe field {c['field']}"
                + (f" in {c['unit']}" if c.get("unit") else "") + f". {better} values are better: "
                f"{c['best']} or better scores 1, {c['worst']} or worse scores 0, {c['scale']} in between. "
                "Recipes without this value are handled by the missing-value policy.")
        out.extend(text_chunks(f"method:criterion_{k}", f"How {c['label'].lower()} is scored", text, "method"))
    return out


def build_corpus(catalogue, knowledge_dir, user_docs):
    chunks = recipe_chunks(catalogue) + group_chunks(catalogue) + methodology_chunks(knowledge_dir) + criterion_chunks()
    for d in user_docs:
        chunks.extend(text_chunks(d["doc_id"], d["title"], d["text"], "user"))
    return chunks
