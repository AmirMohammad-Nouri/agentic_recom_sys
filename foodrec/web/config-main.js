// The administrator's page (/config): Monitor and Config, nothing of the user side (no preferences,
// onboarding, recommendations or assistant). Administrators land here after signing in.
// `foodrec config open` adds #token=... to the link: that token is this installation's admin token;
// it is kept for this browser tab and removed from the address bar.
import { api, clear, demoView, demoViewChosen, h, icon, safeGet, safeSet, sessionSet, setCriteria, setDemoView, startDemo, state } from "./lib.js";
import { startTour } from "./tour.js";
import { configView } from "./views/config.js";
import { monitorView } from "./views/monitor.js";

const m = location.hash.match(/token=([^&]+)/);
if (m) {
  sessionSet("foodrec.admin_token", decodeURIComponent(m[1]));
  history.replaceState(null, "", "/config");
}
const theme = safeGet("foodrec.theme");
if (theme) document.documentElement.dataset.theme = theme;
document.querySelector(".theme-btn").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme === "dark" ||
    (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  const t = dark ? "light" : "dark";
  safeSet("foodrec.theme", t);
  document.documentElement.dataset.theme = t;
});

const main = document.querySelector("main");
const pill = document.querySelector(".status-pill");
const nav = document.querySelector(".nav");
const userBtn = document.querySelector(".user-btn");
let signedIn = false;

// ---- the two pages; both stay in the page, so unsaved Config edits survive a look at Monitor ------
const pages = {};
function route() {
  const [page, ...rest] = location.hash.replace("#/", "").split("/");
  const key = page === "monitor" ? "monitor" : "config";
  clear(nav, [["monitor", "Monitor", "pulse"], ["config", "Config", "tool"]].map(([k, label, ic]) =>
    h("a" + (k === key ? ".active" : ""), { href: `#/${k}`, title: label }, icon(ic), h("span.label", label))));
  pages.monitor.classList.toggle("hidden", key !== "monitor");
  pages.config.classList.toggle("hidden", key !== "config");
  if (key === "monitor") pages.monitor.show(rest[0] === "user" && rest[1] ? decodeURIComponent(rest[1]) : null);
  else if (rest[0]) pages.config.setTab(rest[0]);
  window.scrollTo(0, 0);
}

// ---- account menu ------------------------------------------------------------------------------------
function accountMenu(anchor) {
  document.querySelectorAll(".menu").forEach((x) => x.remove());
  const close = () => pop.remove();
  const demo = state.system?.mode === "demo";
  const item = (ic, title, sub, attrs) => h(attrs.href ? "a.menu-item" : "button.menu-item", attrs,
    icon(ic), h("span", h("b", title), h("span.small.muted", { style: { display: "block" } }, sub)));
  const signOut = () => {
    safeSet("foodrec.session", "");
    sessionSet("foodrec.admin_token", "");
    location.replace("/");
  };
  const account = demo ? [h("div.menu-label", { style: { padding: "0 0 6px" } }, "See the app as"),
    h("div.seg", { style: { width: "100%" } }, [["user", "User"], ["admin", "Admin"]].map(([v, t]) => h("button" + (v === demoView() ? ".on" : ""), {
      style: { flex: "1" },
      onclick: async () => { if (v !== demoView()) { await setDemoView(v); location.replace("/"); } },
    }, t))),
    h("div.hint", { style: { marginTop: "6px" } }, "Admin view: Monitor, with sample data, and every setting, read-only.")]
    : [h("div.menu-label", { style: { padding: "0 0 6px" } }, signedIn ? `Signed in as ${state.user}` : "Administrator"),
      h("button.btn.sm", { onclick: signOut }, "Sign out")];
  const pop = h("div.menu.account-menu", { style: { padding: "10px", width: "260px" } },
    item("spark", "Show the tour", "A walk through Monitor and Config", { onclick: () => { close(); startTour({ side: "admin" }); } }),
    item("info", "How FoodRec works", "The complete guide: concepts and every equation", { href: "/guide", target: "_blank", rel: "noopener" }),
    h("div.divider", { style: { margin: "8px 0" } }), account);
  document.body.append(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.top = `${window.scrollY + r.bottom + 4}px`;
  pop.style.left = `${Math.max(8, r.right - 260)}px`;
  setTimeout(() => document.addEventListener("click", function off(e) {
    if (!pop.contains(e.target)) { pop.remove(); document.removeEventListener("click", off); }
  }), 0);
}

// ---- start -------------------------------------------------------------------------------------------
try {
  state.system = await api("/api/system");
  setCriteria(state.system.criteria_specs);
  const demo = state.system.mode === "demo";
  if (demo) {
    if (!demoViewChosen()) {  // the visitor picks a side first
      location.replace("/");
      await new Promise(() => {});
    }
    await startDemo();
    signedIn = demoView() === "admin";
  } else if (state.system.login_required) {
    const sess = await api("/api/session");
    if (sess.user_id) { state.user = sess.user_id; signedIn = true; }
  }
  const llm = state.system.llm;
  clear(pill, h("span.dot." + (llm.available ? "ok" : "warn")), llm.available ? `${llm.provider} · ${llm.model}` : "model unavailable");
  let overview = null;
  let allowed = true;
  try {
    overview = await api("/api/admin/monitor");
  } catch (e) {
    if (e.status !== 401 && e.status !== 403) throw e;
    allowed = false;
  }
  pages.config = configView();
  if (!allowed) {
    // not an administrator (yet): only the sign-in form of the Config view
    userBtn.classList.add("hidden");
    clear(main, pages.config);
  } else {
    state.monitorEmpty = !overview.populations.some((p) => p.people.length);
    pages.monitor = monitorView();
    clear(main, pages.monitor, pages.config);
    if (demo) document.querySelector(".topbar").after(h("div.demo-banner", h("b", "Demo"),
      " · you see the app as the administrator (switch in the account menu, top right) · to use FoodRec for real, clone the project and run it on your computer"));
    userBtn.querySelector(".uid").textContent = demo ? "Admin view" : signedIn ? state.user : "Administrator";
    userBtn.addEventListener("click", (e) => { e.stopPropagation(); accountMenu(e.currentTarget); });
    window.addEventListener("hashchange", route);
    route();
    if (signedIn) {
      state.user_state = await api(`/api/users/${encodeURIComponent(state.user)}/state`);
      setTimeout(() => startTour({ auto: true, side: "admin" }), 700);
    }
  }
} catch (e) {
  clear(main, h("div.empty", h("h3", "The server is not reachable"), h("p", e.message)));
}
