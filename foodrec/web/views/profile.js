// Profile form (shared with onboarding), profile page with version history, and Activity.
import { api, clear, CRIT_META, emit, f2, grade, h, human, icon, state, toast, userPath, VALUES, weightBar, when, ago } from "../lib.js";
import { decisionCard } from "../components.js";

function chipSet(options, selected, { label = (x) => human(x), strict = false } = {}) {
  const sel = new Set(selected);
  const box = h("div.chips", options.map((o) => {
    const c = h("button.chip" + (strict ? ".strict" : "") + (sel.has(o) ? ".on" : ""), { type: "button" }, label(o));
    c.addEventListener("click", () => { sel.has(o) ? sel.delete(o) : sel.add(o); c.classList.toggle("on"); });
    return c;
  }));
  return { el: box, value: () => [...sel] };
}

// Ingredient picker with a per-item strict toggle.
function ingredientList(all, items, { strictable }) {
  const rows = new Map(items.map((x) => [x.name, x]));
  const list = h("div.chips");
  const input = h("input", { type: "text", placeholder: "Type an ingredient…", list: "ing-options" + (strictable ? "-s" : "") });
  const dl = h("datalist", { id: "ing-options" + (strictable ? "-s" : "") }, all.map((i) => h("option", { value: i })));
  const render = () => clear(list, [...rows.values()].map((x) => h("span.chip" + (x.strict ? ".strict.on" : ".on"),
    strictable ? h("span", { title: "Toggle between 'never include' and 'prefer to avoid'", style: { cursor: "pointer" },
      onclick: () => { x.strict = !x.strict; render(); } }, x.strict ? "never: " : "avoid: ") : null,
    x.name, h("span.x", { onclick: () => { rows.delete(x.name); render(); }, title: "Remove" }, " ×"))));
  const addItem = () => {
    const v = input.value.trim().toLowerCase();
    if (!v) return;
    if (!all.includes(v)) { toast(`"${v}" is not in the catalogue's ingredient list`, "err"); return; }
    rows.set(v, { name: v, strict: strictable });
    input.value = "";
    render();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addItem(); } });
  input.addEventListener("change", addItem);
  render();
  return { el: h("div.stack.tight", h("div.row", input, dl), list), value: () => [...rows.values()] };
}

const GOAL_TEXT = {
  eat_healthier: "All nine factors count equally",
  lose_weight: "Lower energy, fat and sugar; more fibre",
  gain_weight: "Energy-dense dishes with protein score higher",
  build_muscle: "Protein counts most",
  heart_health: "Saturated fat, salt and cholesterol count most",
  less_sugar: "Sugar counts most",
};

function limitRow(label, unit, cur, step) {
  const on = h("input", { type: "checkbox", checked: !!cur });
  const val = h("input", { type: "number", min: 2, step, value: cur?.value ?? 10 });
  const strict = h("select", h("option", { value: "soft", selected: cur && !cur.strict }, "Preference"),
    h("option", { value: "strict", selected: cur?.strict }, "Strict limit"));
  return {
    el: h("div.limit-row", h("label.switch", on, label), h("div.row", val, h("span.small.muted", unit)), strict),
    value: () => (on.checked ? { value: +val.value, strict: strict.value === "strict" } : null),
  };
}

