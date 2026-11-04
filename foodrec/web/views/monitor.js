// Monitor (the administrator's page): the populations, the people in them, and each person's
// interactions and logs. In the demo, sample populations stand in for a real study.
import { ago, api, clear, download, h, icon, state, toast } from "../lib.js";
import { openGuide } from "./config.js";

const STATE = {
  invited: ["Invited", ""], started: ["Started", "info"], in_progress: ["In progress", "accent"],
  finished: ["Finished", "accent"], deactivated: ["Deactivated", "warn"],
};
const REACTION = { like: "Like", save: "Save", choose: "I'll cook this", reject: "Not for me" };
const LOG_KINDS = [["all", "Everything"], ["event", "Actions"], ["recommendation", "Lists shown"], ["decision", "Supervisor"], ["message", "Conversation"]];
const REACT_ORDER = ["like", "save", "choose", "reject"];

const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "–");
const words = (s) => String(s ?? "").replace(/_/g, " ");
const tagFor = (st) => h("span.tag" + (STATE[st][1] ? "." + STATE[st][1] : ""), STATE[st][0]);
const sampleTag = () => h("span.tag.info", { title: "Made up for the demo; no real person" }, "Sample");
const tile = (value, label) => h("div.mon-kpi", h("b", String(value)), h("span", label));
const num = (value, attrs) => h("td.num", attrs || {}, String(value));
const empty = (title, text) => h("div.empty", h("h3", title), h("p", text));
const REACTED = { like: "liked", save: "saved", choose: "to cook", reject: "not for me" };
const counts = (r) => REACT_ORDER.filter((k) => r[k]).map((k) => `${r[k]} ${REACTED[k]}`).join(" · ");
const LOG_LABEL = { event: "action", recommendation: "list shown", decision: "supervisor", message: "message" };

function progress(p) {
  if (!p.cycle) return h("span.small.muted", "Free list");
  const share = p.cycle.total ? Math.min(1, p.cycle.answered / p.cycle.total) : 0;
  return h("div.mon-progress", h("div.cycle-progress", h("span", { style: { width: `${Math.round(share * 100)}%` } })),
    h("span.small.muted", `${p.cycle.answered}/${p.cycle.total}`));
}

const exportJson = (users) => download(`/api/export?format=json&users=${encodeURIComponent(users)}`)
  .then(() => toast("Export downloaded")).catch((e) => toast(e.message, "err"));

