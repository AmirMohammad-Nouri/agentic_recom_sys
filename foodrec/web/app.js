// App shell: top bar, router, system status, onboarding gate.
import { api, can, clear, demoView, h, icon, on, safeGet, safeSet, setCriteria, setDemoView, startDemo, state, toast, track, userPath } from "./lib.js";
import { mountDock } from "./dock.js";
import { startTour } from "./tour.js";
import { discoverView } from "./views/discover.js";
import { dishesView } from "./views/dishes.js";
import { insightsView } from "./views/insights.js";
import { activityView, onboarding, profileView } from "./views/profile.js";

// `feature`: the access-role feature a page needs (see the Config page, Access roles).
const ROUTES = {
  discover: { label: "Discover", view: discoverView, icon: "bowl" },
  dishes: { label: "My dishes", view: dishesView, icon: "bookmark", feature: "page_dishes", menu: true },  // in the account menu
  insights: { label: "Insights", view: insightsView, icon: "info", feature: "page_insights" },
  activity: { label: "Activity", view: activityView, icon: "clock", feature: "page_activity" },
  profile: { label: "Profile", view: profileView, icon: "user", anyOf: ["page_profile", "weight_controls", "page_knowledge"] },
};
// Pages that moved: old addresses keep working.
const MOVED = { knowledge: "profile/knowledge" };
const hashRoute = () => {
  const path = location.hash.replace("#/", "") || "discover";
  return MOVED[path] || path;
};
// The Config page is a separate page (/config), opened with `foodrec config open`; the app has no link to it.
const allowed = (k) => ROUTES[k] && (!ROUTES[k].feature || can(ROUTES[k].feature)) && (!ROUTES[k].anyOf || ROUTES[k].anyOf.some(can));

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
  const route = hashRoute().split("/")[0];
  const open = state.user_state?.open_decisions?.length || 0;
  clear(nav, Object.entries(ROUTES).filter(([k, r]) => allowed(k) && !r.menu).map(([k, r]) => h("a" + (k === route ? ".active" : ""), { href: `#/${k}`, title: r.label },
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
      h("div.modal-foot", h("span.small.muted", can("configure") ? "Change these settings with: foodrec config open" : "Configured by the study administrator."),
        h("button.btn", { onclick: () => overlay.remove() }, "Close"))));
  document.body.append(overlay);
}

