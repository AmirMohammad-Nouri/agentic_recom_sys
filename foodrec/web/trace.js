// The supervisor's visible work: a live, collapsible list of trace steps.
import { h, icon, clear } from "./lib.js";

const SRC = { policy: "policy", rules: "rules", model: "model", skill: "skill", engine: "engine", graph: "graph",
  learner: "learner", store: "store", validator: "validator", template: "template" };

function stepEl(s) {
  const el = h("div.step.k-" + s.kind + (s.status === "running" ? ".running" : "") + (s.status === "failed" ? ".failed" : ""),
    { dataset: { id: s.id || "" } });
  fill(el, s);
  return el;
}

function fill(el, s) {
  el.className = "step k-" + s.kind + (s.status === "running" ? " running" : "") + (s.status === "failed" ? " failed" : "");
  const dur = s.duration_ms ?? null;
  const title = h("div.step-title", h("span", s.title),
    s.source ? h("span.src", SRC[s.source] || s.source) : null,
    dur !== null && dur !== undefined ? h("span.ms", dur < 1000 ? `${Math.round(dur)} ms` : `${(dur / 1000).toFixed(1)} s`) : null);
  const kids = [title];
  if (s.detail || s.status === "running") {
    const d = h("div.step-detail", s.detail || "");
    if (s.status === "running") d.append(h("span.cursor"));
    kids.push(d);
  }
  if (s.evidence && !(Array.isArray(s.evidence) && !s.evidence.length) && s.kind !== "model") {
    kids.push(h("details.step-evidence", h("summary", "evidence"), h("pre", JSON.stringify(s.evidence, null, 2))));
  }
  clear(el, kids);
}

// Create a trace block. `live` keeps it open and shows the cursor on the running step.
export function traceView({ steps = [], live = false, title = null } = {}) {
  const body = h("div.trace-body");
  const label = h("span.grow");
  const head = h("div.trace-head", { role: "button", tabindex: 0 }, icon("chev", "chev"), label);
  const root = h("div.trace" + (live ? ".open" : ""), head, body);
  const byId = new Map();
  let t0 = performance.now();
  let finished = !live;
  const toggle = () => root.classList.toggle("open");
  head.addEventListener("click", toggle);
  head.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });

  function refreshLabel(meta) {
    const n = byId.size;
    if (!finished) {
      const running = [...byId.values()].reverse().find((x) => x.s.status === "running") || [...byId.values()].pop();
      clear(label, h("span", { style: { color: "var(--text)" } }, title || "Working"),
        running ? h("span.muted", " · " + running.s.title) : null, h("span.cursor"));
    } else {
      const secs = meta?.elapsed_ms != null ? meta.elapsed_ms / 1000 : (performance.now() - t0) / 1000;
      const failed = [...byId.values()].filter((x) => x.s.status === "failed").length;
      const dur = secs < 1 ? `${Math.max(1, Math.round(secs * 1000))} ms` : `${secs.toFixed(1)} s`;
      clear(label, title ? h("span", { style: { color: "var(--text)" } }, title + " · ") : null,
        live || meta ? `Worked for ${dur} · ` : "", `${n} step${n === 1 ? "" : "s"}`,
        failed ? h("span", { style: { color: "var(--danger)" } }, ` · ${failed} fell back`) : null);
    }
  }

  function add(s) {
    const el = stepEl(s);
    byId.set(s.id || Math.random().toString(36), { s, el });
    body.append(el);
    refreshLabel();
  }
  function update(s) {
    const x = byId.get(s.id);
    if (!x) return add(s);
    x.s = s;
    fill(x.el, s);
    refreshLabel();
  }
  function token(id, text) {
    const x = byId.get(id);
    if (!x) return;
    x.s.detail = (x.s.detail || "") + text;
    const d = x.el.querySelector(".step-detail");
    if (d) {
      const cur = d.querySelector(".cursor");
      d.textContent = x.s.detail;
      if (cur || x.s.status === "running") d.append(h("span.cursor"));
      d.scrollTop = d.scrollHeight;
    } else fill(x.el, x.s);
  }
  function done(meta) {
    finished = true;
    root.classList.remove("open");
    refreshLabel(meta);
  }
  steps.forEach(add);
  refreshLabel();
  return { el: root, add, update, token, done, open: () => root.classList.add("open") };
}