// ---- overview ---------------------------------------------------------------------------------
function overview(data) {
  const pops = data.populations;
  const sum = (k) => pops.reduce((a, p) => a + p[k], 0);
  const compare = h("section.card.mon-compare", h("div.card-head", h("h2", "Compare populations")),
    h("div.mon-table", h("table.t",
      h("thead", h("tr", h("th", "Population"), h("th.num", "People"), h("th.num", "Started"), h("th.num", "Finished"),
        h("th.num", "Reactions"), h("th.num", { title: "Reactions per person who started" }, "Per person"),
        h("th.num", { title: "Share of reactions that were Not for me" }, "Rejected"), h("th.num", "Chat turns"),
        h("th.num", { title: "Supervisor questions waiting for an answer" }, "Open questions"), h("th", "Last active"))),
      h("tbody", pops.map((p, i) => h("tr.mon-link", { onclick: () => document.getElementById(`mon-pop-${i}`)?.scrollIntoView({ behavior: "smooth" }) },
        h("td", h("div.row", { style: { gap: "6px" } }, h("b", p.name), p.sample ? sampleTag() : null)),
        num(p.size), num(p.started), num(p.finished), num(p.reactions_total), num(p.reactions_per_person),
        h("td.num", p.reactions_total ? `${Math.round(100 * (p.reactions.reject || 0) / p.reactions_total)}%` : "–"),
        num(p.chat_turns), num(p.decisions_open),
        h("td.small.muted", p.last_active ? ago(p.last_active) : "–")))))));
  const groups = pops.map((p, i) => h("section.card.mon-pop", { id: `mon-pop-${i}` },
    h("div.card-head", h("div", h("div.mon-pop-head", h("h2", p.name), p.sample ? sampleTag() : null,
      Object.entries(p.roles).map(([r, n]) => h("span.tag", `${r}: ${n}`))),
    p.description ? h("div.small.muted", p.description) : null),
    h("span.grow"),
    !p.sample && p.people.some((x) => x.joined) ? h("button.btn.sm", { onclick: () => exportJson(p.people.filter((x) => x.joined).map((x) => x.user_id).join(",")) }, "Export as JSON") : null),
    h("div.mon-table", h("table.t",
      h("thead", h("tr", h("th", "Person"), h("th", "Role"), h("th", "Status"), h("th", "Progress"), h("th", "Reactions"), h("th.num", "Chat turns"), h("th", "Last active"), h("th", ""))),
      h("tbody", p.people.map((x) => h("tr.mon-link.mon-user", { tabIndex: 0, role: "link", "aria-label": `Open ${x.label}`,
        onclick: () => { location.hash = `#/monitor/user/${encodeURIComponent(x.user_id)}`; },
        onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/monitor/user/${encodeURIComponent(x.user_id)}`; } },
      h("td.mono", x.label), h("td", x.role_label), h("td", tagFor(x.state)), h("td", progress(x)),
      h("td.small", counts(x.reactions) || h("span.muted", "–")), num(x.chat_turns),
      h("td.small.muted", x.last_active ? ago(x.last_active) : "–"), h("td", icon("chev")))))))));
  return h("div",
    h("div.mon-kpis.mon-totals", tile(pops.length, "Populations"), tile(sum("size"), "People"), tile(sum("started"), "Started"),
      tile(sum("finished"), "Finished"), tile(sum("reactions_total"), "Reactions")),
    compare, groups);
}

// ---- one person ---------------------------------------------------------------------------------
function table(heads, rows, none) {
  return rows.length ? h("div.mon-table", h("table.t", h("thead", h("tr", heads.map((x) => h("th", x)))), h("tbody", rows))) : empty(none, "");
}

function detailTabs(d) {
  const logBox = h("div");
  const kind = h("select", { "aria-label": "Show in the log", onchange: () => paintLog() }, LOG_KINDS.map(([v, t]) => h("option", { value: v }, t)));
  const paintLog = () => {
    const rows = d.log.filter((x) => kind.value === "all" || x.kind === kind.value).slice(0, 500);
    clear(logBox, table(["Time", "Kind", "What"], rows.map((x) => h("tr", h("td.small.muted", when(x.ts)),
      h("td", h("span.tag", LOG_LABEL[x.kind] || words(x.kind))), h("td.mon-wrap.small", x.text))), "Nothing here yet"));
  };
  paintLog();
  const taste = d.criteria?.length ? table(["Criterion", "Saved weight", "Shown by choices", "Gap"], d.criteria.map((c) => h("tr", h("td", c.label),
    h("td", String(c.saved_weight ?? "–")), h("td", String(c.observed_weight ?? "–")), h("td", String(c.gap ?? "–")))), "") : null;
  const tabs = [
    ["interactions", `Interactions (${d.interactions.length})`, () => table(["Time", "Reaction", "Dish", "Position", "Reason"],
      d.interactions.map((x) => h("tr", h("td.small.muted", when(x.ts)), h("td", REACTION[x.type] || words(x.type)), h("td.mon-wrap", x.title || "–"),
        h("td", x.rank_in_list ? `#${x.rank_in_list}` : "–"), h("td.small", words(x.reason) || "–"))), "No reactions yet")],
    ["log", `Log (${d.log.length})`, () => h("div.stack.tight", h("label.row", { style: { gap: "8px" } }, h("span.small.muted", "Show"), kind), logBox)],
    ["conversation", `Conversation (${d.conversation.length})`, () => (d.conversation.length ? h("div.mon-chat", d.conversation.map((m) =>
      h("div.mon-msg." + (m.role === "user" ? "user" : "agent"), h("time", `${m.role === "user" ? d.label : "Assistant"} · ${when(m.ts)}`), m.text)))
      : empty("No conversation", "This person has not used the assistant."))],
    ["supervisor", `Supervisor (${d.decisions.length})`, () => table(["Time", "Asked or proposed", "Why", "Status"],
      d.decisions.map((x) => h("tr", h("td.small.muted", when(x.ts)), h("td.mon-wrap", x.title), h("td.mon-wrap.small", typeof x.explanation === "string" ? x.explanation : "–"),
        h("td", h("span.tag" + (x.status === "open" ? ".warn" : ""), words(x.status))))), "The supervisor has not asked anything")],
    ["values", "Values", () => (d.goal ? h("div.stack", h("div.small", h("b", d.goal.name), ` · version ${d.goal.version} · diversity ${d.goal.diversity}`), taste)
      : empty("No values yet", "This person has not finished setting up."))],
  ];
  let cur = "interactions";
  const body = h("div");
  const seg = h("div.seg.mon-tabs");
  const paint = () => {
    clear(seg, tabs.map(([k, label]) => h(`button.mon-tab-${k}` + (k === cur ? ".on" : ""), { onclick: () => { cur = k; paint(); } }, label)));
    clear(body, tabs.find(([k]) => k === cur)[2]());
  };
  paint();
  return h("section.card.mon-record", h("div.card-body", seg, body));
}

function person(d) {
  return h("div",
    h("p", h("a", { href: "#/monitor" }, "← All populations")),
    h("div.page-head.mon-person", h("div",
      h("h1", d.label),
      h("div.row.wrap", { style: { gap: "6px", marginTop: "4px" } }, tagFor(d.state), d.sample ? sampleTag() : null,
        h("span.tag", d.population), h("span.tag", d.role_label))),
    !d.sample && d.joined ? h("button.btn.sm", { onclick: () => exportJson(d.user_id) }, "Export as JSON") : null),
    h("div.mon-kpis", tile(d.reactions_total, "Reactions"), tile(d.lists, "Lists shown"), tile(d.chat_turns, "Chat turns"),
      tile(d.decisions.length, "Supervisor questions"), tile(d.last_active ? ago(d.last_active) : "–", "Last active")),
    d.cycle ? h("section.card", { style: { marginBottom: "16px" } }, h("div.card-body.stack.tight",
      h("div.small.muted", d.cycle.finished ? "Finished the meal cycle" : "Meal cycle progress"), progress(d))) : null,
    detailTabs(d));
}

// ---- the page -------------------------------------------------------------------------------------
export function monitorView() {
  const body = h("div", h("section.card", h("div.card-body", h("div.skeleton", { style: { height: "160px" } }))));
  const actions = h("div.btn-row");
  let seq = 0;

  async function show(uid) {
    const mine = ++seq;
    clear(actions, uid ? null : [
      h("button.btn.ghost.sm.guide-btn", { onclick: () => openGuide("monitor") }, icon("info"), "Guide"),
      h("button.btn.sm", { onclick: () => show(null) }, icon("reset"), "Refresh"),
      h("a.btn.sm.primary", { href: "#/config/users" }, icon("plus"), state.system?.mode === "demo" ? "Create links" : "Create a population")]);
    head.classList.toggle("hidden", !!uid);
    try {
      const data = await api(uid ? `/api/admin/monitor/users/${encodeURIComponent(uid)}` : "/api/admin/monitor");
      if (mine !== seq) return;
      if (uid) return clear(body, person(data));
      clear(body,
        data.has_sample ? h("div.change", { style: { marginBottom: "16px" } }, h("b", "Sample data. "),
          "The populations marked Sample are made up, so you can see what monitoring looks like. Links you create appear here with what those people really do.") : null,
        data.populations.length ? overview(data) : h("section.card", empty("No population yet",
          "Create one under Config, Users & study. People appear here with their activity once they are added.")));
    } catch (e) {
      if (mine === seq) clear(body, empty("Could not load", e.message));
    }
  }

  const head = h("div.page-head", h("div", h("h1", "Monitor"),
    h("p", "How each population is doing, and what each person did. Open a person to read their interactions and logs.")), actions);
  const root = h("div", head, body);
  root.show = show;
  return root;
}
