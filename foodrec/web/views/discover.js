// Discover: the three values, the meal, and the top five. The assistant is docked beside it.
import { api, can, CRIT, CRIT_META, clear, emit, f2, h, human, icon, on, pct, state, toast, userPath, VALUES, visibleCriteria, weightBar, legend } from "../lib.js";
import { changeSummary, recipeCard } from "../components.js";

function defaultMeal() {
  const hr = new Date().getHours();
  return hr < 11 ? "breakfast" : hr < 15 ? "lunch" : hr < 17 ? "snack" : "dinner";
}

export function discoverView() {
  const goal = state.user_state.goal;
  const raw = Object.fromEntries(VALUES.map((k) => [k, Math.round((goal?.weights[k] ?? 0.33) * 100)]));
  let diversity = goal?.diversity ?? 0.3;
  let mode = goal?.mode ?? "lightweight";
  const applied = []; // undo stack of applied configs
  state.context = state.context || { meal: defaultMeal(), place: "home" };
  const taste = state.system?.taste_share ?? 0.25;

  // ---- controls -------------------------------------------------------------------------
  const norm = () => {
    const s = VALUES.reduce((a, k) => a + raw[k], 0);
    return s > 0 ? Object.fromEntries(VALUES.map((k) => [k, raw[k] / s])) : null;
  };
  const bar = h("div");
  const zeroNote = h("div.hint.hidden", { style: { color: "var(--warn)" } },
    "All priorities are zero. The last valid configuration will be kept; raise any slider to change it.");
  const sliders = VALUES.map((k) => {
    const val = h("span.val");
    const input = h("input", { type: "range", min: 0, max: 100, step: 5, value: raw[k], "aria-label": CRIT_META[k].long });
    const row = h("div.slider",
      h("span.name", { title: CRIT_META[k].what }, h("span.swatch", { style: { background: CRIT_META[k].color } }), CRIT_META[k].long), val, input,
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
    const eff = n || goal.weights;
    clear(bar, weightBar({ p: taste, ...Object.fromEntries(VALUES.map((k) => [k, (1 - taste) * eff[k]])) }));
    zeroNote.classList.toggle("hidden", !!n);
    clear(divVal, h("b", f2(diversity)));
    [...modeSeg.children].forEach((b, i) => b.classList.toggle("on", ["lightweight", "extended"][i] === mode));
    [...mealSeg.children].forEach((b, i) => b.classList.toggle("on", ["any", ...MEALS][i] === state.context.meal));
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
        if (!p) return;
        const c = p.comparison;
        if (!c) { preview.textContent = "Preview unavailable."; return; }
        // preview compares against the saved goal; describe relative to it
        const n = c.entered.length + c.left.length + c.moved.length;
        preview.textContent = n === 0 ? "Preview: same list as your saved goal."
          : `Preview vs saved goal: ${c.entered.length} in, ${c.left.length} out, ${c.moved.length} moved. Not applied yet.`;
      } catch { preview.textContent = ""; }
    }, 450);
  }

  const MEALS = Object.keys(state.system?.options?.meal_contexts || { breakfast: 1, lunch: 1, dinner: 1, snack: 1, dessert: 1 });
  const mealSeg = h("div.seg.meal-seg", ["any", ...MEALS].map((m) => h("button", {
    onclick: () => { state.context.meal = m; refresh(); run(); },
  }, human(m))));
  const placeSel = h("select", { onchange: (e) => (state.context.place = e.target.value), "aria-label": "Place" },
    ["home", "work", "out"].map((m) => h("option", { value: m, selected: state.context.place === m }, "at " + m)));

  const controls = h("aside.card.sticky",
    h("div.card-head", h("h2", "Your values"), h("span.small.muted", `goal v${goal.version}`)),
    h("div.card-body.stack",
      h("div.stack", sliders.map((s) => s.row)),
      h("div.stack.tight", h("div.field-label", "Weights applied"), bar, zeroNote,
        h("div.hint", `Taste keeps a fixed ${pct(taste)} share, learned from your likes and rejections; the sliders share the rest.`)),
      h("div.divider"),
      h("div.slider", h("span.name", "Diversity"), divVal, divInput, h("div.ends", h("span", "Similar dishes"), h("span", "Varied dishes"))),
      h("div.stack.tight", h("div.field-label", "Computation"), modeSeg, modeDesc),
      h("div.stack.tight", h("div.field-label", "Place (recorded with feedback)"), placeSel),
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
    const body = { ...(cfg || { weights: norm(), diversity, mode }), meal: state.context.meal === "any" ? null : state.context.meal };
    try {
      const res = await api(userPath("/recommend"), { method: "POST", body });
      if (lastResult) applied.push(lastResult.config);
      lastResult = res;
      state.lastResult = res;
      renderResults(res);
      if (can("agent_trace") && res.pipeline_trace) emit("feed", { title: res.results.length ? `Ranked ${res.results.length} recipes` : "No eligible recipes",
        steps: res.pipeline_trace.map((s, i) => ({ ...s, id: res.request_id + i, status: "done" })), ts: Date.now() / 1000 });
      preview.textContent = "";
    } catch (e) { toast(e.message, "err"); }
  }

  function renderResults(res) {
    const titles = {};
    for (const r of res.results) titles[r.recipe_id] = r.title;
    clear(resultsHead,
      h("div", h("h2", res.results.length === 5 ? "Your top five" : `Your top ${res.results.length}`),
        can("explanations") ? h("div.small.muted", `${res.weights_source} · ${res.config.mode} · diversity ${f2(res.config.diversity)} · ` +
          `${res.eligibility.eligible_count} eligible of ${res.eligibility.eligible_count + res.eligibility.excluded_count}`) : null),
      can("explanations") ? h("span.small.muted.mono", { title: "Immutable request snapshot id" }, res.request_id) : null);
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
      res.comparison && can("explanations") ? changeSummary(res.comparison, titles) : null,
      res.config.weights_message && res.config.weights_message.startsWith("All") ? h("div.change", res.config.weights_message) : null,
      res.results.map((r) => recipeCard(r, { requestId: res.request_id, compareWith })),
      can("utility") && visibleCriteria().length ? h("div.card-body", { style: { borderTop: "1px solid var(--border)" } }, legend(visibleCriteria()),
        h("div.hint", { style: { marginTop: "4px" } }, "Bars show each criterion's share of the utility. Taste match is a model estimate; CO₂e is estimated from ingredient amounts and food-group averages.")) : null);
  }

  async function saveGoal() {
    const n = norm();
    if (!n) return toast("Raise at least one priority before saving", "err");
    try {
      const out = await api(userPath("/goal"), { method: "POST", body: { name: "My priorities", weights: n, diversity, mode } });
      state.user_state = await api(userPath("/state"));
      toast(`Saved as goal v${out.goal_version}`);
      emit("feed", { title: `Goal v${out.goal_version} saved`, ts: Date.now() / 1000,
        steps: [{ id: "g" + out.goal_version, kind: "perceive", source: "learner", status: "done", title: "New goal recorded",
          detail: "Your observed weights moved halfway toward the new goal, and the event count for the supervisor restarts.",
          evidence: out.trail }] });
    } catch (e) { toast(e.message, "err"); }
  }

  function setControls(cfg) {
    for (const s of sliders) { raw[s.k] = Math.round((cfg.weights[s.k] ?? 0) * 100); s.input.value = raw[s.k]; }
    diversity = cfg.diversity; divInput.value = Math.round(diversity * 100);
    mode = cfg.mode;
    refresh();
  }
  function undo() {
    const prev = applied.pop();
    if (!prev) return toast("Nothing to undo");
    const cfg = { weights: prev.values_applied, diversity: prev.diversity, mode: prev.mode };
    setControls(cfg);
    run(cfg).then(() => applied.pop());
  }
  function resetToGoal() {
    const g = state.user_state.goal;
    setControls(g);
    run({ weights: g.weights, diversity: g.diversity, mode: g.mode });
  }

  const off = on((what) => {
    if (what === "results_changed" && !state.lastResult) run({ weights: state.user_state.goal.weights, diversity, mode });
  });

  refresh();
  if (state.lastResult && state.lastResult.user_id === state.user) { lastResult = state.lastResult; renderResults(lastResult); }
  else run({ weights: goal.weights, diversity: goal.diversity, mode: goal.mode });

  // Without priority controls (an access-role setting) the saved goal is used and the list fills the page.
  const el = h("div",
    h("div.page-head", h("div", h("h1", "Discover"),
      h("p", can("explanations") ? "Five recipes ranked by healthiness, sustainability, convenience and your taste. Every reason comes from a recorded evidence record."
        : "Recipes picked for you.")),
      h("div.stack.tight", { style: { alignItems: "flex-end" } }, h("span.field-label", "Meal"), mealSeg)),
    can("weight_controls") ? h("div.discover", controls, results) : results);
  el.cleanup = off;
  return el;
}
