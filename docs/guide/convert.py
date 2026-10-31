"""Regenerate the Markdown version of the guide from the interactive HTML guide.

    pip install beautifulsoup4            (only needed for this script)
    python docs/guide/convert.py          (defaults: docs/guide/index.html -> docs/guide/guide.md)

GitHub shows .html files as source code, so the repository carries a Markdown copy that GitHub
renders: text, tables and LaTeX math (inline and block math), the SVG figures as standalone
files in docs/guide/figures/ (styles written as SVG attributes, so every viewer shows them), and
the interactive simulator as a worked example computed with the same arithmetic. Edit
index.html, then run this script.
"""
import math
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "index.html"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "guide.md"
FIG = OUT.parent / "figures"
FIG.mkdir(parents=True, exist_ok=True)

html = SRC.read_text(encoding="utf-8")
soup = BeautifulSoup(html, "html.parser")
css = re.findall(r"<style>(.*?)</style>", html, re.S)[1]

# ---- standalone SVG styles: the page's colour variables (light and dark) + the svg rules ----------
root_light = re.search(r":root\s*\{([^}]*)\}", css).group(1)
dark = re.search(r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root:not\(\[data-theme=\"light\"\]\)\s*\{([^}]*)\}", css)
svg_rules = "\n".join(r.strip() for r in re.findall(r"[^{}]+\{[^{}]*\}", css) if r.strip().startswith("svg "))
svg_rules = re.sub(r"(?m)^svg ", "", svg_rules)  # inside the file the root is the svg itself
SVG_STYLE = (":root{" + root_light + "}\n"
             + ("@media (prefers-color-scheme: dark){:root{" + dark.group(1) + "}}\n" if dark else "")
             + ":root{color:var(--ink);background:var(--surface);font-family:system-ui,-apple-system,'Segoe UI',sans-serif}\n"
             + svg_rules.replace("var(--body)", "system-ui,sans-serif").replace("var(--mono)", "ui-monospace,monospace"))

fig_no = 0
VARS = {}
for k, v in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", css):  # the light theme is defined first
    VARS.setdefault(k, v)


def resolve_vars(text):
    """Replace var(--x) by its light-theme value, so every SVG renderer shows the same colours."""
    text = re.sub(r"@media \(prefers-color-scheme: dark\)\{:root\{[^}]*\}\}", "", text)
    for _ in range(3):
        text = re.sub(r"var\((--[a-z0-9-]+)\)", lambda m: VARS.get(m.group(1), "#888").strip(), text)
    return text.replace("currentColor", VARS["--ink-2"].strip())


# class rules of the svg elements, e.g. ".node.agent" -> {"fill": ..., "stroke": ...}
RULES = []
for sel, body in re.findall(r"svg\s+([.\w-]+)\s*\{([^}]*)\}", css):
    props = {}
    for decl in body.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            props[k.strip()] = v.strip()
    RULES.append((set(c for c in sel.split(".") if c), props))
FONT = {"var(--body)": "system-ui, -apple-system, Segoe UI, sans-serif", "var(--mono)": "ui-monospace, Menlo, monospace"}


def presentation(props):
    """CSS declarations -> SVG presentation attributes (understood by every renderer)."""
    out = {}
    for k, v in props.items():
        if k == "font":
            m = re.match(r"(\d{3})?\s*([\d.]+px)\s+(.+)", v)
            if m:
                if m.group(1):
                    out["font-weight"] = m.group(1)
                out["font-size"] = m.group(2)
                out["font-family"] = FONT.get(m.group(3).strip(), m.group(3).strip())
        elif k in ("fill", "stroke", "stroke-width", "stroke-dasharray", "opacity", "font-size", "font-weight"):
            out[k] = v
    return out


def inline_styles(svg):
    for el in svg.find_all(True):
        cls = set(el.get("class") or [])
        if not cls:
            continue
        merged = {}
        for want, props in RULES:  # later and more specific rules win, as in CSS
            if want <= cls:
                merged.update(props)
        for k, v in presentation(merged).items():
            el[k] = v
        del el["class"]


def save_svg(svg, label):
    global fig_no
    fig_no += 1
    svg["xmlns"] = "http://www.w3.org/2000/svg"
    inline_styles(svg)
    svg["color"] = "var(--ink-2)"
    svg["font-family"] = FONT["var(--body)"]
    vb = [float(x) for x in svg.get("viewBox", "0 0 800 400").split()]
    bg = soup.new_tag("rect", x=str(vb[0]), y=str(vb[1]), width=str(vb[2]), height=str(vb[3]), fill="var(--surface)")
    svg.insert(0, bg)
    vb = [float(x) for x in svg.get("viewBox", "0 0 800 400").split()]
    svg["width"], svg["height"] = str(int(vb[2])), str(int(vb[3]))
    name = f"figure-{fig_no}.svg"
    (FIG / name).write_text(resolve_vars(str(svg)), encoding="utf-8")
    return name


# ---- inline conversion -------------------------------------------------------------------------
def math_inline(t):
    t = re.sub(r"\\\((.+?)\\\)", lambda m: "$`" + m.group(1).strip() + "`$", t, flags=re.S)
    return t


def inline(node):
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node))
    if not isinstance(node, Tag):
        return ""
    name = node.name
    if name in ("script", "style", "button", "select", "svg"):
        return ""
    inner = "".join(inline(c) for c in node.children)
    if name in ("b", "strong"):
        return f"**{inner.strip()}**" if inner.strip() else ""
    if name in ("i", "em"):
        return f"*{inner.strip()}*" if inner.strip() else ""
    if name == "code":
        return f"`{node.get_text()}`"
    if name in ("sub", "sup"):
        return f"<{name}>{inner}</{name}>"
    if name == "br":
        return "<br>"
    cls = node.get("class") or []
    if "pill" in cls:
        return f"`{inner.strip()}`"
    if "no" in cls:
        return f"{inner.strip()} · "
    if name == "a":
        href = node.get("href", "")
        return f"[{inner.strip()}]({href})" if href else inner
    return inner


