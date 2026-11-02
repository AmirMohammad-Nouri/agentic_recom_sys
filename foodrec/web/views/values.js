// "Your values" (Profile): how much each value counts, diversity, computation mode and place.
// Saving creates a new goal version; Discover then ranks with it.
import { api, clear, CRIT_META, emit, f2, h, icon, on, pct, state, toast, userPath, VALUES, weightBar } from "../lib.js";

export function valuesPanel() {
  const goal = state.user_state.goal;
  const raw = Object.fromEntries(VALUES.map((k) => [k, Math.round((goal?.weights[k] ?? 0.33) * 100)]));
  let diversity = goal?.diversity ?? 0.3;
  let mode = goal?.mode ?? "lightweight";
  state.context = state.context || { meal: "any", place: "home" };
  const taste = state.system?.taste_share ?? 0.25;

  const norm = () => {
    const s = VALUES.reduce((a, k) => a + raw[k], 0);
    return s > 0 ? Object.fromEntries(VALUES.map((k) => [k, raw[k] / s])) : null;
  };
  const bar = h("div");
  const zeroNote = h("div.hint.hidden", { style: { color: "var(--warn)" } }, "All values are at zero. Raise any slider before saving.");
  const sliders = VALUES.map((k) => {
    const val = h("span.val");
    const input = h("input", { type: "range", min: 0, max: 100, step: 5, value: raw[k], "aria-label": CRIT_META[k].long });
    input.addEventListener("input", () => { raw[k] = +input.value; refresh(); schedulePreview(); });
    return { k, val, input, row: h("div.slider",
      h("span.name", { title: CRIT_META[k].what }, h("span.swatch", { style: { background: CRIT_META[k].color } }), CRIT_META[k].long), val, input,
      h("div.ends", h("span", CRIT_META[k].lo), h("span", CRIT_META[k].hi))) };
  });
  const divVal = h("span.val");
  const divInput = h("input", { type: "range", min: 0, max: 100, step: 5, value: Math.round(diversity * 100), "aria-label": "Diversity" });
  divInput.addEventListener("input", () => { diversity = divInput.value / 100; refresh(); schedulePreview(); });
  const modeSeg = h("div.seg", ["lightweight", "extended"].map((m) => h("button", {
    onclick: () => { mode = m; refresh(); schedulePreview(); },
  }, m === "lightweight" ? "Lightweight" : "Extended")));
  const modeDesc = h("div.hint");
  const preview = h("div.hint", { style: { minHeight: "18px" } });
  const goalTag = h("span.tag");
  const saveBtn = h("button.btn.primary", { onclick: () => saveGoal() }, "Save as new goal");
  const unsaved = h("div.small.muted");

  const differsFromGoal = () => {
    const g = state.user_state.goal, n = norm();
    if (!g || !n) return false;
    return VALUES.some((k) => Math.abs((n[k] ?? 0) - (g.weights[k] ?? 0)) >= 0.005) ||
      Math.abs(diversity - g.diversity) >= 0.005 || mode !== g.mode;
  };

  function refresh() {
    const n = norm();
    for (const s of sliders) clear(s.val, h("b", pct(n ? n[s.k] : 0)));
    const eff = n || state.user_state.goal.weights;
    clear(bar, weightBar({ p: taste, ...Object.fromEntries(VALUES.map((k) => [k, (1 - taste) * eff[k]])) }));
    zeroNote.classList.toggle("hidden", !!n);
    clear(divVal, h("b", f2(diversity)));
    [...modeSeg.children].forEach((b, i) => b.classList.toggle("on", ["lightweight", "extended"][i] === mode));
    modeDesc.textContent = state.system?.modes?.[mode]?.description || "";
    const g = state.user_state.goal;
    goalTag.textContent = `Saved goal v${g.version}`;
    const diff = differsFromGoal();
    unsaved.textContent = diff ? `Not saved yet. Saving creates goal v${g.version + 1}.` : "These are your saved values.";
    saveBtn.disabled = !diff || !n;
  }

  let timer = null;
  function schedulePreview() {
    clearTimeout(timer);
    if (!differsFromGoal() || !norm()) { preview.textContent = ""; return; }
    preview.textContent = "Previewing…";
    timer = setTimeout(async () => {
      try {
        const c = (await api(userPath("/preview"), { method: "POST", body: { weights: norm(), diversity, mode } })).comparison;
        if (!c) { preview.textContent = ""; return; }
        const n = c.entered.length + c.left.length + c.moved.length;
        preview.textContent = n === 0 ? "Your top dishes would stay the same."
          : `Your top dishes would change: ${c.entered.length} in, ${c.left.length} out, ${c.moved.length} moved.`;
      } catch { preview.textContent = ""; }
    }, 450);
  }

  async function saveGoal() {
    const n = norm();
    if (!n) return;
    try {
      const out = await api(userPath("/goal"), { method: "POST", body: { name: "My priorities", weights: n, diversity, mode } });
      state.user_state = await api(userPath("/state"));
      state.lastResult = null;
      toast(`Saved as goal v${out.goal_version}`);
      preview.textContent = "";
      refresh();
      emit("feed", { title: `Goal v${out.goal_version} saved`, ts: Date.now() / 1000,
        steps: [{ id: "g" + out.goal_version, kind: "perceive", source: "learner", status: "done", title: "New goal recorded",
          detail: "Your observed weights moved halfway toward the new goal, and the event count for the supervisor restarts.",
          evidence: out.trail }] });
    } catch (e) { toast(e.message, "err"); }
  }

  function setControls(g) {
    for (const s of sliders) { raw[s.k] = Math.round((g.weights[s.k] ?? 0) * 100); s.input.value = raw[s.k]; }
    diversity = g.diversity; divInput.value = Math.round(diversity * 100);
    mode = g.mode;
    preview.textContent = "";
    refresh();
  }

  const off = on(async (what, data) => {
    // accepting a supervisor proposal creates a new goal version: show it
    if (what === "decision" && data?.decision?.status === "accepted") {
      state.user_state = await api(userPath("/state"));
      setControls(state.user_state.goal);
    }
  });
  refresh();

  const place = h("select", { onchange: (e) => (state.context.place = e.target.value), "aria-label": "Place" },
    ["home", "work", "out"].map((m) => h("option", { value: m, selected: state.context.place === m }, "at " + m)));
  const el = h("div.values-card.two-col",
    h("section.card",
      h("div.card-head", h("h2", "Your values"), goalTag),
      h("div.card-body.stack",
        h("p.small.muted", "How much each value counts when dishes are ranked."),
        h("div.stack", sliders.map((s) => s.row)),
        h("div.stack.tight", h("div.field-label", "Share of the ranking"), bar, zeroNote,
          h("div.hint", `Taste keeps a fixed ${pct(taste)} share, learned from your reactions; the sliders share the rest.`)))),
    h("div.stack",
      h("section.card",
        h("div.card-head", h("h2", "Diversity")),
        h("div.card-body.stack",
          h("div.slider", h("span.name", "Variety in a list"), divVal, divInput, h("div.ends", h("span", "Similar dishes"), h("span", "Varied dishes"))),
          h("div.stack.tight", h("div.field-label", "Computation"), modeSeg, modeDesc),
          h("div.stack.tight", h("div.field-label", "Place (recorded with your reactions)"), place))),
      h("section.card",
        h("div.card-body.stack", unsaved, preview,
          h("div.btn-row", saveBtn,
            h("button.btn.ghost", { onclick: () => setControls(state.user_state.goal), title: "Back to your saved goal" }, icon("reset"), "Reset to saved"))))));
  el.cleanup = off;
  return el;
}
