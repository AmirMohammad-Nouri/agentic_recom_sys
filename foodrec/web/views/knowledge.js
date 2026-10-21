// Knowledge: knowledge-graph explorer and the retrieval library.
import { api, clear, h, human, icon, state, toast, ago } from "../lib.js";

const VIS = "https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js";
const LABEL_COLORS = { Recipe: "--c-t", Ingredient: "--c-h", Cuisine: "--c-p", Category: "--muted", Allergen: "--danger",
  Diet: "--c-s", User: "--accent", Interaction: "--c-c", Request: "--info", Decision: "--warn", Goal: "--c-s" };

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888"; }
function loadVis() {
  if (window.vis) return Promise.resolve();
  return new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = VIS; s.onload = ok; s.onerror = () => fail(new Error("Could not load the graph library (offline?)"));
    document.head.append(s);
  });
}
function nodeName(n) {
  const p = n.props || {};
  if (n.label === "Interaction") return `${p.type || "event"}${p.reason ? " · " + human(p.reason) : ""}`;
  if (n.label === "Request") return `list ${p.request_id?.slice(4, 10) || ""}`;
  if (n.label === "Goal") return `goal v${p.version}`;
  if (n.label === "Decision") return p.title?.slice(0, 28) || "decision";
  return p.title || p.name || p.user_id || n.uid;
}

function graphPanel() {
  const canvas = h("div", { id: "graph" });
  const detail = h("div.card-body.node-detail", h("div.small.muted", "Click a node to see its properties. Double-click to expand its neighbours."));
  const q = h("input", { type: "search", placeholder: "Search recipes, ingredients, cuisines…", style: { width: "100%" } });
  const label = h("select", h("option", { value: "" }, "Any type"), Object.keys(LABEL_COLORS).map((l) => h("option", { value: l }, l)));
  const hits = h("div.stack.tight", { style: { marginTop: "8px" } });
  const stats = h("div.small.muted");
  let net = null, nodes = null, edges = null;

  async function show(uid, depth = 1, reset = true) {
    try {
      await loadVis();
    } catch (e) { clear(canvas, h("div.empty", e.message)); return; }
    const g = await api(`/api/graph/neighborhood?uid=${encodeURIComponent(uid)}&depth=${depth}`);
    if (!net || reset) {
      nodes = new vis.DataSet(); edges = new vis.DataSet();
      net = new vis.Network(canvas, { nodes, edges }, {
        nodes: { shape: "dot", size: 12, font: { size: 12, color: cssVar("--text"), face: "system-ui" }, borderWidth: 0 },
        edges: { arrows: { to: { enabled: true, scaleFactor: 0.4 } }, color: { color: cssVar("--border-strong"), highlight: cssVar("--accent") },
          font: { size: 9, color: cssVar("--muted"), strokeWidth: 0, face: "system-ui" }, smooth: { type: "continuous" } },
        physics: { barnesHut: { gravitationalConstant: -4200, springLength: 120 }, stabilization: { iterations: 140 } },
        interaction: { hover: true, tooltipDelay: 200 },
      });
      net.on("click", (p) => { if (p.nodes[0]) inspect(p.nodes[0]); });
      net.on("doubleClick", (p) => { if (p.nodes[0]) show(p.nodes[0], 1, false); });
    }
    for (const n of g.nodes) {
      if (nodes.get(n.uid)) continue;
      nodes.add({ id: n.uid, label: nodeName(n), title: n.label, color: cssVar(LABEL_COLORS[n.label] || "--muted"),
        size: n.uid === uid ? 18 : n.label === "Recipe" || n.label === "User" ? 13 : 9, raw: n });
    }
    for (const e of g.edges) {
      const id = `${e.source}|${e.type}|${e.target}`;
      if (!edges.get(id)) edges.add({ id, from: e.source, to: e.target, label: e.type.toLowerCase().replaceAll("_", " ") });
    }
    inspect(uid);
  }

  function inspect(uid) {
    const n = nodes?.get(uid)?.raw;
    if (!n) return;
    clear(detail, h("div.row.between", h("h3", nodeName(n)), h("span.tag", n.label)),
      h("div.mono.muted", n.uid),
      h("dl", Object.entries(n.props || {}).filter(([k]) => k !== "uid").slice(0, 18).map(([k, v]) =>
        [h("dt", human(k)), h("dd", typeof v === "number" ? (Number.isInteger(v) ? v : v > 1e9 ? new Date(v * 1000).toLocaleString() : v.toFixed(3)) : String(v))])),
      h("button.btn.sm", { style: { marginTop: "10px" }, onclick: () => show(uid, 1, false) }, icon("plus"), "Expand neighbours"));
  }

  async function search() {
    const term = q.value.trim();
    if (!term) return clear(hits);
    const rows = await api(`/api/graph/search?q=${encodeURIComponent(term)}${label.value ? "&label=" + label.value : ""}`);
    clear(hits, rows.length ? rows.slice(0, 8).map((r) => h("button.pick", { onclick: () => show(r.uid) },
      h("div.t", r.name), h("div.small.muted", r.label))) : h("div.small.muted", "No match."));
  }
  q.addEventListener("input", () => { clearTimeout(q._t); q._t = setTimeout(search, 200); });
  label.addEventListener("change", search);

  api("/api/graph/stats").then((s) => clear(stats, `${s.nodes} nodes · ${s.relationships} relationships · ${state.system.graph}`));
  setTimeout(() => show(`user:${state.user}`), 0);

  return h("div.sidebar-layout",
    h("section.card", { style: { overflow: "hidden" } }, h("div.card-head", h("h2", "Knowledge graph"), stats), canvas),
    h("aside.stack",
      h("section.card", h("div.card-body", h("div.row", q), h("div", { style: { marginTop: "8px" } }, label), hits,
        h("div.btn-row", { style: { marginTop: "10px" } },
          h("button.btn.sm", { onclick: () => show(`user:${state.user}`) }, icon("user"), "Me"),
          h("button.btn.sm", { onclick: () => show("recipe:r001") }, "A recipe"),
          h("button.btn.sm", { onclick: () => show("ingredient:chickpeas") }, "An ingredient")))),
      h("section.card", detail)));
}

