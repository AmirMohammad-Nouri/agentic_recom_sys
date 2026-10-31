// Shared helpers: DOM builder, API client, icons, formatting, tiny app state.

// p = taste (a fixed, learned share); the others are the values the user weighs. They are
// configurable (foodrec.config.json "criteria"): setCriteria() replaces these in place from
// /api/system, so every module that imported them sees the configured set.
export const CRIT = ["p", "h", "s", "t"];
export const VALUES = ["h", "s", "t"];
export const CRIT_META = {
  p: { label: "Taste", long: "Taste match", color: "var(--c-p)", icon: "heart",
    what: "Learned from your likes and rejections" },
  h: { label: "Healthiness", long: "Healthiness", lo: "Not important", hi: "Very important", color: "var(--c-h)", icon: "pulse",
    what: "Nine nutrient factors, weighted by your health goal" },
  s: { label: "Sustainability", long: "Sustainability", lo: "Not important", hi: "Very important", color: "var(--c-s)", icon: "leaf",
    what: "Estimated kg CO₂e per kg of dish" },
  t: { label: "Convenience", long: "Convenience", lo: "Not important", hi: "Very important", color: "var(--c-t)", icon: "list",
    what: "Fewer ingredients score higher" },
};

const ICON_FOR = { healthiness: "pulse", sustainability: "leaf", convenience: "list", field: "target" };
export function setCriteria(specs) {
  if (!specs?.length) return;
  VALUES.splice(0, VALUES.length, ...specs.map((c) => c.id));
  CRIT.splice(0, CRIT.length, "p", ...VALUES);
  for (const k of Object.keys(CRIT_META)) if (k !== "p" && !VALUES.includes(k)) delete CRIT_META[k];
  for (const c of specs) {
    CRIT_META[c.id] = {
      ...(CRIT_META[c.id] || {}), label: c.label, long: c.label, lo: "Not important", hi: "Very important",
      color: c.color || CRIT_META[c.id]?.color || `var(--c-${c.id})`, icon: ICON_FOR[c.kind] || "target",
      what: c.description || CRIT_META[c.id]?.what || "", kind: c.kind, spec: c,
    };
  }
}

// h("div.card.pad", {onclick}, child, "text", [children])
export function h(sel, attrs, ...kids) {
  const [tag, ...cls] = sel.split(".");
  const el = document.createElement(tag || "div");
  if (cls.length) el.className = cls.join(" ");
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    kids.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className += (el.className ? " " : "") + v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k in el && k !== "list" && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, kids);
  return el;
}

function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}

export function clear(el, ...kids) {
  el.replaceChildren();
  append(el, kids);
  return el;
}

const ICONS = {
  clock: "M12 7v5l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  euro: "M17 7.5A6 6 0 1 0 17 16.5M5 10h9M5 14h9",
  leaf: "M5 19c0-8 5-13 14-14 0 9-5 14-13 14H5zM5 19l7-7",
  flame: "M12 21c-3.9 0-7-2.8-7-6.5C5 10 9 8 9 4c2.5 1.4 6 4.5 6 9 .9-.7 1.6-1.9 1.8-3C18.3 11.4 19 13 19 14.5 19 18.2 15.9 21 12 21z",
  heart: "M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z",
  bookmark: "M7 4h10v16l-5-4-5 4z",
  check: "M5 12l4 4 10-10",
  x: "M6 6l12 12M18 6L6 18",
  chev: "M9 6l6 6-6 6",
  down: "M6 9l6 6 6-6",
  info: "M12 8h.01M11 12h1v5h1M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6",
  send: "M5 12h13M13 6l6 6-6 6",
  undo: "M9 14L4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3",
  reset: "M4 12a8 8 0 1 0 2.3-5.7M4 4v4h4",
  sun: "M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
  graph: "M6 6m-2 0a2 2 0 1 0 4 0 2 2 0 1 0-4 0M18 6m-2 0a2 2 0 1 0 4 0 2 2 0 1 0-4 0M12 18m-2 0a2 2 0 1 0 4 0 2 2 0 1 0-4 0M8 6h8M7 8l4 8M17 8l-4 8",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4",
  plus: "M12 5v14M5 12h14",
  trash: "M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 20c1.5-3.5 4.5-5 8-5s6.5 1.5 8 5",
  compare: "M8 4v16M16 4v16M4 8h4M16 16h4",
  bowl: "M4 11h16a8 8 0 0 1-16 0zM9 7c0-2 2-2 2-4M14 7c0-2 2-2 2-4",
  eye: "M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  bulb: "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V16h5v-.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z",
  branch: "M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9",
  tool: "M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z",
  cpu: "M9 9h6v6H9zM4 9h2M4 15h2M18 9h2M18 15h2M9 4v2M15 4v2M9 18v2M15 18v2M6 6h12v12H6z",
  shield: "M12 3l7 3v5c0 5-3 8.5-7 10-4-1.5-7-5-7-10V6zM9 12l2 2 4-4",
  pulse: "M3 12h4l2-5 4 10 2-5h6",
  list: "M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01",
  panel: "M4 5h16v14H4zM14 5v14",
  close: "M6 6l12 12M18 6L6 18",
  target: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
  ext: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
};
export function icon(name, cls = "") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "i " + cls);
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", ICONS[name] || ICONS.info);
  svg.append(p);
  return svg;
}