export function profileForm(declared = {}) {
  const o = state.system.options;
  const allergens = chipSet(o.allergens, declared.allergens || [], { strict: true });
  const diets = chipSet(o.diets, declared.diets || [], { strict: true });
  const cuisines = chipSet(o.cuisines, declared.preferred_cuisines || []);
  const dislikes = ingredientList(o.ingredients, declared.disliked_ingredients || [], { strictable: true });
  const likes = ingredientList(o.ingredients, (declared.liked_ingredients || []).map((n) => ({ name: n })), { strictable: false });
  const maxIng = limitRow("At most", "ingredients", declared.max_ingredients, 1);
  const goals = o.health_goals || [];
  let goal = declared.health_goal || "eat_healthier";
  const goalPick = h("div.goal-grid", goals.map((g) => {
    const b = h("button.goal-card" + (g.id === goal ? ".on" : ""), { type: "button" }, h("b", g.label), h("span.small.muted", GOAL_TEXT[g.id] || ""));
    b.addEventListener("click", () => { goal = g.id; [...goalPick.children].forEach((x) => x.classList.toggle("on", x === b)); });
    return b;
  }));
  const sections = {
    safety: h("div.stack",
      h("div.field", h("span.field-label", "Allergies"), allergens.el,
        h("span.hint", "Strict. Recipes with incomplete allergen data are excluded too, because missing data is not proof of safety.")),
      h("div.field", h("span.field-label", "Diets"), diets.el, h("span.hint", "Strict. A recipe whose diet flag is unknown is excluded."))),
    taste: h("div.stack",
      h("div.field", h("span.field-label", "Ingredients to avoid"), dislikes.el,
        h("span.hint", "Click the prefix to switch between 'never include' (a rule) and 'avoid' (a preference that only lowers taste match).")),
      h("div.field", h("span.field-label", "Ingredients you like"), likes.el),
      h("div.field", h("span.field-label", "Cuisines you enjoy"), cuisines.el)),
    limits: h("div.stack",
      h("div.hint", "A strict limit removes recipes; a preference only counts in the ranking."),
      maxIng.el),
    goal: h("div.stack", goalPick,
      h("div.hint", "Your health goal decides how much each of the nine nutrient factors counts in the healthiness score. It does not filter recipes and it is not medical advice.")),
  };
  return {
    sections,
    value: () => ({
      allergens: allergens.value(), diets: diets.value(), preferred_cuisines: cuisines.value(),
      disliked_ingredients: dislikes.value(), liked_ingredients: likes.value().map((x) => x.name),
      max_ingredients: maxIng.value(), health_goal: goal, liked_recipes: declared.liked_recipes || [],
    }),
  };
}

export function profileView() {
  const st = state.user_state;
  const form = profileForm(st.profile.declared);
  const history = h("div");
  const goals = h("div");

  async function save() {
    try {
      const out = await api(userPath("/profile"), { method: "PUT", body: { declared: form.value() } });
      state.user_state = await api(userPath("/state"));
      state.lastResult = null;
      toast(`Saved profile v${out.profile_version}`);
      load();
    } catch (e) { toast(e.message, "err"); }
  }
  async function load() {
    const [ph, gh] = await Promise.all([api(userPath("/profile/history")), api(userPath("/goal/history"))]);
    clear(history, h("table.t", h("thead", h("tr", h("th", "Version"), h("th", "When"), h("th", "Source"), h("th", "Rules"))),
      h("tbody", ph.map((p) => h("tr", h("td", "v" + p.version), h("td", when(p.created_at)), h("td", human(p.source)),
        h("td.small", [...p.declared.allergens.map((a) => "allergy " + a), ...p.declared.diets.map(human),
          ...p.declared.disliked_ingredients.filter((d) => d.strict).map((d) => "never " + d.name)].join(", ") || "–"))))));
    clear(goals, gh.map((g) => h("div.hit",
      h("div.row.between", h("div", h("b", `v${g.version} · ${g.name}`), h("div.small.muted", `${human(g.source)} · ${when(g.created_at)} · ${g.mode} · diversity ${f2(g.diversity)}`)),
        g.version !== gh[0].version ? h("button.btn.sm", { onclick: async () => {
          await api(userPath("/goal/revert"), { method: "POST", body: { version: g.version } });
          state.user_state = await api(userPath("/state")); state.lastResult = null; toast(`Restored v${g.version} as a new version`); load();
        } }, icon("undo"), "Restore") : h("span.tag.accent", "current")),
      h("div", { style: { marginTop: "6px" } }, weightBar(g.weights, VALUES)),
      h("div.small.muted", { style: { marginTop: "4px" } }, VALUES.map((k) => `${CRIT_META[k].label} ${f2(g.weights[k])}`).join(" · ")))));
  }
  load();
  return h("div",
    h("div.page-head", h("div", h("h1", "Profile"), h("p", "Your rules and preferences. Every save creates a new version; nothing is overwritten.")),
      h("button.btn.primary", { onclick: save }, "Save new version")),
    h("div.two-col",
      h("div.stack",
        h("section.card", h("div.card-head", h("h2", "Safety rules")), h("div.card-body", form.sections.safety)),
        h("section.card", h("div.card-head", h("h2", "Taste")), h("div.card-body", form.sections.taste)),
        VALUES.includes("h") ? h("section.card", h("div.card-head", h("h2", "Health goal")), h("div.card-body", form.sections.goal)) : null,
        h("section.card", h("div.card-head", h("h2", "Ingredient limit")), h("div.card-body", form.sections.limits))),
      h("div.stack",
        h("section.card", h("div.card-head", h("h2", "Goal versions")), h("div.card-body", goals)),
        h("section.card", h("div.card-head", h("h2", "Profile versions")), h("div.card-body", history)))));
}