def text_of(node):
    return math_inline(inline(node)).strip()


def cell(node):
    return text_of(node).replace("|", "\\|").replace("\n", " ")


# ---- block conversion --------------------------------------------------------------------------
out = []


def emit(s=""):
    out.append(s)


def display_math(t):
    """Paragraph text that is (or contains) display math \\[ ... \\] -> ```math blocks."""
    parts = re.split(r"\\\[(.+?)\\\]", t, flags=re.S)
    for i, p in enumerate(parts):
        if i % 2:
            emit("```math")
            emit(p.strip())
            emit("```")
            emit()
        elif p.strip():
            emit(math_inline(p.strip()))
            emit()


def table(t):
    rows = t.find_all("tr")
    if not rows:
        return
    head = rows[0]
    hcells = head.find_all(["th", "td"])
    emit("| " + " | ".join(cell(c) for c in hcells) + " |")
    emit("|" + "---|" * len(hcells))
    for r in rows[1:]:
        cs = r.find_all(["th", "td"])
        emit("| " + " | ".join(cell(c) for c in cs) + " |")
    emit()


TOC = []


def block(node, depth=0):
    if isinstance(node, Comment):
        return
    if isinstance(node, NavigableString):
        t = str(node).strip()
        if t:
            display_math(re.sub(r"\s+", " ", t))
        return
    if not isinstance(node, Tag):
        return
    name, cls = node.name, node.get("class") or []
    if name in ("script", "style", "nav", "button", "link", "meta", "title"):
        return
    if "sim" in cls:
        emit(SIM_MD)
        return
    if node.get("id") and name not in ("svg", "b", "span", "div") or (name == "div" and "card" in cls and node.get("id")):
        emit(f'<a name="{node["id"]}"></a>')
        emit()
    if "key" in cls:
        emit("*Colours in the figures:* " + " · ".join(sp.get_text(strip=True) for sp in node.find_all("span")))
        emit()
        return
    if "cols" in cls or "ladder" in cls:
        for item in node.find_all("div", recursive=False):
            head = item.find(["b", "span"])
            rest = "".join(inline(c) for c in item.children if c is not head)
            emit(f"- **{head.get_text(strip=True)}**: {math_inline(rest).strip()}")
        emit()
        return
    if "formula" in cls:
        emit("```text")
        emit(node.get_text(" ", strip=True))
        emit("```")
        emit()
        return
    if "bars" in cls:
        emit("| " + (node.get("aria-label") or "Weights") + " | |")
        emit("|---|---|")
        for row in node.find_all("div", class_="bar-row"):
            lab, v = row.find("span", class_="lab"), row.find("span", class_="v")
            emit(f"| {lab.get_text(strip=True) if lab else ''} | {v.get_text(strip=True) if v else ''} |")
        emit()
        return
    if "card-head" in cls:
        h = node.find("h3")
        src = node.find("span", class_="src")
        title = text_of(h) if h else ""
        emit(f"#### {title}")
        TOC.append((4, title, node.parent.get("id")))
        emit()
        if src:
            emit(f"<sub>Code: `{src.get_text(strip=True)}`</sub>")
            emit()
        return
    if "part" in cls:
        h = node.find("h2")
        span = node.find("span")
        emit(f'<a name="{node.get("id")}"></a>')
        emit()
        emit(f"## {span.get_text(strip=True)} · {h.get_text(strip=True)}")
        TOC.append((2, f"{span.get_text(strip=True)} · {h.get_text(strip=True)}", node.get("id")))
        p = node.find("p")
        if p:
            emit()
            emit(f"*{text_of(p)}*")
        emit()
        return
    if "eyebrow" in cls:
        emit(f"<sub>{text_of(node)}</sub>")
        emit()
        return
    if name == "h1":
        emit(f"# {text_of(node)}")
        emit()
        return
    if name == "h2":
        emit(f"### {text_of(node)}")
        sec = node.find_parent("section")
        TOC.append((3, text_of(node), sec.get("id") if sec else None))
        emit()
        return
    if name == "h3":
        emit(f"#### {text_of(node)}")
        emit()
        return
    if name == "p":
        raw = inline(node)
        if "\\[" in raw:
            display_math(raw.strip())
        else:
            emit(math_inline(raw).strip())
            emit()
        return
    if name == "figure":
        svg = node.find("svg")
        cap = node.find("figcaption")
        if svg:
            label = svg.get("aria-label", "figure")
            fn = save_svg(svg, label)
            emit(f"![{label}](figures/{fn})")
            emit()
        if cap:
            emit(f"*{text_of(cap)}*")
            emit()
        return
    if name == "table":
        table(node)
        return
    if name == "dl":
        for dt in node.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            emit(f"- {text_of(dt)}: {text_of(dd) if dd else ''}")
        emit()
        return
    if name in ("ul", "ol"):
        for i, li in enumerate(node.find_all("li", recursive=False), 1):
            emit(("1." if name == "ol" else "-") + " " + text_of(li))
        emit()
        return
    if "lbl" in cls:
        emit(f"**{text_of(node)}**")
        emit()
        return
    if name == "div" and ("why" in cls or "how" in cls or "note" in cls):
        start = len(out)
        for c in node.children:
            block(c, depth + 1)
        quoted = []
        for line in out[start:]:
            quoted.append("> " + line if line else ">")
        del out[start:]
        out.extend(quoted)
        emit()
        return
    if name == "pre":
        emit("```")
        emit(node.get_text())
        emit("```")
        emit()
        return
    # an element with only inline content (e.g. div.eq with display math, span legend)
    if name in ("span",) or not node.find(["p", "div", "section", "table", "figure", "dl", "ul", "ol", "h1", "h2", "h3", "pre"]):
        t = inline(node).strip()
        if t:
            display_math(t)
        return
    for c in node.children:
        block(c, depth + 1)


