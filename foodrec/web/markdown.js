// Minimal, safe markdown for the Config guides: headings, paragraphs, lists, tables, code
// blocks, inline code, bold, italic and links. Builds DOM nodes; never uses innerHTML.
import { h } from "./lib.js";

function inline(text) {
  const out = [];
  const rx = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  for (const m of text.matchAll(rx)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("`")) out.push(h("code", t.slice(1, -1)));
    else if (t.startsWith("**")) out.push(h("b", inline(t.slice(2, -2))));
    else if (t.startsWith("*")) out.push(h("i", inline(t.slice(1, -1))));
    else {
      const [, label, href] = t.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const safe = /^(https?:|\/|#)/.test(href) ? href : "#";
      out.push(h("a", { href: safe, target: safe.startsWith("http") ? "_blank" : null, rel: "noopener" }, label));
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function markdown(src) {
  const root = h("div.md");
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.startsWith("```")) {
      const buf = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) buf.push(lines[i]);
      i++;
      root.append(h("pre", h("code", buf.join("\n"))));
      continue;
    }
    const hm = line.match(/^(#{1,4})\s+(.*)/);
    if (hm) { root.append(h(`h${Math.min(6, hm[1].length + 1)}`, inline(hm[2]))); i++; continue; }
    if (line.trim().startsWith("|")) {
      const rows = [];
      for (; i < lines.length && lines[i].trim().startsWith("|"); i++) rows.push(lines[i].trim());
      const cells = (r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r, j) => !(j === 1 && /^\|[\s:|-]+\|$/.test(r)));
      root.append(h("table.t", h("thead", h("tr", cells(body[0]).map((c) => h("th", inline(c))))),
        h("tbody", body.slice(1).map((r) => h("tr", cells(r).map((c) => h("td", inline(c))))))));
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const list = h(ordered ? "ol" : "ul");
      for (; i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i]); i++) {
        list.append(h("li", inline(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""))));
      }
      root.append(list);
      continue;
    }
    if (line.startsWith("> ")) {
      const buf = [];
      for (; i < lines.length && lines[i].startsWith("> "); i++) buf.push(lines[i].slice(2));
      root.append(h("blockquote", inline(buf.join(" "))));
      continue;
    }
    const buf = [];
    for (; i < lines.length && lines[i].trim() && !/^(#|```|\||\s*([-*]|\d+\.)\s|> )/.test(lines[i]); i++) buf.push(lines[i].trim());
    root.append(h("p", inline(buf.join(" "))));
  }
  return root;
}
