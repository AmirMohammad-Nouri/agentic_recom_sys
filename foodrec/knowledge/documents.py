"""Builds the retrievable corpus: recipes, ingredients, methodology notes and user documents.

Recipe and ingredient passages are generated from the frozen catalogue, so every number in
them is a catalogue value. Methodology notes live in data/knowledge/*.md and describe how
the system computes things; they contain no health advice. User documents are whatever the
user added and are labelled as such in every answer that cites them.
"""
import re
from pathlib import Path

from ..constraints import per_serving
from ..features import nutriscore


def _fmt(v, unit="", nd=1):
    return "unknown" if v is None else f"{round(v, nd)}{unit}"


def recipe_chunks(catalogue):
    out = []
    for r in catalogue.recipes:
        n = r.get("nutrition_per_100g") or {}
        ns = nutriscore(n)
        diets = [d.replace("_", " ") for d, ok in (r.get("diet") or {}).items() if ok]
        text = (f"{r['title']} is a {r['cuisine']} {r.get('meal', 'main')} dish. "
                f"Ingredients: {', '.join(i['name'] + ' ' + str(i['grams']) + ' g' for i in r['ingredients'])}. "
                f"Preparation {_fmt(r.get('prep_minutes'), ' min', 0)}. "
                f"Per serving ({r.get('serving_grams')} g): {r.get('kcal_per_serving')} kcal, "
                f"protein {_fmt(per_serving(r, 'protein_g'), ' g')}, sugars {_fmt(per_serving(r, 'sugars_g'), ' g')}, "
                f"fibre {_fmt(per_serving(r, 'fibre_g'), ' g')}. "
                f"Nutri-Score grade {ns['grade'] if ns else 'unknown'}. "
                f"Cost {_fmt(r.get('cost_eur_per_serving'), ' EUR', 2)} per serving, "
                f"footprint {_fmt(r.get('co2e_kg_per_serving'), ' kg CO2e', 2)} per serving (demo placeholder values). "
                f"Suitable for: {', '.join(diets) or 'no listed diet'}. "
                f"Allergens: {', '.join(r.get('allergens') or []) or 'none listed'}"
                f"{'' if r.get('allergens_complete') else ' (allergen data incomplete)'}.")
        out.append({"id": f"recipe:{r['id']}", "doc_id": f"recipe:{r['id']}", "kind": "recipe",
                    "title": r["title"], "text": text, "meta": {"recipe_id": r["id"], "cuisine": r["cuisine"]}})
    return out


def ingredient_chunks(ingredients):
    out = []
    for name, i in sorted(ingredients.items()):
        text = (f"{name} ({i.get('category')}). Per 100 g: {i.get('kcal')} kcal, protein {i.get('protein_g')} g, "
                f"sugars {i.get('sugars_g')} g, fibre {i.get('fibre_g')} g, saturated fat {i.get('sat_fat_g')} g, "
                f"sodium {i.get('sodium_mg')} mg. Allergens: "
                f"{'unknown' if i.get('allergens') is None else (', '.join(i['allergens']) or 'none')}. "
                f"Demo placeholder price {i.get('price_eur_per_kg')} EUR/kg and footprint "
                f"{i.get('co2e_kg_per_kg')} kg CO2e/kg.")
        out.append({"id": f"ingredient:{name}", "doc_id": f"ingredient:{name}", "kind": "ingredient",
                    "title": name, "text": text, "meta": {"name": name}})
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


def build_corpus(catalogue, ingredients, knowledge_dir, user_docs):
    chunks = recipe_chunks(catalogue) + ingredient_chunks(ingredients) + methodology_chunks(knowledge_dir)
    for d in user_docs:
        chunks.extend(text_chunks(d["doc_id"], d["title"], d["text"], "user"))
    return chunks
