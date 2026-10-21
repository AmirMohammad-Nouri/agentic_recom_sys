// Discover: priorities, the top five, and the supervisor's live column.
import { api, CRIT, CRIT_META, clear, f2, h, human, icon, on, pct, state, toast, userPath, weightBar, legend, ago } from "../lib.js";
import { changeSummary, decisionCard, recipeCard } from "../components.js";
import { traceView } from "../trace.js";

const feed = []; // supervisor feed for this session: {kind, title, steps, ts}

export function discoverView() {
  const goal = state.user_state.goal;
  const raw = Object.fromEntries(CRIT.map((k) => [k, Math.round((goal?.weights[k] ?? 0.2) * 100)]));
  let diversity = goal?.diversity ?? 0.3;
  let mode = goal?.mode ?? "lightweight";
  const applied = []; // undo stack of applied configs
  state.context = state.context || { meal: "dinner", place: "home" };

  // ---- controls -------------------------------------------------------------------------
  const norm = () => {
    const s = CRIT.reduce((a, k) => a + raw[k], 0);
    return s > 0 ? Object.fromEntries(CRIT.map((k) => [k, raw[k] / s])) : null;
  };
  const bar = h("div");
  const zeroNote = h("div.hint.hidden", { style: { color: "var(--warn)" } },
    "All priorities are zero. The last valid configuration will be kept; raise any slider to change it.");
  const sliders = CRIT.map((k) => {
    const val = h("span.val");
    const input = h("input", { type: "range", min: 0, max: 100, step: 5, value: raw[k], "aria-label": CRIT_META[k].long });
    const row = h("div.slider",
      h("span.name", h("span.swatch", { style: { background: CRIT_META[k].color } }), CRIT_META[k].long), val, input,
      h("div.ends", h("span", CRIT_META[k].lo), h("span", CRIT_META[k].hi)));
    input.addEventListener("input", () => { raw[k] = +input.value; refresh(); schedulePreview(); });
    return { k, row, val, input };
  });
  const divVal = h("span.val");
  const divInput = h("input", { type: "range", min: 0, max: 100, step: 5, value: Math.round(diversity * 100), "aria-label": "Diversity" });
  divInput.addEventListener("input", () => { diversity = divInput.value / 100; refresh(); schedulePreview(); });
  const modeSeg = h("div.seg", ["lightweight", "extended"].map((m) => h("button", {
    onclick: () => { mode = m; refresh(); schedulePreview(); },
  }, m === "lightweight" ? "Lightweight" : "Extended")));
  const modeDesc = h("div.hint");
  const preview = h("div.hint", { style: { minHeight: "18px" } });

  function refresh() {
    const n = norm();
    for (const s of sliders) clear(s.val, h("b", pct(n ? n[s.k] : 0)), ` · ${raw[s.k]}`);
    clear(bar, weightBar(n || goal.weights));
    zeroNote.classList.toggle("hidden", !!n);
    clear(divVal, h("b", f2(diversity)));
    [...modeSeg.children].forEach((b, i) => b.classList.toggle("on", ["lightweight", "extended"][i] === mode));
    modeDesc.textContent = state.system?.modes?.[mode]?.description || "";
  }

  let pTimer = null;
  function schedulePreview() {
    clearTimeout(pTimer);
    if (!lastResult) return;
    preview.textContent = "Previewing…";
    pTimer = setTimeout(async () => {
      try {
        const p = await api(userPath("/preview"), { method: "POST", body: { weights: norm(), diversity, mode } });
        const c = p.comparison;
        if (!c) { preview.textContent = "Preview unavailable."; return; }
        // preview compares against the saved goal; describe relative to it
        const n = c.entered.length + c.left.length + c.moved.length;
        preview.textContent = n === 0 ? "Preview: same list as your saved goal."
          : `Preview vs saved goal: ${c.entered.length} in, ${c.left.length} out, ${c.moved.length} moved. Not applied yet.`;
      } catch { preview.textContent = ""; }
    }, 450);
  }

  const contextSel = h("div.row.wrap", { style: { gap: "6px" } },
    h("select", { onchange: (e) => (state.context.meal = e.target.value), "aria-label": "Meal" },
      ["breakfast", "lunch", "dinner", "snack"].map((m) => h("option", { value: m, selected: state.context.meal === m }, human(m)))),
    h("select", { onchange: (e) => (state.context.place = e.target.value), "aria-label": "Place" },
      ["home", "work", "out"].map((m) => h("option", { value: m, selected: state.context.place === m }, "at " + m))));

  const controls = h("aside.card.sticky",
    h("div.card-head", h("h2", "Priorities"), h("span.small.muted", `goal v${goal.version}`)),
    h("div.card-body.stack",
      h("div.stack", sliders.map((s) => s.row)),
      h("div.stack.tight", h("div.field-label", "Weights applied"), bar, zeroNote),
      h("div.divider"),
      h("div.slider", h("span.name", "Diversity"), divVal, divInput, h("div.ends", h("span", "Similar dishes"), h("span", "Varied dishes"))),
      h("div.stack.tight", h("div.field-label", "Computation"), modeSeg, modeDesc),
      h("div.stack.tight", h("div.field-label", "Context recorded with feedback"), contextSel),
      h("div.divider"),
      h("div.btn-row",
        h("button.btn.primary.grow", { onclick: () => run() }, "Apply"),
        h("button.btn", { onclick: saveGoal, title: "Save these settings as a new goal version" }, "Save as goal")),
      h("div.btn-row",
        h("button.btn.ghost.sm", { onclick: undo, title: "Back to the previously applied settings" }, icon("undo"), "Undo"),
        h("button.btn.ghost.sm", { onclick: resetToGoal, title: "Back to your saved goal" }, icon("reset"), "Reset to saved")),
      preview));

  // ---- results ------------------------------------------------------------------------------
  const resultsHead = h("div.card-head");
  const resultsBody = h("div", h("div.card-body.stack", [1, 2, 3].map(() => h("div.skeleton", { style: { height: "70px" } }))));
  const results = h("section.card", resultsHead, resultsBody);
  let lastResult = null;

  async function run(cfg) {
    const body = cfg || { weights: norm(), diversity, mode };
    try {
      const res = await api(userPath("/recommend"), { method: "POST", body });
      if (lastResult) applied.push(lastResult.config);
      lastResult = res;
      state.lastResult = res;
      renderResults(res);
      pushFeed({ kind: "pipeline", title: res.results.length ? `Ranked ${res.results.length} recipes` : "No eligible recipes",
        steps: res.pipeline_trace.map((s, i) => ({ ...s, id: res.request_id + i, status: "done" })), ts: Date.now() / 1000 });
      preview.textContent = "";
    } catch (e) { toast(e.message, "err"); }
  }

  function renderResults(res) {
    const titles = {};
    for (const r of res.results) titles[r.recipe_id] = r.title;
    clear(resultsHead,
      h("div", h("h2", "Your top five"),
        h("div.small.muted", `${res.weights_source} · ${res.config.mode} · diversity ${f2(res.config.diversity)} · ` +
          `${res.eligibility.eligible_count} eligible of ${res.eligibility.eligible_count + res.eligibility.excluded_count}`)),
      h("span.small.muted.mono", { title: "Immutable request snapshot id" }, res.request_id));
    if (res.empty && !res.results.length) {
      clear(resultsBody, h("div.card-body.stack",
        h("div.empty", h("h3", "Nothing passes your mandatory rules"), h("p", res.empty.message)),
        res.empty.constraints?.length ? h("table.t", h("thead", h("tr", h("th", "If you removed"), h("th.num", "Eligible recipes"))),
          h("tbody", res.empty.constraints.map((c) => h("tr", h("td", c.label), h("td.num", c.eligible_if_removed))))) : null,
        h("div.hint", "Rules are never relaxed automatically. Edit them in Profile.")));
      return;
    }
    const compareWith = res.results.map((r) => ({ ...r }));
    clear(resultsBody,
      res.comparison ? changeSummary(res.comparison, titles) : null,
      res.config.weights_message && res.config.weights_message.startsWith("All") ? h("div.change", res.config.weights_message) : null,
      res.results.map((r) => recipeCard(r, { requestId: res.request_id, compareWith })),
      h("div.card-body", { style: { borderTop: "1px solid var(--border)" } }, legend(),
        h("div.hint", { style: { marginTop: "4px" } }, "Bars show each criterion's share of the utility. Preference match is a model estimate; cost and CO₂e are demo placeholders.")));
  }

  async function saveGoal() {
    const n = norm();
    if (!n) return toast("Raise at least one priority before saving", "err");
    try {
      const out = await api(userPath("/goal"), { method: "POST", body: { name: "My priorities", weights: n, diversity, mode } });
      state.user_state = await api(userPath("/state"));
      toast(`Saved as goal v${out.goal_version}`);
      pushFeed({ kind: "goal", title: `Goal v${out.goal_version} saved`, ts: Date.now() / 1000,
        steps: [{ id: "g" + out.goal_version, kind: "perceive", source: "learner", status: "done", title: "Explicit statement recorded",
          detail: "Saving a goal is the strongest evidence of what you want. The revealed-preference model was updated toward it.",
          evidence: out.trail }] });
    } catch (e) { toast(e.message, "err"); }
  }

  function setControls(cfg) {
    for (const s of sliders) { raw[s.k] = Math.round(cfg.weights[s.k] * 100); s.input.value = raw[s.k]; }
    diversity = cfg.diversity; divInput.value = Math.round(diversity * 100);
    mode = cfg.mode;
    refresh();
  }
  function undo() {
    const prev = applied.pop();
    if (!prev) return toast("Nothing to undo");
    const cfg = { weights: prev.weights_applied, diversity: prev.diversity, mode: prev.mode };
    setControls(cfg);
    run(cfg).then(() => applied.pop());
  }
  function resetToGoal() {
    const g = state.user_state.goal;
    setControls(g);
    run({ weights: g.weights, diversity: g.diversity, mode: g.mode });
  }

  // ---- supervisor column ---------------------------------------------------------------------
  const decisionsBox = h("div.stack");
  const feedBox = h("div");
  const supervisor = h("aside.card.sticky.supervisor-col",
    h("div.card-head", h("h2", "Supervisor"), h("button.btn.ghost.sm", { onclick: reviewNow, title: "Run the conflict and confidence check now" }, "Review now")),
    h("div.card-body", decisionsBox),
    h("div", { style: { borderTop: "1px solid var(--border)" } }, feedBox));

  async function loadDecisions() {
    const open = await api(userPath("/decisions?status=open"));
    clear(decisionsBox, open.length ? open.map((d) => decisionCard(d, { onResolved: loadDecisions }))
      : h("div.small.muted", "No open questions. The supervisor checks your goal against your choices after every piece of feedback."));
  }
  async function reviewNow() {
    const rv = await api(userPath("/review"), { method: "POST" });
    pushFeed({ kind: "review", title: `Review: ${human(rv.status)}`, steps: rv.trace, ts: Date.now() / 1000 });
    loadDecisions();
  }
  function renderFeed() {
    clear(feedBox, feed.length ? feed.slice().reverse().slice(0, 12).map((f) => {
      const tv = traceView({ steps: f.steps || [], title: f.title });
      return h("div.feed-item", h("div.when", ago(f.ts)), tv.el);
    }) : h("div.feed-item.small.muted", "Activity appears here: how each list was computed, and what the supervisor concluded after your feedback."));
  }
  function pushFeed(item) { feed.push(item); renderFeed(); }

  const off = on((what, data) => {
    if (what === "review") {
      pushFeed({ kind: "review", title: `After you ${data.type === "choose" ? "chose" : data.type + "d"} ${data.recipe}: ${human(data.status)}`, steps: data.trace, ts: data.ts });
      loadDecisions();
    }
    if (what === "decision") api(userPath("/state")).then((s) => { state.user_state = s; });
  });

  refresh();
  renderFeed();
  loadDecisions();
  if (state.lastResult && state.lastResult.user_id === state.user) { lastResult = state.lastResult; renderResults(lastResult); }
  else run({ weights: goal.weights, diversity: goal.diversity, mode: goal.mode });

  const el = h("div",
    h("div.page-head", h("div", h("h1", "Discover"),
      h("p", "Five recipes ranked by your priorities. Every reason comes from a recorded evidence record."))),
    h("div.discover", controls, results, supervisor));
  el.cleanup = off;
  return el;
}