# ---- the simulator, as a worked example with the same arithmetic --------------------------------
def simulate():
    K = ["h", "s", "t"]
    NAME = {"h": "healthiness", "s": "sustainability", "t": "convenience"}
    STATED = {"h": 0.4, "s": 0.4, "t": 0.2}
    D = [("Lentil and spinach soup", .82, .88, .42), ("Quick tomato pasta", .55, .74, .92),
         ("Grilled salmon with greens", .86, .52, .58), ("Beef and bean chilli", .61, .18, .33),
         ("Chickpea salad wrap", .74, .81, .75)]
    mean = {k: sum(d[i + 1] for d in D) / len(D) for i, k in enumerate(K)}
    o, n, rows = dict(STATED), 0, []
    pasta = dict(zip(K, D[1][1:]))
    for step in range(1, 9):
        d = {k: pasta[k] - mean[k] for k in K}
        if d["t"] > 0:
            d["t"] += 0.2
        x = {k: o[k] * math.exp(d[k]) for k in K}
        tot = sum(x.values())
        o = {k: 0.95 * x[k] / tot + 0.05 * STATED[k] for k in K}
        n += 1
        match = 1 - 0.5 * sum(abs(STATED[k] - o[k]) for k in K)
        best = max(K, key=lambda k: abs(o[k] - STATED[k]))
        a = abs(o[best] - STATED[best])
        if a >= 0.15 and n >= 6:
            sup = f"proposes moving {NAME[best]} to {round(100 * (STATED[best] + o[best]) / 2)} %"
        elif a >= 0.10 and n >= 3:
            sup = f"asks a trade-off question about {NAME[best]}"
        else:
            sup = "stays quiet"
        rows.append(f"| {step} | {round(100 * o['h'])} % | {round(100 * o['s'])} % | {round(100 * o['t'])} % "
                    f"| {round(100 * match)} % | {round(100 * n / (n + 5))} % | {sup} |")
    return rows


