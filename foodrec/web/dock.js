// The assistant, docked on every page: chat with the supervisor, plus its activity feed and
// open questions. It stays open across pages; recipe cards can send questions to it.
import { api, ago, clear, emit, f2, grade, h, human, icon, on, richText, safeGet, safeSet, state, stream, toast, userPath } from "./lib.js";
import { changeSummary, compareTable, decisionCard, personalFits, scoreBreakdown } from "./components.js";
import { traceView } from "./trace.js";

const SUGGEST = ["Something easy with few ingredients", "Why is #1 first?", "Compare #1 and #2",
  "What if I cared more about sustainability?", "What do you know about me?",
  "How is healthiness calculated?", "What did I reject this week?"];

const feed = []; // supervisor activity for this session: {title, steps, ts}
export function pushFeed(item) { feed.push(item); renderFeed?.(); }
let renderFeed = null;

function attachments(a) {
  const out = [];
  const res = a.result;
  if (res?.results?.length) {
    out.push(h("div.mini-recipes", res.results.map((r) => h("div.mini",
      h("div.row.between", h("span.t", `#${r.final_rank} ${r.title}`), grade(r.health_grade)),
      h("div.small.muted", `${r.cuisine === "Other" ? human(r.meal) : r.cuisine} · ${r.n_ingredients} ingredients · utility ${f2(r.utility)}`),
      h("div.small", { style: { marginTop: "4px", color: "var(--text-2)" } }, personalFits(r)[0] || r.reasons.ranked_here[0] || "")))));
    if (res.config.request_filters?.length) {
      out.push(h("div.row.wrap", res.config.request_filters.map((f) => h("span.tag.info", { title: "Applied to this request only; not saved" }, f.label)),
        h("span.small.muted", "request-only filters")));
    }
  }
  if (a.comparison?.rows) {
    const c = a.comparison;
    out.push(compareTable({ title: c.a.title, utility: c.a.utility ?? NaN, scores: Object.fromEntries(c.rows.map((r) => [r.criterion, r.a])) },
      { title: c.b.title, utility: c.b.utility ?? NaN, scores: Object.fromEntries(c.rows.map((r) => [r.criterion, r.b])) }));
  }
  if (a.what_if?.comparison) {
    const titles = {};
    for (const r of [...a.what_if.alternative.results, ...a.what_if.base.results]) titles[r.recipe_id] = r.title;
    out.push(h("div.card", { style: { overflow: "hidden" } }, changeSummary(a.what_if.comparison, titles)));
  }
  if (a.breakdown) {
    out.push(h("details.attach-details", h("summary", "How its three scores are calculated"), scoreBreakdown(a.breakdown)));
  }
  if (a.hits?.length) {
    out.push(h("details.attach-details", h("summary", `${a.hits.length} retrieved passages`),
      h("div.stack.tight", { style: { marginTop: "6px" } }, a.hits.map((x) => h("div.small",
        h("span.tag", x.kind), " ", h("b", x.title), h("span.muted.mono", `  bm25 #${x.bm25_rank ?? "–"} · vector #${x.dense_rank ?? "–"} · rrf ${x.rrf}`),
        h("div.muted", x.text.slice(0, 220) + (x.text.length > 220 ? "…" : "")))))));
  }
  for (const d of a.decisions || []) out.push(decisionCard(d));
  return out.length ? h("div.attach", out) : null;
}

function assistantMsg(m, tv) {
  return h("div.msg-assistant",
    h("div.who", h("span.avatar", icon("spark")), "Assistant", m?.ts ? h("span", "· " + new Date(m.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })) : null),
    tv ? tv.el : null,
    m ? richText(m.text) : null,
    m ? attachments(m.attachments || {}) : null);
}

