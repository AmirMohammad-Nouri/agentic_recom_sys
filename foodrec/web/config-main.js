// The separate Config page (/config). `foodrec config open` adds #token=... to the link: that
// token is this installation's admin token; it is kept for this browser tab and removed from
// the address bar.
import { api, clear, h, safeGet, safeSet, sessionSet, setCriteria, startDemo, state } from "./lib.js";
import { configView } from "./views/config.js";

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

// "Open the app": the app replaces the Config page in this tab (so only one page stays open,
// and Back does not return to the Config page). Unsaved changes ask first (beforeunload).
document.querySelector(".open-app").addEventListener("click", () => location.replace("/"));

const main = document.querySelector("main");
const pill = document.querySelector(".status-pill");
try {
  state.system = await api("/api/system");
  setCriteria(state.system.criteria_specs);
  if (state.system.mode === "demo") await startDemo();  // the visitor and their admin/user view
  else if (state.system.login_required) {
    const sess = await api("/api/session");
    if (sess.user_id) state.user = sess.user_id;
  }
  const llm = state.system.llm;
  clear(pill, h("span.dot." + (llm.available ? "ok" : "warn")), llm.available ? `${llm.provider} · ${llm.model}` : "model unavailable");
  clear(main, configView());
} catch (e) {
  clear(main, h("div.empty", h("h3", "The server is not reachable"), h("p", e.message)));
}
