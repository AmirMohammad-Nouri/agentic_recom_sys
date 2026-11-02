// Discover: the recommended dishes, and nothing else (values and diversity are in Profile).
// With meal cycles on, the user walks through the meals of each day and reacts to every dish.
import { api, can, clear, emit, h, human, icon, legend, on, pct, state, toast, userPath, VALUES, visibleCriteria } from "../lib.js";
import { changeSummary, recipeCard } from "../components.js";

const title = (m) => human(m)[0].toUpperCase() + human(m).slice(1);

function defaultMeal(meals) {
  const hr = new Date().getHours();
  const m = hr < 11 ? "breakfast" : hr < 15 ? "lunch" : hr < 17 ? "snack" : "dinner";
  return meals.includes(m) ? m : meals[0];
}

const footerLegend = () => (can("utility") && visibleCriteria(VALUES).length
  ? h("div.card-body", { style: { borderTop: "1px solid var(--border)" } }, legend(visibleCriteria(VALUES))) : null);

function noDishes(res) {
  return h("div.card-body.stack",
    h("div.empty", h("h3", "Nothing passes your rules"), h("p", res?.empty?.message || "No dish is left for this meal.")),
    res?.empty?.constraints?.length ? h("table.t", h("thead", h("tr", h("th", "If you removed"), h("th.num", "Eligible recipes"))),
      h("tbody", res.empty.constraints.map((c) => h("tr", h("td", c.label), h("td.num", c.eligible_if_removed))))) : null,
    h("div.hint", "Rules are never relaxed automatically. Edit them in Profile."));
}

// ---- free list: pick a meal, see the top dishes ---------------------------------------------
function listView() {
  const MEALS = Object.keys(state.system?.options?.meal_contexts || { breakfast: 1, lunch: 1, dinner: 1 });
  // the meals on offer may be limited (a cycle's meals): then there is no "any"
  const choices = state.system?.options?.meal_contexts?.snack ? ["any", ...MEALS] : MEALS;
  state.context = state.context || { meal: defaultMeal(MEALS), place: "home" };
  if (!choices.includes(state.context.meal)) state.context.meal = choices[0];
  const mealSeg = h("div.seg.meal-seg", choices.map((m) => h("button", {
    onclick: () => { state.context.meal = m; paint(); run(); },
  }, human(m))));
  const paint = () => [...mealSeg.children].forEach((b, i) => b.classList.toggle("on", choices[i] === state.context.meal));
  const head = h("div.card-head");
  const body = h("div", h("div.card-body.stack", [1, 2, 3].map(() => h("div.skeleton", { style: { height: "70px" } }))));

  async function run() {
    try {
      const res = await api(userPath("/recommend"), { method: "POST", body: { meal: state.context.meal === "any" ? null : state.context.meal } });
      state.lastResult = res;
      render(res);
      if (can("agent_trace") && res.pipeline_trace) emit("feed", { title: res.results.length ? `Ranked ${res.results.length} recipes` : "No eligible recipes",
        steps: res.pipeline_trace.map((s, i) => ({ ...s, id: res.request_id + i, status: "done" })), ts: Date.now() / 1000 });
    } catch (e) { toast(e.message, "err"); }
  }
  function render(res) {
    const titles = Object.fromEntries(res.results.map((r) => [r.recipe_id, r.title]));
    clear(head, h("div", h("h2", res.results.length === 5 ? "Your top five" : `Your top ${res.results.length}`),
      can("explanations") ? h("div.small.muted", `${res.eligibility.eligible_count.toLocaleString()} of ` +
        `${(res.eligibility.eligible_count + res.eligibility.excluded_count).toLocaleString()} recipes pass your rules`) : null));
    if (res.empty && !res.results.length) return clear(body, noDishes(res));
    const compareWith = res.results.map((r) => ({ ...r }));
    clear(body,
      res.comparison && can("explanations") ? changeSummary(res.comparison, titles) : null,
      res.results.map((r) => recipeCard(r, { requestId: res.request_id, compareWith })),
      footerLegend());
  }
  const off = on((what) => { if (what === "results_changed" && !state.lastResult) run(); });
  paint();
  if (state.lastResult && state.lastResult.user_id === state.user) render(state.lastResult);
  else run();
  const el = h("div",
    h("div.page-head", h("div", h("h1", "Discover"),
      h("p", can("explanations") ? "Dishes ranked by your values and your taste." : "Dishes picked for you.")),
      h("div.stack.tight", { style: { alignItems: "flex-end" } }, h("span.field-label", "Meal"), mealSeg)),
    h("section.card", head, body));
  el.cleanup = off;
  return el;
}

