// App shell: top bar, router, system status, onboarding gate.
import { api, clear, h, icon, on, safeGet, safeSet, state, toast, userPath } from "./lib.js";
import { discoverView } from "./views/discover.js";
import { assistantView } from "./views/assistant.js";
import { insightsView } from "./views/insights.js";
import { knowledgeView } from "./views/knowledge.js";
import { activityView, onboarding, profileView } from "./views/profile.js";

const ROUTES = {
  discover: { label: "Discover", view: discoverView, icon: "bowl" },
  assistant: { label: "Assistant", view: assistantView, icon: "spark" },
  insights: { label: "Insights", view: insightsView, icon: "info" },
  knowledge: { label: "Knowledge", view: knowledgeView, icon: "graph" },
  activity: { label: "Activity", view: activityView, icon: "clock" },
  profile: { label: "Profile", view: profileView, icon: "user" },
};

const main = document.querySelector("main");
const nav = document.querySelector(".nav");
const statusPill = document.querySelector(".status-pill");
let current = null;

function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}
applyTheme(safeGet("foodrec.theme"));

function renderNav() {
  const route = location.hash.replace("#/", "") || "discover";
  const open = state.user_state?.open_decisions?.length || 0;
  clear(nav, Object.entries(ROUTES).map(([k, r]) => h("a" + (k === route ? ".active" : ""), { href: `#/${k}`, title: r.label },
    icon(r.icon), h("span.label", r.label), k === "discover" && open ? h("span.count", { title: `${open} open question(s)` }, open) : null)));
}

function renderStatus() {
  const s = state.system;
  if (!s) return;
  const llm = s.llm;
  clear(statusPill, h("span.dot." + (llm.available ? "ok" : "warn")),
    h("span.txt", llm.available ? llm.model : "rule-based", h("span.muted", " · "), s.graph.startsWith("Neo4j") ? "Neo4j" : "local graph"));
}

function systemPanel() {
  const s = state.system;
  const row = (k, v, ok) => h("tr", h("td", h("span.row", h("span.dot." + (ok === undefined ? "" : ok ? "ok" : "warn")), k)), h("td", v));
  const overlay = h("div.overlay", { onclick: (e) => { if (e.target === overlay) overlay.remove(); } },
    h("div.modal",
      h("div.modal-head", h("h2", "System"), h("p.muted", "Which component is active, and what is used when one is missing.")),
      h("div.modal-body",
        h("table.t", h("tbody",
          row("Supervisor model", s.llm.available ? `${s.llm.model} via Ollama` : `Not available — ${s.llm.reason}. Intent parsing uses rules; answers use templates.`, s.llm.available),
          row("Knowledge graph", `${s.graph} · ${s.graph_stats.nodes} nodes, ${s.graph_stats.relationships} relationships`, s.graph.startsWith("Neo4j")),
          row("Retrieval", `${s.retrieval.note} · ${s.retrieval.chunks} passages`, s.retrieval.semantic),
          row("Catalogue", `${s.catalogue.name} · ${s.catalogue.recipes} recipes · version ${s.catalogue.version}`),
          row("Scaler", s.scaler))),
        h("div.change", { style: { borderRadius: "8px", border: "1px solid var(--border)" } }, s.catalogue.note),
        h("div", h("div.section-label", "Supervisor skills"),
          h("table.t", h("tbody", s.skills.map((k) => h("tr", h("td.mono", k.name), h("td", k.description), h("td", h("span.tag" + (k.side_effect === "none" ? "" : ".warn"), k.side_effect)))))))),
      h("div.modal-foot", h("span.small.muted", "Configure with environment variables; see README."),
        h("button.btn", { onclick: () => overlay.remove() }, "Close"))));
  document.body.append(overlay);
}

function userMenu(anchor) {
  const input = h("input", { type: "text", value: state.user, style: { width: "100%" }, "aria-label": "User id" });
  const pop = h("div.menu", { style: { padding: "10px", width: "240px" } },
    h("div.menu-label", { style: { padding: "0 0 6px" } }, "Pseudonymous user id"), input,
    h("div.btn-row", { style: { marginTop: "8px" } }, h("button.btn.primary.sm", {
      onclick: () => { const v = input.value.trim(); if (v) { safeSet("foodrec.user", v); location.reload(); } },
    }, "Switch")),
    h("div.hint", { style: { marginTop: "6px" } }, "Each id has its own profile, goals and history."));
  document.querySelectorAll(".menu").forEach((m) => m.remove());
  document.body.append(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.top = `${window.scrollY + r.bottom + 4}px`;
  pop.style.left = `${Math.max(8, r.right - 240)}px`;
  input.focus();
  setTimeout(() => document.addEventListener("click", function off(e) {
    if (!pop.contains(e.target)) { pop.remove(); document.removeEventListener("click", off); }
  }), 0);
}

async function route() {
  const key = location.hash.replace("#/", "") || "discover";
  const r = ROUTES[key] || ROUTES.discover;
  current?.cleanup?.();
  renderNav();
  try {
    state.user_state = await api(userPath("/state"));
    renderNav();
    current = r.view();
    clear(main, current);
    window.scrollTo(0, 0);
  } catch (e) {
    clear(main, h("div.empty", h("h3", "Could not load"), h("p", e.message)));
  }
}

async function boot() {
  document.querySelector(".theme-btn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme === "dark" ||
      (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    const t = dark ? "light" : "dark";
    safeSet("foodrec.theme", t);
    applyTheme(t);
  });
  document.querySelector(".user-btn").addEventListener("click", (e) => { e.stopPropagation(); userMenu(e.currentTarget); });
  document.querySelector(".user-btn .uid").textContent = state.user;
  statusPill.addEventListener("click", systemPanel);
  try {
    state.system = await api("/api/system");
  } catch (e) {
    clear(main, h("div.empty", h("h3", "The server is not reachable"), h("p", e.message)));
    return;
  }
  renderStatus();
  on((what) => {
    if (what === "decision" || what === "review") api(userPath("/state")).then((s) => { state.user_state = s; renderNav(); });
  });
  window.addEventListener("hashchange", route);
  state.user_state = await api(userPath("/state"));
  if (!state.user_state.onboarded) {
    clear(main, h("div.empty", { style: { paddingTop: "80px" } }, h("h3", "Welcome"), h("p", "A short setup first.")));
    onboarding(() => { toast("Profile created"); route(); });
  } else route();
}

boot();