// The account menu (the user button, top right): My dishes and, for administrators, Config;
// then, by mode: signed-in user and Sign out (local), the Admin/User view switch (demo), or the
// pseudonymous user id (open, development only).
function userMenu(anchor) {
  const close = () => pop.remove();
  const mode = state.system?.mode;
  const item = (ic, title, sub, attrs) => h(attrs.href ? "a.menu-item" : "button.menu-item", attrs,
    icon(ic), h("span", h("b", title), h("span.small.muted", { style: { display: "block" } }, sub)));
  const top = [
    can("page_dishes") ? item("bookmark", "My dishes", "Saved for later, liked and cooked",
      { onclick: () => { close(); location.hash = "#/dishes"; } }) : null,
    can("configure") ? item("tool", "Config", mode === "demo" ? "Every setting, read-only in the demo" : "Model, dataset, criteria, roles, study",
      { href: "/config" }) : null,
    item("spark", "Show the tour", "A short walk through the app", { onclick: () => { close(); startTour(); } }),
    item("info", "How FoodRec works", "The complete guide: concepts and every equation", { href: "/guide", target: "_blank", rel: "noopener" }),
  ].filter(Boolean);
  let account = null;
  let input = null;
  if (joinedByLink()) {
    account = h("div.menu-label", { style: { padding: "0" } }, "You joined with a personal link");
  } else if (mode === "demo") {
    const view = demoView();
    account = [h("div.menu-label", { style: { padding: "0 0 6px" } }, "See the app as"),
      h("div.seg", { style: { width: "100%" } }, [["user", "User"], ["admin", "Admin"]].map(([v, t]) => h("button" + (v === view ? ".on" : ""), {
        style: { flex: "1" },
        onclick: async () => { if (v !== view) { await setDemoView(v); location.reload(); } },
      }, t))),
      h("div.hint", { style: { marginTop: "6px" } }, view === "admin"
        ? "Admin view: everything a user sees, plus the Config page (read-only here)."
        : "User view: what a participant with full access sees.")];
  } else if (state.system?.login_required) {
    account = [h("div.menu-label", { style: { padding: "0 0 6px" } }, `Signed in as ${state.user}`),
      h("button.btn.sm", { onclick: () => { safeSet("foodrec.session", ""); location.reload(); } }, "Sign out")];
  } else if (can("switch_user")) {
    input = h("input", { type: "text", value: state.user, style: { width: "100%" }, "aria-label": "User id",
      onkeydown: (e) => { if (e.key === "Enter") go(); } });
    const go = () => { const v = input.value.trim(); if (v) { safeSet("foodrec.user", v); location.reload(); } };
    account = [h("div.menu-label", { style: { padding: "0 0 6px" } }, "Pseudonymous user id"), input,
      h("div.btn-row", { style: { marginTop: "8px" } }, h("button.btn.primary.sm", { onclick: go }, "Switch")),
      h("div.hint", { style: { marginTop: "6px" } }, "Each id has its own profile, goals and history.")];
  } else {
    account = h("div.menu-label", { style: { padding: "0" } }, `User ${state.user}`);
  }
  const pop = h("div.menu.account-menu", { style: { padding: "10px", width: "260px" } },
    top, top.length ? h("div.divider", { style: { margin: "8px 0" } }) : null, account);
  document.querySelectorAll(".menu").forEach((m) => m.remove());
  document.body.append(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.top = `${window.scrollY + r.bottom + 4}px`;
  pop.style.left = `${Math.max(8, r.right - 260)}px`;
  input?.focus();
  setTimeout(() => document.addEventListener("click", function off(e) {
    if (!pop.contains(e.target)) { pop.remove(); document.removeEventListener("click", off); }
  }), 0);
}

const joinedByLink = () => !!state.user && safeGet("foodrec.joined") === state.user;

function demoBanner() {
  const b = h("div.demo-banner", h("b", "Demo"), joinedByLink()
    ? " · you joined with a personal link · data is reset regularly"
    : [` · you see the app as ${demoView() === "admin" ? "the administrator" : "a user"} `,
      "(switch in the account menu, top right) · data is reset regularly · to use FoodRec for real, clone the project and run it on your computer"]);
  document.querySelector(".topbar").after(b);
}

function loginScreen(system) {
  const user = h("input", { type: "text", autocomplete: "username", placeholder: "Username", style: { width: "100%" } });
  const pw = h("input", { type: "password", autocomplete: "current-password", placeholder: "Password", style: { width: "100%" } });
  const msg = h("div.small", { style: { color: "var(--danger)", minHeight: "18px" } });
  async function go(e) {
    e?.preventDefault();
    try {
      const out = await api("/api/login", { method: "POST", body: { username: user.value.trim(), password: pw.value } });
      safeSet("foodrec.session", out.token);
      safeSet("foodrec.user", out.user_id);
      safeSet("foodrec.joined", "");
      location.reload();
    } catch (err) { msg.textContent = err.message; }
  }
  clear(main, h("div", { style: { maxWidth: "360px", margin: "80px auto" } },
    h("section.card", h("div.card-body.stack",
      h("h2", system.study?.name || "Sign in"),
      system.study?.description ? h("p.muted", system.study.description) : h("p.muted", "Use the username and password you were given."),
      h("form.stack", { onsubmit: go }, user, pw, msg, h("button.btn.primary", { type: "submit" }, "Sign in"))))));
  user.focus();
}

async function route() {
  if (MOVED[location.hash.replace("#/", "")]) location.replace(`#/${hashRoute()}`);
  let key = hashRoute().split("/")[0];
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

// A personal link (/join/<token>) is the way in: exchange it, then forget it from the address bar.
async function joinFromLink() {
  const m = location.pathname.match(/^\/join\/([^/]+)$/);
  if (!m) return true;
  try {
    const out = await api("/api/join", { method: "POST", body: { token: decodeURIComponent(m[1]) } });
    if (out.token) safeSet("foodrec.session", out.token);
    if (out.mode === "demo") { safeSet("foodrec.visitor", out.user_id); safeSet("foodrec.view", "user"); }
    safeSet("foodrec.joined", out.user_id);
    safeSet("foodrec.user", out.user_id);
    state.user = out.user_id;
    history.replaceState(null, "", "/");
    return true;
  } catch (e) {
    history.replaceState(null, "", "/");
    clear(main, h("div.empty", { style: { paddingTop: "80px" } }, h("h3", "This link does not work"), h("p", e.message),
      h("p.muted", "Ask the person who sent it for a new one.")));
    return false;
  }
}

async function boot() {
  if (!await joinFromLink()) return;
  document.querySelector(".theme-btn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme === "dark" ||
      (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    const t = dark ? "light" : "dark";
    safeSet("foodrec.theme", t);
    applyTheme(t);
  });
  document.querySelector(".user-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    userMenu(e.currentTarget);
  });
  document.querySelector(".user-btn .uid").textContent = state.user;
  statusPill.addEventListener("click", systemPanel);
  try {
    state.system = await api("/api/system");
    setCriteria(state.system.criteria_specs);
    if (state.system.mode === "demo") {
      await startDemo();
      document.querySelector(".user-btn .uid").textContent = demoView() === "admin" ? "Admin view" : "User view";
      demoBanner();
    } else if (state.system.login_required) {
      const sess = await api("/api/session");
      if (!sess.user_id && safeGet("foodrec.joined")) {
        // the session of a personal link ended: the administrator removed or deactivated it
        document.querySelector(".user-btn .uid").textContent = "";
        clear(main, h("div.empty", { style: { paddingTop: "80px" } }, h("h3", "Your access has ended"),
          h("p", "The link you used was deactivated or removed. Ask the person who sent it."),
          h("button.btn", { onclick: () => { safeSet("foodrec.joined", ""); location.reload(); } }, "Sign in with an account instead")));
        return;
      }
      if (!sess.user_id) { document.querySelector(".user-btn .uid").textContent = "sign in"; return loginScreen(state.system); }
      state.user = sess.user_id;
      document.querySelector(".user-btn .uid").textContent = state.user;
    }
    state.user_state = await api(userPath("/state"));
  } catch (e) {
    clear(main, h("div.empty", { style: { paddingTop: "80px" } }, h("h3", e.status === 403 ? "Your access has ended" : "The server is not reachable"), h("p", e.message)));
    return;
  }
  renderStatus();
  document.querySelector(".user-btn").title = "Account";
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
      setTimeout(() => startTour({ auto: true }), 900);
    });
  } else {
    track("session_start", { role: state.user_state.access?.role });
    route();
    setTimeout(() => startTour({ auto: true }), 900);
  }
}

boot();