const EVENT_TEXT = {
  feedback: (p) => `${human(p.type)} ${p.recipe_title || p.recipe_id}${p.reason ? " · " + human(p.reason) : ""}`,
  profile_created: () => "Completed onboarding", profile_updated: (p) => `Profile saved as v${p.profile_version}${p.source !== "user" ? " (" + human(p.source) + ")" : ""}`,
  goal_saved: (p) => `Goal saved as v${p.goal_version}`, proposal_accepted: (p) => `Accepted a proposal → goal v${p.goal_version}`,
  proposal_dismissed: (p) => `Dismissed ${human(p.kind)}`, question_answered: (p) => `Answered a question: ${human(p.answer)}`,
  profile_correction: (p) => `Corrected belief ${p.concept} → ${f2(p.mean)}`,
  dish_removed: (p) => `Removed ${p.recipe_title || p.recipe_id} from ${human(p.collection)}`,
};

export function activityView() {
  const events = h("ul.timeline");
  const decisions = h("div.stack");
  (async () => {
    let [evs, decs] = await Promise.all([api(userPath("/events")), api(userPath("/decisions"))]);
    evs = evs.filter((e) => e.type !== "ui");  // page views etc. are for the study export, not the user's history
    const ids = [...new Set(evs.map((e) => e.payload.recipe_id).filter(Boolean))].slice(0, 200);
    const recipes = ids.length ? await api("/api/recipes?limit=200&ids=" + encodeURIComponent(ids.join(","))) : [];
    const titles = Object.fromEntries(recipes.map((r) => [r.recipe_id, r.title]));
    clear(events, evs.length ? evs.map((e) => h("li", { title: e.event_id },
      h("span.muted.small", when(e.ts)), h("span", h("span.tag", human(e.type))),
      h("span", (EVENT_TEXT[e.type] || (() => human(e.type)))({ ...e.payload, recipe_title: titles[e.payload.recipe_id] }),
        e.context?.meal ? h("span.small.muted", `  · ${e.context.meal}${e.context.place ? " at " + e.context.place : ""}`) : null)))
      : h("li", h("span.muted", "No events yet.")));
    clear(decisions, decs.length ? decs.map((d) => decisionCard(d)) : h("div.small.muted", "The supervisor has not raised anything yet."));
  })();
  return h("div",
    h("div.page-head", h("div", h("h1", "Activity"), h("p", "Everything you did and every supervisor decision, newest first."))),
    h("div.sidebar-layout",
      h("section.card", h("div.card-head", h("h2", "Event log")), events),
      h("aside.stack", h("section.card", h("div.card-head", h("h2", "Supervisor decisions")), h("div.card-body", decisions)))));
}