// ---- API ------------------------------------------------------------------------------
// Every request names the user (the server checks that user's access role) and, when one was
// entered on the Config page, the admin token.
export function headers(json = false) {
  const hd = { "X-User": state.user };
  const session = safeGet("foodrec.session");
  if (session) hd.Authorization = `Bearer ${session}`;
  const tok = sessionGet("foodrec.admin_token");
  if (tok) hd["X-Admin-Token"] = tok;
  if (json) hd["Content-Type"] = "application/json";
  return hd;
}

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, { method, headers: headers(!!body), body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || data.detail || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// Download a file from the API (exports), keeping the auth headers.
export async function download(path) {
  const res = await fetch(path, { headers: headers() });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.detail || `HTTP ${res.status}`);
  }
  const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "foodrec-export";
  const url = URL.createObjectURL(await res.blob());
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// Read a newline-delimited JSON stream, calling onItem for each object.
export async function stream(path, body, onItem) {
  const res = await fetch(path, { method: "POST", headers: headers(true), body: JSON.stringify(body) });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line) onItem(JSON.parse(line));
    }
  }
  if (buf.trim()) onItem(JSON.parse(buf));
}

// ---- state ------------------------------------------------------------------------------
const listeners = new Set();
export const state = {
  user: safeGet("foodrec.user") || "demo",
  system: null,
  user_state: null,
  lastResult: null,
};
export function on(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function emit(what, data) { for (const fn of listeners) fn(what, data); }
export function userPath(p = "") { return `/api/users/${encodeURIComponent(state.user)}${p}`; }

export function safeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
export function safeSet(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } }
export function sessionGet(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
export function sessionSet(k, v) {
  try { if (v) sessionStorage.setItem(k, v); else sessionStorage.removeItem(k); } catch { /* storage unavailable */ }
}

// ---- access role (set by the Config page; enforced by the server too) --------------------
export function can(feature) { return state.user_state?.access?.features?.[feature] !== false; }
export function visibleCriteria(keys = CRIT) {
  const vis = state.user_state?.access?.visible_criteria;
  return vis ? keys.filter((k) => vis.includes(k)) : keys;
}
// Report an interface interaction for the study log (fire and forget).
export function track(kind, data = {}) {
  if (!state.user_state?.onboarded) return;
  fetch(userPath("/ui-events"), { method: "POST", headers: headers(true), body: JSON.stringify({ kind, ...data }) })
    .catch(() => {});
}

// ---- formatting --------------------------------------------------------------------------
export const f2 = (x) => (x === null || x === undefined ? "–" : Number(x).toFixed(2));
export const pct = (x) => (x === null || x === undefined ? "–" : `${Math.round(x * 100)}%`);
export function ago(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
export function when(ts) {
  return new Date(ts * 1000).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
export const human = (s) => String(s ?? "").replaceAll("_", " ");

export function toast(msg, kind = "") {
  let box = document.querySelector(".toasts");
  if (!box) document.body.append((box = h("div.toasts")));
  const t = h("div.toast" + (kind ? "." + kind : ""), msg);
  box.append(t);
  setTimeout(() => t.remove(), kind === "err" ? 6000 : 3200);
}

// Small popover menu anchored to a button.
export function menu(anchor, items) {
  document.querySelectorAll(".menu").forEach((m) => m.remove());
  const m = h("div.menu", { role: "menu" }, items.map((it) =>
    it.label && !it.run ? h("div.menu-label", it.label)
      : h("button", { role: "menuitem", onclick: () => { m.remove(); it.run(); } }, it.text)));
  document.body.append(m);
  const r = anchor.getBoundingClientRect();
  const left = Math.min(window.scrollX + r.left, window.scrollX + window.innerWidth - m.offsetWidth - 12);
  m.style.left = `${Math.max(8, left)}px`;
  m.style.top = `${window.scrollY + r.bottom + 4}px`;
  setTimeout(() => document.addEventListener("click", function off(e) {
    if (!m.contains(e.target)) { m.remove(); document.removeEventListener("click", off); }
  }), 0);
}

export function grade(g) {
  return h("span.grade." + (g || "none"), { title: g ? `Health grade ${g} (FoodRec banding of the healthiness score, not Nutri-Score)` : "Health grade unknown" }, g || "?");
}

// Ask the embedded assistant from anywhere: opens the dock and sends the text.
export function ask(text) { emit("ask", text); }
export const co2 = (x) => (x === null || x === undefined ? "unknown" : `${Number(x).toFixed(x < 10 ? 1 : 0)} kg CO₂e/kg`);

export function weightBar(weights, keys = CRIT) {
  return h("div.weightbar", keys.map((k) => h("span", {
    style: { width: `${(weights[k] || 0) * 100}%`, background: CRIT_META[k].color },
    title: `${CRIT_META[k].long}: ${pct(weights[k])}`,
  })));
}

export function legend(keys = CRIT) {
  return h("div.legend", keys.map((k) => h("span", h("i", { style: { background: CRIT_META[k].color } }), CRIT_META[k].label)));
}

// Minimal, safe rendering of assistant text: paragraphs and "- " bullet lists.
export function richText(text) {
  const out = h("div.answer");
  let list = null;
  for (const line of String(text || "").split("\n")) {
    const t = line.trim();
    if (!t) { list = null; continue; }
    if (t.startsWith("- ")) {
      if (!list) out.append((list = h("ul")));
      list.append(h("li", t.slice(2)));
    } else {
      list = null;
      out.append(h("p", t));
    }
  }
  return out;
}
