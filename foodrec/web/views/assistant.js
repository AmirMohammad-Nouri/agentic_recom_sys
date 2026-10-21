// Assistant: chat with the supervisor; its work streams live above each answer.
import { api, clear, eur, f2, grade, h, icon, richText, safeGet, safeSet, state, stream, toast, userPath } from "../lib.js";
import { decisionCard, compareTable, changeSummary, personalFits } from "../components.js";
import { traceView } from "../trace.js";

const SUGGEST = ["Something quick and high in protein", "Why is #1 first?", "Compare #1 and #2",
  "What if I cared more about sustainability?", "Why do you think price matters to me?",
  "What did I reject this week?", "How is healthiness calculated?", "Which recipes contain chickpeas?"];

function attachments(a) {
  const out = [];
  const res = a.result;
  if (res?.results?.length) {
    out.push(h("div.mini-recipes", res.results.map((r) => h("div.mini",
      h("div.row.between", h("span.t", `#${r.final_rank} ${r.title}`), grade(r.nutri_grade)),
      h("div.small.muted", `${r.cuisine} · ${r.prep_minutes} min · ${eur(r.cost_eur)} · utility ${f2(r.utility)}`),
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
  if (a.hits?.length) {
    out.push(h("details.step-evidence", h("summary", `${a.hits.length} retrieved passages`),
      h("div.stack.tight", { style: { marginTop: "6px" } }, a.hits.map((x) => h("div.small",
        h("span.tag", x.kind), " ", h("b", x.title), h("span.muted.mono", `  bm25 #${x.bm25_rank ?? "–"} · vector #${x.dense_rank ?? "–"} · rrf ${x.rrf}`),
        h("div.muted", x.text.slice(0, 220) + (x.text.length > 220 ? "…" : "")))))));
  }
  for (const d of a.decisions || []) out.push(decisionCard(d));
  return out.length ? h("div.attach", out) : null;
}

function assistantMsg(m, tv) {
  return h("div.msg-assistant",
    h("div.who", h("span.avatar", icon("spark")), "Supervisor", m?.ts ? h("span", "· " + new Date(m.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })) : null),
    tv ? tv.el : null,
    m ? richText(m.text) : null,
    m ? attachments(m.attachments || {}) : null);
}

export function assistantView() {
  const log = h("div.chat-log");
  const input = h("textarea", { placeholder: "Ask for recipes, explanations, comparisons or your history…", rows: 2, "aria-label": "Message" });
  const think = h("input", { type: "checkbox", checked: safeGet("foodrec.think") === "1" });
  think.addEventListener("change", () => safeSet("foodrec.think", think.checked ? "1" : "0"));
  const sendBtn = h("button.btn.primary", { onclick: send, title: "Send (Enter)" }, icon("send"), "Send");
  const llm = state.system?.llm || {};
  let busy = false;

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(200, input.scrollHeight) + "px"; });

  async function load() {
    const msgs = await api(userPath("/messages"));
    clear(log, msgs.length ? null : intro());
    for (const m of msgs) {
      if (m.role === "user") log.append(h("div.msg-user", m.text));
      else {
        const tv = traceView({ steps: m.trace });
        tv.done({ elapsed_ms: m.trace.length ? m.trace[m.trace.length - 1].t_ms : 0 });
        log.append(assistantMsg(m, tv));
      }
    }
    window.scrollTo(0, document.body.scrollHeight);
  }

  function intro() {
    return h("div.empty", { style: { paddingTop: "48px" } },
      h("h3", "Ask the supervisor"),
      h("p", "It plans which tools to use, shows each step as it works, and only states what the evidence supports."),
      h("div.suggestions", { style: { justifyContent: "center", marginTop: "16px" } },
        SUGGEST.map((s) => h("button.chip", { onclick: () => { input.value = s; send(); } }, s))));
  }

  async function send() {
    const text = input.value.trim();
    if (!text || busy) return;
    busy = true; sendBtn.disabled = true;
    input.value = ""; input.style.height = "";
    if (log.querySelector(".empty")) clear(log);
    log.append(h("div.msg-user", text));
    const tv = traceView({ live: true, title: "Working" });
    const slot = assistantMsg(null, tv);
    log.append(slot);
    window.scrollTo(0, document.body.scrollHeight);
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
          if (m.attachments?.result) state.lastResult = null; // Discover reloads the newest list
        } else if (ev.type === "error") {
          tv.done({});
          slot.append(h("div.small", { style: { color: "var(--danger)" } }, "Something failed: " + ev.error));
        }
        window.scrollTo(0, document.body.scrollHeight);
      });
    } catch (e) { toast(e.message, "err"); tv.done({}); }
    busy = false; sendBtn.disabled = false; input.focus();
  }

  load();
  return h("div.chat",
    log,
    h("div.composer",
      h("div.composer-box", input,
        h("div.composer-foot",
          h("span.row.small.muted", h("span.dot." + (llm.available ? "ok" : "warn")),
            llm.available ? `${llm.model} on Ollama` : "Rule-based mode (no local model)"),
          h("label.switch", { title: "Stream the model's own reasoning tokens into the trace (slower; shown as unverified)" }, think, "Show model reasoning"),
          h("span.grow"),
          h("button.btn.ghost.sm", { onclick: async () => { await api(userPath("/messages"), { method: "DELETE" }); load(); } }, "Clear"),
          sendBtn)),
      h("div.hint", { style: { marginTop: "6px", textAlign: "center" } },
        "The assistant never changes your goal or rules by itself. Proposed changes appear as cards you confirm.")));
}