// ---- meal cycle: day by day, meal by meal, a reaction to every dish -------------------------
function cycleView() {
  const root = h("div", h("div.page-head", h("div", h("h1", "Discover"))),
    h("section.card", h("div.card-body.stack", [1, 2, 3].map(() => h("div.skeleton", { style: { height: "70px" } })))));
  let st = null;

  async function call(path, method = "GET") {
    try {
      st = await api(userPath(path), { method });
      state.lastResult = st.result ? { ...st.result, user_id: state.user } : null;
      paint();
      window.scrollTo(0, 0);
    } catch (e) { toast(e.message, "err"); }
  }

  function steps() {
    return h("ol.cycle-steps", st.meals.map((m, i) => h("li" + (i < st.meal_index ? ".done" : i === st.meal_index ? ".now" : ""),
      h("span.dot-step", i < st.meal_index ? icon("check") : i + 1), human(m))));
  }

  const barFill = h("span");
  const countText = h("span.small.muted");
  const nextBtn = h("button.btn.primary", { onclick: () => call("/cycle/next", "POST") });
  const leftText = h("span.small");

  function progress() {
    const total = st.total || 1;
    barFill.style.width = pct(Math.min(1, st.answered_total / total));
    countText.textContent = `${st.answered_total} of ${st.total} reactions`;
    const shown = st.result?.results?.length || 0;
    const left = shown - Object.keys(st.answered).length;
    const lastMeal = st.meal_index >= st.meals.length - 1;
    const label = lastMeal ? (st.day >= st.days ? "Finish" : `Continue to day ${st.day + 1}`) : `Continue to ${human(st.meals[st.meal_index + 1])}`;
    nextBtn.textContent = label;
    nextBtn.disabled = left > 0;
    leftText.textContent = left > 0 ? `React to ${left} more dish${left === 1 ? "" : "es"} to continue.` : "All done for this meal.";
  }

  function paint() {
    if (st.finished) {
      clear(root,
        h("div.page-head", h("div", h("h1", "Discover"))),
        h("section.card", h("div.card-body.stack.cycle-done",
          h("h2", "You finished the cycle"),
          h("p", `${st.days} day${st.days === 1 ? "" : "s"}, ${st.meals.length} meal${st.meals.length === 1 ? "" : "s"} a day, ${st.recommendations} dish${st.recommendations === 1 ? "" : "es"} per meal: ${st.answered_total} reaction${st.answered_total === 1 ? "" : "s"}. Thank you.`),
          h("div.btn-row",
            can("page_insights") ? h("a.btn.primary", { href: "#/insights" }, "See what was learned") : null,
            h("button.btn", { onclick: () => call("/cycle/restart", "POST") }, icon("reset"), "Start again")))));
      return;
    }
    const res = st.result || { results: [] };
    const compareWith = res.results.map((r) => ({ ...r }));
    clear(root,
      h("div.page-head", h("div", h("h1", "Discover"), h("p", `Day ${st.day} of ${st.days} · ${title(st.meal)}`))),
      h("section.card.cycle-bar",
        h("div.card-body.stack.tight",
          steps(),
          h("div.row.between", { style: { gap: "12px" } }, h("div.cycle-progress.grow", barFill), countText))),
      h("section.card", { style: { marginTop: "16px" } },
        h("div.card-head", h("div", h("h2", `${title(st.meal)}: your ${res.results.length} dish${res.results.length === 1 ? "" : "es"}`),
          h("div.small.muted", "Tell us what you think of each one."))),
        res.results.length ? res.results.map((r) => recipeCard(r, { requestId: res.request_id, compareWith, answered: st.answered[r.recipe_id] })) : noDishes(res),
        footerLegend(),
        h("div.card-body.row.between.cycle-foot", leftText, nextBtn)));
    progress();
  }

  const off = on((what, data) => {
    if (what === "answered" && st && !st.finished && st.result?.request_id === data.request_id && !st.answered[data.recipe_id]) {
      st.answered[data.recipe_id] = data.type;
      st.answered_total += 1;
      progress();
    }
  });
  call("/cycle");
  root.cleanup = off;
  return root;
}

export function discoverView() {
  return state.user_state.cycle?.enabled ? cycleView() : listView();
}