SIM_MD = "\n".join([
    "#### Worked example (the interactive simulator of the HTML version)",
    "",
    "Stated goal: health 40 %, sustainability 40 %, convenience 20 %. Five example dishes are shown each time:",
    "",
    "| Shown dish (example) | Health | Sust. | Conv. |",
    "|---|---|---|---|",
    "| Lentil and spinach soup | 0.82 | 0.88 | 0.42 |",
    "| Quick tomato pasta | 0.55 | 0.74 | 0.92 |",
    "| Grilled salmon with greens | 0.86 | 0.52 | 0.58 |",
    "| Beef and bean chilli | 0.61 | 0.18 | 0.33 |",
    "| Chickpea salad wrap | 0.74 | 0.81 | 0.75 |",
    "",
    "The user chooses the **quick tomato pasta** with the reason *it is easy*, eight times in a row. Same",
    "arithmetic as the app (multiplicative update, +0.2 nudge for a consistent reason, 5 % anchor to the goal):",
    "",
    "| Choice | Observed health | Observed sust. | Observed conv. | Match | Confidence | Supervisor |",
    "|---|---|---|---|---|---|---|",
    *simulate(),
    "",
    "> The interactive version, where you can choose and reject dishes yourself, is in the HTML guide:",
    "> open `docs/guide/index.html` in a browser, or **How FoodRec works** in the app's account menu.",
    "",
])

# ---- run --------------------------------------------------------------------------------------
body = soup.body
emit("<!-- Generated from docs/guide/index.html (the interactive version) by docs/guide/convert.py. "
     "Edit index.html and run: python docs/guide/convert.py -->")
emit()
for child in body.children:
    block(child)

# a contents list after the title and introduction
toc = ["## Contents", ""]
for level, title, anchor in TOC:
    if level == 4 or not anchor:
        continue
    toc.append(("" if level == 2 else "   ") + f"- [{re.sub(r'[$`]', '', title)}](#{anchor})")
toc.append("")
first_part = next(i for i, l in enumerate(out) if l.startswith("## Part"))
anchor_line = first_part - 2 if out[first_part - 2].startswith("<a name") else first_part
out[anchor_line:anchor_line] = toc
md = "\n".join(out)
md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
OUT.write_text(md, encoding="utf-8")
print(f"wrote {OUT} ({len(md.splitlines())} lines), {fig_no} figure(s) in {FIG}")