function libraryPanel() {
  const q = h("input", { type: "search", placeholder: "Test retrieval, e.g. \"how is sustainability measured\"", style: { flex: 1 } });
  const kinds = h("select", h("option", { value: "" }, "All sources"), ["recipe", "ingredient", "method", "user"].map((k) => h("option", { value: k }, k)));
  const results = h("div");
  const docs = h("div");
  const title = h("input", { type: "text", placeholder: "Title" });
  const text = h("textarea", { rows: 6, placeholder: "Paste vetted guidelines, notes or a description. It is chunked, embedded and searchable immediately." });
  const r = state.system.retrieval;

  async function search() {
    if (!q.value.trim()) return;
    const out = await api(`/api/knowledge/search?q=${encodeURIComponent(q.value)}${kinds.value ? "&kinds=" + kinds.value : ""}`);
    clear(results, out.hits.length ? out.hits.map((x) => h("div.hit",
      h("div.row.between", h("b", x.title), h("span.tag", x.kind)),
      h("div.small", { style: { color: "var(--text-2)", marginTop: "2px" } }, x.text.slice(0, 300) + (x.text.length > 300 ? "…" : "")),
      h("div.scores", h("span.tag", `BM25 rank ${x.bm25_rank ?? "–"}`), h("span.tag", `vector rank ${x.dense_rank ?? "–"} (${x.dense ?? 0})`),
        h("span.tag.accent", `fused ${x.rrf}`), h("span.mono.muted", x.id))))
      : h("div.small.muted", "No passages found."));
  }
  async function loadDocs() {
    const list = await api("/api/knowledge/documents");
    clear(docs, list.length ? list.map((d) => h("div.hit.row.between",
      h("div.grow", h("b", d.title), h("div.small.muted", `${d.text.length} characters · added ${ago(d.created_at)}`)),
      h("button.icon-btn", { title: "Remove", onclick: async () => { await api(`/api/knowledge/documents/${d.doc_id}`, { method: "DELETE" }); loadDocs(); } }, icon("trash"))))
      : h("div.small.muted", "No documents added yet. Recipes, ingredients and method notes are indexed automatically."));
  }
  async function add() {
    try {
      await api("/api/knowledge/documents", { method: "POST", body: { title: title.value, text: text.value } });
      title.value = ""; text.value = "";
      toast("Document indexed");
      loadDocs();
    } catch (e) { toast(e.message, "err"); }
  }
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  loadDocs();
  return h("div.two-col",
    h("section.card", h("div.card-head", h("h2", "Search the knowledge base")),
      h("div.card-body.stack",
        h("div.row", q, kinds, h("button.btn", { onclick: search }, icon("search"), "Search")),
        h("div.hint", `${r.chunks} passages · ${r.embedder}${r.semantic ? "" : " (lexical fallback, not semantic)"} · ${r.vector_backend} · ${r.fusion}`),
        results)),
    h("section.card", h("div.card-head", h("h2", "Your documents")),
      h("div.card-body.stack", h("div.field", h("label", "Title"), title), h("div.field", h("label", "Text"), text),
        h("div", h("button.btn.primary", { onclick: add }, icon("plus"), "Add to knowledge base")),
        h("div.divider"), docs)));
}

export function knowledgeView() {
  const body = h("div");
  const tabs = h("div.seg");
  const set = (which) => {
    [...tabs.children].forEach((b) => b.classList.toggle("on", b.dataset.k === which));
    clear(body, which === "graph" ? graphPanel() : libraryPanel());
  };
  tabs.append(h("button", { dataset: { k: "graph" }, onclick: () => set("graph") }, "Graph"),
    h("button", { dataset: { k: "library" }, onclick: () => set("library") }, "Library"));
  set("graph");
  return h("div",
    h("div.page-head", h("div", h("h1", "Knowledge"),
      h("p", "The structured food graph and the documents the supervisor can retrieve from.")), tabs),
    body);
}