export function onboarding(onDone) {
  const form = profileForm({});
  const picked = new Map();
  let step = 0;
  // starting slider values: the configured default weights of the enabled criteria
  const dw = state.system.default_weights || {};
  const raw = Object.fromEntries(VALUES.map((k) => [k, Math.round(100 * (dw[k] ?? 1 / VALUES.length))]));
  const hasHealth = VALUES.includes("h");
  const body = h("div.modal-body");
  const ind = h("div.steps-ind");
  const back = h("button.btn.ghost", { onclick: () => go(step - 1) }, "Back");
  const next = h("button.btn.primary", { onclick: () => (step === 4 ? finish() : go(step + 1)) }, "Continue");
  const titles = ["Safety first", "What you like", hasHealth ? "Your health goal" : "Limits", "A few dishes you like",
    VALUES.length === 3 ? "Your three values" : "Your values"];
  const subs = ["These are strict rules. They filter recipes before anything is ranked.",
    "Preferences shape the taste match; only 'never include' items become rules.",
    hasHealth ? "This decides how the nutrient factors are weighted inside the healthiness score." : "An optional limit on the number of ingredients.",
    "Search and pick up to five. This gives the taste model a starting point.",
    "How much should each count? You can change these at any time; every change is versioned."];
  const head = h("div.modal-head");

  // dish picker with search (the catalogue has thousands of recipes)
  const grid = h("div.pick-grid");
  const search = h("input", { type: "search", placeholder: "Search dishes or an ingredient, e.g. lentil, pasta, chicken", "aria-label": "Search dishes" });
  const pickedBox = h("div.chips");
  let timer = null;
  const renderPicked = () => clear(pickedBox, [...picked.values()].map((r) => h("span.chip.on", r.title,
    h("span.x", { onclick: () => { picked.delete(r.recipe_id); renderPicked(); syncGrid(); } }, " ×"))));
  const syncGrid = () => [...grid.children].forEach((b) => b.classList.toggle("on", picked.has(b.dataset.id)));
  async function loadDishes() {
    const rs = await api(`/api/recipes?limit=24&q=${encodeURIComponent(search.value.trim())}`);
    clear(grid, rs.length ? rs.map((r) => {
      const img = r.image_url ? h("img", { src: r.image_url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }) : null;
      img?.addEventListener("error", () => img.remove());
      const b = h("button.pick" + (picked.has(r.recipe_id) ? ".on" : ""), { type: "button", dataset: { id: r.recipe_id } },
        h("div.pick-img", icon("bowl"), img), h("div.t", r.title),
        h("div.small.muted", `${r.cuisine === "Other" ? human(r.meal) : r.cuisine} · ${r.n_ingredients} ingredients`));
      b.addEventListener("click", () => {
        if (picked.has(r.recipe_id)) picked.delete(r.recipe_id); else if (picked.size < 5) picked.set(r.recipe_id, r);
        else toast("Five is enough for a start");
        syncGrid(); renderPicked();
      });
      return b;
    }) : h("div.small.muted", "No dish matches that search."));
  }
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(loadDishes, 250); });
  loadDishes();
  const dishes = h("div.stack", search, pickedBox, grid);

  const total = h("div");
  const sliders = VALUES.map((k) => {
    const input = h("input", { type: "range", min: 0, max: 100, step: 5, value: raw[k] });
    const v = h("span.val");
    input.addEventListener("input", () => { raw[k] = +input.value; paint(); });
    return { k, v, el: h("div.slider", h("span.name", h("span.swatch", { style: { background: CRIT_META[k].color } }), CRIT_META[k].long), v, input,
      h("div.ends", h("span", CRIT_META[k].what), h("span", ""))) };
  });
  function paint() {
    const tot = VALUES.reduce((a, k) => a + raw[k], 0) || 1;
    for (const s of sliders) clear(s.v, h("b", `${Math.round((100 * raw[s.k]) / tot)}%`));
    clear(total, weightBar(Object.fromEntries(VALUES.map((k) => [k, raw[k] / tot])), VALUES));
  }
  paint();
  const prio = h("div.stack", sliders.map((s) => s.el), h("div.stack.tight", h("div.field-label", "Share of each value"), total),
    h("div.hint", `Taste is not a slider: it keeps a fixed ${Math.round((state.system.taste_share ?? 0.25) * 100)}% share and is learned from what you like and reject.`));
  const pages = [form.sections.safety, form.sections.taste,
    hasHealth ? h("div.stack", form.sections.goal, h("div.divider"), form.sections.limits) : form.sections.limits, dishes, prio];
  function go(i) {
    step = Math.max(0, Math.min(4, i));
    clear(head, h("div.small.muted", `Step ${step + 1} of 5`), h("h2", { style: { fontSize: "18px", marginTop: "4px" } }, titles[step]),
      h("p.muted", { style: { marginTop: "2px" } }, subs[step]), ind);
    clear(ind, [0, 1, 2, 3, 4].map((j) => h("span" + (j <= step ? ".on" : ""))));
    clear(body, pages[step]);
    back.disabled = step === 0;
    next.textContent = step === 4 ? "Start" : "Continue";
  }
  async function finish() {
    const tot = VALUES.reduce((a, k) => a + raw[k], 0);
    if (!tot) return toast("Give at least one value a weight above zero", "err");
    const declared = { ...form.value(), liked_recipes: [...picked.keys()] };
    try {
      await api(userPath("/onboard"), { method: "POST", body: { declared, weights: Object.fromEntries(VALUES.map((k) => [k, raw[k] / tot])) } });
      overlay.remove();
      onDone();
    } catch (e) { toast(e.message, "err"); }
  }
  const overlay = h("div.overlay", h("div.modal.wide", { role: "dialog", "aria-modal": "true" }, head, body,
    h("div.modal-foot", back, h("span.small.muted", "Stored locally in this app's database."), next)));
  go(0);
  document.body.append(overlay);
}
