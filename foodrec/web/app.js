// App shell: top bar, router, system status, onboarding gate.
import { api, can, clear, h, icon, on, safeGet, safeSet, state, toast, track, userPath } from "./lib.js";
import { mountDock } from "./dock.js";
import { configView } from "./views/config.js";
import { discoverView } from "./views/discover.js";
import { insightsView } from "./views/insights.js";
import { knowledgeView } from "./views/knowledge.js";
import { activityView, onboarding, profileView } from "./views/profile.js";

// `feature`: the access-role feature a page needs (see the Config page, Access roles).
const ROUTES = {
  discover: { label: "Discover", view: discoverView, icon: "bowl" },
  insights: { label: "Insights", view: insightsView, icon: "info", feature: "page_insights" },
  knowledge: { label: "Knowledge", view: knowledgeView, icon: "graph", feature: "page_knowledge" },
  activity: { label: "Activity", view: activityView, icon: "clock", feature: "page_activity" },
  profile: { label: "Profile", view: profileView, icon: "user", feature: "page_profile" },
  config: { label: "Config", view: configView, icon: "tool", feature: "configure" },
};
const allowed = (k) => ROUTES[k] && (!ROUTES[k].feature || can(ROUTES[k].feature));

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
  clear(nav, Object.entries(ROUTES).filter(([k]) => allowed(k)).map(([k, r]) => h("a" + (k === route ? ".active" : ""), { href: `#/${k}`, title: r.label },
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
          row("Catalogue", `${s.catalogue.name} · ${s.catalogue.recipes} recipes · version ${s.catalogue.version} · ${s.catalogue.import === "imported" ? "imported into the database at this start" : "loaded from the database"}`),
          row("Labels", `cuisine, meal and food groups by ${s.catalogue.coverage.labeller}`, s.catalogue.coverage.labeller !== "rules"),
          row("Scoring", s.scaler),
          row("Config file", `${s.config.path || "none (built-in defaults)"} · version ${s.config.version}` +
            (s.config.restart_pending?.length ? ` · ${s.config.restart_pending.length} change(s) wait for a restart` : ""), !s.config.restart_pending?.length),
          row("Your access role", `${state.user_state?.access?.label || "–"} (${state.user_state?.access?.role || "–"})`))),
        h("div.change", { style: { borderRadius: "8px", border: "1px solid var(--border)" } }, s.catalogue.note),
        h("div", h("div.section-label", "Supervisor skills"),
          h("table.t", h("tbody", s.skills.map((k) => h("tr", h("td.mono", k.name), h("td", k.description), h("td", h("span.tag" + (k.side_effect === "none" ? "" : ".warn"), k.side_effect)))))))),
      h("div.modal-foot", h("span.small.muted", can("configure") ? h("a", { href: "#/config", onclick: () => overlay.remove() }, "Open the Config page") : "Configured by the study administrator."),
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
  let key = location.hash.replace("#/", "") || "discover";
  if (!allowed(key)) key = "discover";
  const r = ROUTES[key];
  current?.cleanup?.();
  renderNav();
  try {
    state.user_state = await api(userPath("/state"));
    renderNav();
    current = allowed(key) ? r.view() : ROUTES.discover.view();
    clear(main, current);
    window.scrollTo(0, 0);
    track("page_view", { page: key });
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
  document.querySelector(".user-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    if (can("switch_user")) userMenu(e.currentTarget);
  });
  document.querySelector(".user-btn .uid").textContent = state.user;
  statusPill.addEventListener("click", systemPanel);
  try {
    [state.system, state.user_state] = await Promise.all([api("/api/system"), api(userPath("/state"))]);
  } catch (e) {
    clear(main, h("div.empty", h("h3", "The server is not reachable"), h("p", e.message)));
    return;
  }
  renderStatus();
  document.querySelector(".user-btn").title = can("switch_user") ? "Switch user" : `Signed in as ${state.user}`;
  if (state.system.study?.name) document.title = `${state.system.study.name} · FoodRec`;
  // The dock waits for onboarding: a study may assign a role without the assistant at that point.
  const dockToggle = document.querySelector(".dock-toggle");
  const startDock = () => {
    dockToggle?.classList.toggle("hidden", !can("assistant"));
    if (can("assistant") && !document.querySelector(".dock")) mountDock();
  };
  if (state.user_state.onboarded) startDock();
  else dockToggle?.classList.add("hidden");
  on((what) => {
    if (what === "decision" || what === "review") api(userPath("/state")).then((s) => { state.user_state = s; renderNav(); });
    if (what === "redirect_onboarding") location.reload();
  });
  window.addEventListener("hashchange", route);
  if (!state.user_state.onboarded && !state.user_state.registration?.allowed) {
    clear(main, h("div.empty", { style: { paddingTop: "80px" } }, h("h3", state.system.study?.name || "Registration"),
      h("p", state.user_state.registration?.reason || "This user id cannot start."),
      can("switch_user") ? h("button.btn", { onclick: (e) => { e.stopPropagation(); userMenu(document.querySelector(".user-btn")); } }, "Use another id") : null));
    return;
  }
  if (!state.user_state.onboarded) {
    clear(main, h("div.empty", { style: { paddingTop: "80px" } }, h("h3", "Welcome"), h("p", "A short setup first.")));
    onboarding(async () => {
      toast("Profile created");
      // the role may have been assigned at onboarding (study assignment)
      state.user_state = await api(userPath("/state"));
      track("session_start", { role: state.user_state.access?.role });
      startDock();
      route();
    });
  } else {
    track("session_start", { role: state.user_state.access?.role });
    route();
  }
}

boot();