export function mountDock() {
  const log = h("div.chat-log");
  const input = h("textarea", { placeholder: "Ask about recipes, scores, your preferences…", rows: 1, "aria-label": "Message" });
  const think = h("input", { type: "checkbox", checked: safeGet("foodrec.think") === "1" });
  think.addEventListener("change", () => safeSet("foodrec.think", think.checked ? "1" : "0"));
  const sendBtn = h("button.send-btn", { onclick: () => send(), title: "Send (Enter)", "aria-label": "Send" }, icon("send"));
  let busy = false;
  let loaded = false;

  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(160, input.scrollHeight) + "px"; });

  const scrollEnd = () => { chatPane.scrollTop = chatPane.scrollHeight; };

  async function load() {
    loaded = true;
    const msgs = await api(userPath("/messages"));
    clear(log, msgs.length ? null : intro());
    for (const m of msgs.slice(-30)) {
      if (m.role === "user") log.append(h("div.msg-user", m.text));
      else {
        const tv = traceView({ steps: m.trace });
        tv.done({ elapsed_ms: m.trace.length ? m.trace[m.trace.length - 1].t_ms : 0 });
        log.append(assistantMsg(m, tv));
      }
    }
    requestAnimationFrame(scrollEnd);
  }

  function intro() {
    return h("div.dock-intro",
      h("div.dock-intro-mark", icon("spark")),
      h("h3", "Ask the assistant"),
      h("p", "It plans which tools to use, shows every step while it works, and only states what the recorded evidence supports."),
      h("div.suggestions", SUGGEST.map((s) => h("button.chip", { onclick: () => send(s) }, s))));
  }

  async function send(preset) {
    const text = (preset ?? input.value).trim();
    if (!text || busy) return;
    if (!loaded) await load();
    busy = true; sendBtn.disabled = true;
    input.value = ""; input.style.height = "";
    if (log.querySelector(".dock-intro")) clear(log);
    log.append(h("div.msg-user", text));
    const tv = traceView({ live: true });
    const slot = assistantMsg(null, tv);
    log.append(slot);
    scrollEnd();
    try {
      await stream(userPath("/chat"), { text, think: think.checked }, (ev) => {
        if (ev.type === "step") tv.add(ev.step);
        else if (ev.type === "step_update") tv.update(ev.step);
        else if (ev.type === "model_token") tv.token(ev.id, ev.text);
        else if (ev.type === "done") {
          tv.done({ elapsed_ms: ev.elapsed_ms });
          const m = ev.message;
          slot.append(richText(m.text));
          const att = attachments(m.attachments || {});
          if (att) slot.append(att);
          if (m.attachments?.result) { state.lastResult = null; emit("results_changed"); }
          if (m.attachments?.decisions?.length) loadDecisions();
        } else if (ev.type === "error") {
          tv.done({});
          slot.append(h("div.small", { style: { color: "var(--danger)" } }, "Something failed: " + ev.error));
        }
        scrollEnd();
      });
    } catch (e) { toast(e.message, "err"); tv.done({}); }
    busy = false; sendBtn.disabled = false;
    input.focus();
  }

  // ---- supervisor activity tab ------------------------------------------------------
  const decisionsBox = h("div.stack");
  const feedBox = h("div.feed");
  async function loadDecisions() {
    const open = await api(userPath("/decisions?status=open"));
    clear(decisionsBox, open.length ? open.map((d) => decisionCard(d, { onResolved: loadDecisions }))
      : h("div.small.muted", "No open questions. The supervisor compares your goal with your choices after every piece of feedback."));
    setBadge(open.length);
  }
  renderFeed = () => clear(feedBox, feed.length ? feed.slice().reverse().slice(0, 15).map((f) =>
    h("div.feed-item", h("div.when", ago(f.ts)), traceView({ steps: f.steps || [], title: f.title }).el))
    : h("div.small.muted.feed-empty", "How each list was computed, and what the supervisor concluded after your feedback, appears here."));
  async function reviewNow() {
    const rv = await api(userPath("/review"), { method: "POST" });
    pushFeed({ title: `Review: ${human(rv.status)}`, steps: rv.trace, ts: Date.now() / 1000 });
    loadDecisions();
  }

  // ---- shell --------------------------------------------------------------------------
  const chatPane = h("div.dock-pane.chat-pane", log);
  const actPane = h("div.dock-pane.hidden",
    h("div.dock-section", h("div.row.between", h("div.section-label", "Open questions"),
      h("button.btn.ghost.sm", { onclick: reviewNow, title: "Run the goal-vs-behaviour check now" }, "Review now")), decisionsBox),
    h("div.dock-section", h("div.section-label", "Supervisor activity"), feedBox));
  const badge = h("span.count.hidden");
  const setBadge = (n) => { badge.textContent = n; badge.classList.toggle("hidden", !n); fabBadge.textContent = n; fabBadge.classList.toggle("hidden", !n); };
  const tabs = [h("button.on", { onclick: () => tab(0) }, "Chat"), h("button", { onclick: () => tab(1) }, "Supervisor", badge)];
  function tab(i) {
    tabs.forEach((t, j) => t.classList.toggle("on", i === j));
    chatPane.classList.toggle("hidden", i !== 0);
    actPane.classList.toggle("hidden", i !== 1);
    composer.classList.toggle("hidden", i !== 0);
    if (i === 1) loadDecisions();
  }
  const llm = state.system?.llm || {};
  const composer = h("div.dock-composer",
    h("div.composer-box", input, sendBtn),
    h("div.composer-foot",
      h("span.row.small.muted", h("span.dot." + (llm.available ? "ok" : "warn")), llm.available ? llm.model : "rule-based"),
      h("label.switch", { title: "Stream the model's own reasoning into the timeline (slower; shown as unverified)" }, think, "Show reasoning"),
      h("span.grow"),
      h("button.btn.ghost.sm", { onclick: async () => { await api(userPath("/messages"), { method: "DELETE" }); load(); } }, "Clear")));
  const dock = h("aside.dock", { "aria-label": "Assistant" },
    h("div.dock-head",
      h("span.avatar", icon("spark")), h("b", "Assistant"),
      h("div.seg.dock-tabs", tabs), h("span.grow"),
      h("button.icon-btn", { onclick: () => toggle(false), title: "Close (the assistant keeps its history)", "aria-label": "Close assistant" }, icon("close"))),
    chatPane, actPane, composer);
  const fabBadge = h("span.count.hidden");
  const fab = h("button.dock-fab", { onclick: () => toggle(true), title: "Open the assistant", "aria-label": "Open the assistant" }, icon("spark"), h("span", "Ask"), fabBadge);
  document.body.append(dock, fab);

  function toggle(open) {
    document.body.classList.toggle("dock-open", open);
    safeSet("foodrec.dock", open ? "1" : "0");
    if (open) { if (!loaded) load(); setTimeout(() => input.focus(), 50); }
  }

  on((what, data) => {
    if (what === "ask") { toggle(true); tab(0); send(data); }
    if (what === "feed") pushFeed(data);
    if (what === "review") {
      pushFeed({ title: `After you ${data.type === "choose" ? "chose" : data.type + "d"} ${data.recipe}: ${human(data.status)}`, steps: data.trace, ts: data.ts });
      loadDecisions();
    }
    if (what === "decision") loadDecisions();
  });

  const topBtn = document.querySelector(".dock-toggle");
  topBtn?.addEventListener("click", () => toggle(!document.body.classList.contains("dock-open")));
  renderFeed();
  loadDecisions();
  const saved = safeGet("foodrec.dock");
  toggle(window.innerWidth >= 1100 && (saved === null || saved === "1"));
  return { toggle, send };
}
