// The supervisor's visible work: a live timeline of what it looked at, decided and checked.
import { h, icon, clear, human, CRIT_META } from "./lib.js";

const KIND = {
  perceive: { icon: "eye", label: "Context" },
  reason: { icon: "bulb", label: "Reasoning" },
  decide: { icon: "branch", label: "Decision" },
  tool: { icon: "tool", label: "Tool" },
  model: { icon: "cpu", label: "Model thinking" },
  check: { icon: "shield", label: "Check" },
  answer: { icon: "spark", label: "Answer" },
};
const SRC = { policy: "Policy", rules: "Rules", model: "Model", skill: "Skill", engine: "Ranking engine", graph: "Knowledge graph",
  learner: "User model", store: "Event log", validator: "Validator", template: "Template", retriever: "Retrieval" };
const CRIT_NAME = new Proxy({}, { get: (_, k) => CRIT_META[k]?.long });

const fmtMs = (ms) => (ms < 1000 ? `${Math.max(1, Math.round(ms))} ms` : `${(ms / 1000).toFixed(1)} s`);
const isScalar = (v) => v === null || ["string", "number", "boolean"].includes(typeof v);

function val(v) {
  if (v === null || v === undefined) return h("span.muted", "–");
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) < 10 ? 2 : 1);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.every(isScalar) ? v.map((x) => (typeof x === "number" ? val(x) : String(x))).join(", ") : `${v.length} items`;
  if (typeof v === "object") {
    const keys = Object.keys(v);
    if (keys.length && keys.every((k) => isScalar(v[k]))) {
      return h("span.kv-inline", keys.map((k) => h("span", h("i", (CRIT_NAME[k] || human(k)) + " "), val(v[k]))));
    }
    return `${keys.length} fields`;
  }
  return String(v);
}

// Evidence as a readable table: objects -> key/value rows, arrays of records -> small table.
function evidenceView(ev) {
  if (Array.isArray(ev)) {
    if (!ev.length) return null;
    if (ev.every((x) => x && typeof x === "object" && !Array.isArray(x))) {
      const cols = [...new Set(ev.flatMap((x) => Object.keys(x)))].filter((c) => ev.some((x) => isScalar(x[c]) && x[c] !== null)).slice(0, 4);
      return h("table.ev-table", h("thead", h("tr", cols.map((c) => h("th", human(c))))),
        h("tbody", ev.slice(0, 8).map((x) => h("tr", cols.map((c) => h("td", val(x[c])))))),
        ev.length > 8 ? h("tfoot", h("tr", h("td", { colspan: cols.length }, `+ ${ev.length - 8} more`))) : null);
    }
    return h("div.ev-list", ev.slice(0, 12).map((x) => h("span.tag", String(isScalar(x) ? x : JSON.stringify(x)))));
  }
  if (ev && typeof ev === "object") {
    const keys = Object.keys(ev);
    if (!keys.length) return null;
    return h("table.ev-table.kv", h("tbody", keys.slice(0, 14).map((k) => h("tr", h("th", CRIT_NAME[k] || human(k)), h("td", val(ev[k]))))));
  }
  return h("div.ev-list", String(ev));
}

function stepEl(s) {
  const el = h("li.tstep");
  fill(el, s);
  return el;
}

function fill(el, s) {
  const k = KIND[s.kind] || { icon: "info", label: human(s.kind) };
  el.className = "tstep k-" + s.kind + (s.status === "running" ? " running" : "") + (s.status === "failed" ? " failed" : "");
  const dur = s.duration_ms;
  const head = h("div.tstep-head",
    h("span.tstep-title", s.title),
    s.status === "failed" ? h("span.pill.bad", "fell back") : null,
    s.source ? h("span.pill.src", SRC[s.source] || human(s.source)) : null,
    dur != null ? h("span.pill.time", fmtMs(dur)) : null);
  const kids = [h("span.tstep-dot", { title: k.label }, icon(k.icon)), h("div.tstep-main", head)];
  const main = kids[1];
  if (s.kind === "model") {
    main.append(h("div.thinking", h("div.thinking-label", "Model reasoning · unverified, shown as streamed"),
      h("div.thinking-text", s.detail || "", s.status === "running" ? h("span.cursor") : null)));
  } else if (s.detail || s.status === "running") {
    main.append(h("div.tstep-detail", s.detail || "", s.status === "running" ? h("span.cursor") : null));
  }
  const ev = s.kind !== "model" ? evidenceView(s.evidence) : null;
  if (ev) {
    const raw = h("pre.raw.hidden", JSON.stringify(s.evidence, null, 2));
    const toggle = h("button.linkish", { type: "button", onclick: (e) => {
      raw.classList.toggle("hidden");
      e.currentTarget.textContent = raw.classList.contains("hidden") ? "raw JSON" : "hide JSON";
    } }, "raw JSON");
    main.append(h("div.evidence", h("div.evidence-head", h("span", "Evidence"), toggle), ev, raw));
  }
  clear(el, kids);
}

// Create a trace block. `live` keeps it open and animates the running step.
export function traceView({ steps = [], live = false, title = null, open = false } = {}) {
  const list = h("ol.tline");
  const label = h("span.trace-label");
  const status = h("span.trace-status");
  const chips = h("span.trace-chips");
  const head = h("button.trace-head", { type: "button", "aria-expanded": "false" }, status, label, chips, icon("chev", "chev"));
  const root = h("div.trace" + (live || open ? ".open" : "") + (live ? ".live" : ""), head, h("div.trace-body", list));
  const byId = new Map();
  const t0 = performance.now();
  let finished = !live;
  head.addEventListener("click", () => {
    root.classList.toggle("open");
    head.setAttribute("aria-expanded", root.classList.contains("open"));
  });

  function refreshLabel(meta) {
    const all = [...byId.values()].map((x) => x.s);
    const n = all.length;
    const failed = all.filter((s) => s.status === "failed").length;
    const sources = [...new Set(all.map((s) => s.source).filter((x) => x && !["policy", "template"].includes(x)))];
    clear(chips, sources.slice(0, 4).map((x) => h("span.pill.src", SRC[x] || x)));
    if (!finished) {
      const running = all.slice().reverse().find((s) => s.status === "running") || all[all.length - 1];
      clear(status, h("span.spinner"));
      clear(label, h("b", title || "Thinking"), running ? h("span.muted", " · " + running.title) : null);
    } else {
      const ms = meta?.elapsed_ms != null ? meta.elapsed_ms : (live ? performance.now() - t0 : null);
      clear(status, failed ? h("span.st.warn", icon("info")) : h("span.st.ok", icon("check")));
      clear(label, h("b", title || (ms != null ? `Thought for ${fmtMs(ms)}` : "How this was worked out")),
        h("span.muted", ` · ${n} step${n === 1 ? "" : "s"}`),
        failed ? h("span.warn-text", ` · ${failed} fell back to a safe default`) : null);
    }
  }

  function add(s) {
    const el = stepEl(s);
    byId.set(s.id || Math.random().toString(36), { s, el });
    list.append(el);
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
    const d = x.el.querySelector(".thinking-text, .tstep-detail");
    if (d) {
      d.textContent = x.s.detail;
      if (x.s.status === "running") d.append(h("span.cursor"));
      d.scrollTop = d.scrollHeight;
    } else fill(x.el, x.s);
  }
  function done(meta) {
    finished = true;
    root.classList.remove("open", "live");
    refreshLabel(meta);
  }
  steps.forEach(add);
  refreshLabel();
  return { el: root, add, update, token, done, open: () => root.classList.add("open") };
}
