// Reusable UI pieces: recipe card, decision card, change summary, comparison table.
import { api, ask, can, co2, CRIT, CRIT_META, clear, emit, f2, grade, h, human, icon, menu, pct, state, toast, track, userPath, VALUES, visibleCriteria, weightBar, ago } from "./lib.js";

// Feedback reasons come from the configured interactions (foodrec.config.json "interactions").
const reasons = (kind) => (state.system?.interactions?.[`${kind}_reasons`] || []).filter((r) => r.show !== false);
const REJECT = () => reasons("reject").map((r) => [r.id, r.label]);
const CHOOSE = () => [...reasons("choose").map((r) => [r.id, r.label]), [null, "No particular reason"]];
// Reasons that may be a one-off: the user says whether they should change what is learned.
const scoped = (reason) => [...reasons("choose"), ...reasons("reject")].some((r) => r.id === reason && r.ask_scope);

export async function sendFeedback(r, requestId, type, reason, scope = "lasting") {
  try {
    const out = await api(userPath("/feedback"), {
      method: "POST",
      body: { request_id: requestId, recipe_id: r.recipe_id, type, reason, scope, context: state.context || {} },
    });
    if (out.duplicate) { toast("Already recorded for this list"); return out; }
    emit("review", { ...out.review, recipe: r.title, type, reason, scope, ts: Date.now() / 1000 });
    if (out.review.decisions.length) toast("The supervisor has a question for you");
    return out;
  } catch (e) {
    toast(e.message, "err");
    throw e;
  }
}

export const personalFits = (r) => (r.evidence?.fit || []).filter((f) => f.type !== "constraint").map((f) => f.text);
const ruleCount = (r) => (r.evidence?.fit || []).filter((f) => f.type === "constraint").length;

function thumb(r) {
  const box = h("div.thumb", icon("bowl"));
  if (r.image_url) {
    const img = h("img", { src: r.image_url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
    img.addEventListener("error", () => img.remove());
    box.append(img);
  }
  return box;
}

// Three small score chips: what the recipe scores on each value criterion.
export function scoreChips(r) {
  const keys = visibleCriteria(VALUES);
  if (!keys.length) return null;
  return h("div.score-chips", keys.map((k) => {
    const v = r.scores?.[k];
    return h("span.score-chip", { title: `${CRIT_META[k].long}: ${CRIT_META[k].what}` },
      h("span.sc-bar", h("span", { style: { width: pct(v || 0), background: CRIT_META[k].color } })),
      h("span.sc-label", CRIT_META[k].label), h("b", f2(v)));
  }));
}

// What a card shows depends on the user's access role (Config page); the server has already
// removed the fields the role may not see, so every part below also checks that its data exists.
export function recipeCard(r, { requestId, compareWith = [] } = {}) {
  const ev = r.evidence || {};
  const rk = ev.ranking || {};
  const top = rk.top_contributor;
  const dv = rk.diversity || {};
  const status = h("span.done");
  const detailBox = h("div.hidden");
  const explain = can("explanations");
  const details = can("food_details");
  const showUtility = can("utility") && r.utility != null;

  const contribW = {};
  const contribs = rk.contributions || {};
  const total = Object.values(contribs).reduce((a, c) => a + c.contribution, 0) || 1;
  for (const k of CRIT) contribW[k] = (contribs[k]?.contribution || 0) / total;

  const act = async (type, reason, scope = "lasting") => {
    try {
      const out = await sendFeedback(r, requestId, type, reason, scope);
      if (out.duplicate) return;
      clear(status, icon("check"), ` ${human(type === "choose" ? "chosen" : type + "d")}${reason ? " · " + human(reason) : ""}${scope === "today" ? " · today only" : ""}`);
    } catch { /* toast already shown */ }
  };
  // Second step for reasons that may be temporary: only today, or update what is learned.
  const askScope = (anchor, type, reason) => {
    if (!scoped(reason)) return act(type, reason);
    setTimeout(() => menu(anchor, [{ label: "Is this lasting?" },
      { text: "Update my preference", run: () => act(type, reason, "lasting") },
      { text: "Only today", run: () => act(type, reason, "today") }]), 0);
  };

  return h("article.recipe",
    h("div.rank-col", h("div.rank", r.final_rank), thumb(r)),
    h("div",
      h("div.row.between", { style: { alignItems: "flex-start" } },
        h("div.grow",
          h("h3", r.url ? h("a.title-link", { href: r.url, target: "_blank", rel: "noopener" }, r.title) : r.title),
          h("div.meta",
            h("span.m", r.cuisine === "Other" ? human(r.meal) : `${r.cuisine} · ${human(r.meal)}`),
            details ? [
              h("span.m", { title: "Health grade" }, grade(r.health_grade)),
              h("span.m", icon("list"), `${r.n_ingredients} ingredients`),
              h("span.m", icon("flame"), r.kcal == null ? "kcal unknown" : `${Math.round(r.kcal)} kcal`),
              h("span.m", icon("leaf"), co2(r.co2e_kg_per_kg))] : null)),
        showUtility ? h("div.util", h("div", f2(r.utility)), h("div.small.muted", "utility")) : null),
      scoreChips(r),
      showUtility && rk.contributions ? h("div.contrib", { title: "Share of the utility contributed by each criterion" }, weightBar(contribW, visibleCriteria())) : null,
      explain ? h("div.reason",
        top && contribs[top] ? [h("b", CRIT_META[top].long), ` contributes most (${f2(contribs[top].contribution)} of ${f2(r.utility)}). `] : null,
        dv.promoted ? h("span.tag.info", { title: r.reasons.ranked_here.find((x) => x.includes("diversity")) || "" },
          `moved up from utility rank ${rk.utility_rank} for variety`) : null,
        dv.demoted ? h("span.tag", `utility rank ${rk.utility_rank}`) : null) : null,
      explain && personalFits(r).length ? h("ul.reason-list.fit", personalFits(r).slice(0, 2).map((x) => h("li", x))) : null,
      explain && ruleCount(r) ? h("div.small.muted", { style: { marginTop: "4px" }, title: r.evidence.fit.filter((f) => f.type === "constraint").map((f) => f.text).join("\n") },
        `Passes your ${ruleCount(r)} mandatory rule${ruleCount(r) === 1 ? "" : "s"}`) : null,
      explain && r.reasons?.compromises?.length ? h("ul.reason-list.comp", r.reasons.compromises.slice(0, 1).map((x) => h("li", x))) : null,
      (r.missing_info?.length || (explain && r.repetition)) ? h("div.row.wrap", { style: { marginTop: "4px", gap: "4px" } },
        explain && r.repetition ? h("span.tag.warn", { title: `Temporary penalty; utility ${f2(r.repetition.utility_before_penalty)} before it` },
          `Had it ${ago(r.repetition.last_eaten_ts)} · -${f2(r.repetition.penalty)}`) : null,
        (r.missing_info || []).map((m) => h("span.tag.warn", { title: m.text }, m.criterion ? `Missing data: ${CRIT_META[m.criterion].long.toLowerCase()}` : "Weights adjusted for missing data"))) : null,
      h("div.recipe-actions",
        can("feedback") ? [
          h("button.btn.sm", { onclick: () => act("like") }, icon("heart"), "Like"),
          h("button.btn.sm", { onclick: () => act("save") }, icon("bookmark"), "Save"),
          h("button.btn.sm", { onclick: (e) => { const b = e.currentTarget; menu(b, [{ label: "Why this one?" },
            ...CHOOSE().map(([k, t]) => ({ text: t, run: () => askScope(b, "choose", k) }))]); } }, icon("check"), "I'll cook this"),
          h("button.btn.sm.ghost", { onclick: (e) => { const b = e.currentTarget; menu(b, [{ label: "What is wrong with it?" },
            ...REJECT().map(([k, t]) => ({ text: t, run: () => askScope(b, "reject", k) }))]); } }, icon("x"), "Not for me")] : null,
        h("span.grow"),
        status,
        can("assistant") && explain ? h("button.btn.sm.ghost", { onclick: () => ask(`Why is #${r.final_rank} ${r.title} recommended?`), title: "Ask the assistant about this recipe" },
          icon("spark"), "Ask") : null,
        hasDetails(r) ? h("button.btn.sm.ghost", {
          onclick: (e) => {
            detailBox.classList.toggle("hidden");
            if (!detailBox.firstChild) detailBox.append(recipeDetails(r, compareWith));
            const label = detailsLabel();
            e.currentTarget.lastChild.textContent = detailBox.classList.contains("hidden") ? label : "Hide";
            if (!detailBox.classList.contains("hidden")) track("details_opened", { recipe_id: r.recipe_id, rank: r.final_rank });
          },
        }, icon("info"), detailsLabel()) : null),
      detailBox));
}

// ---- score breakdown: how each of the three scores was calculated ------------------------
export function scoreBreakdown(bd) {
  // one section per enabled criterion; built-ins have their own layout, field criteria a generic one
  const on = (k) => VALUES.includes(k) && bd[k];
  const fieldSection = (k) => {
    const c = bd[k];
    const better = c.direction === "lower" ? "lower is better" : "higher is better";
    return h("section",
      h("div.bd-head", h("span.swatch", { style: { background: CRIT_META[k].color } }), h("b", CRIT_META[k].long),
        h("span.bd-score", f2(c.score)), h("span.small.muted", c.value == null ? "value unknown" : `${c.value} ${c.unit || ""}`)),
      CRIT_META[k].what ? h("div.small.muted", CRIT_META[k].what) : null,
      h("div.formula", `From ${c.field} (${better}): ${c.anchors.best} → 1, ${c.anchors.worst} → 0, ${c.scale}. Score = ${c.formula}.`));
  };
  const hh = bd.h, ss = bd.s, tt = bd.t;
  const fac = hh.factors.map((f) => h("tr" + (f.score == null ? ".muted" : ""),
    h("td", f.label, f.flipped ? h("span.tag.info", { title: "Reversed for the goal: more is better" }, "reversed") : null),
    h("td.num", f.value == null ? "–" : `${f.value} ${f.unit}`),
    h("td.num.small.muted", `${f.good} → ${f.poor}`),
    h("td.num", f.weight === 1 ? "1" : h("b", f.weight)),
    h("td.bar-cell", f.score == null ? h("span.small.muted", "missing") :
      h("div.row", h("div.minibar.grow", h("span", { style: { width: pct(f.score), background: "var(--c-h)" } })), h("span.small", f2(f.score))))));
  const fp = (ss.breakdown || []).filter((b) => b.share >= 0.01).slice(0, 6);
  return h("div.breakdown",
    on("h") ? h("section",
      h("div.bd-head", h("span.swatch", { style: { background: CRIT_META.h.color } }), h("b", CRIT_META.h.long), grade(hh.grade),
        h("span.bd-score", f2(hh.score)), h("span.small.muted", `goal: ${hh.goal_label}`)),
      h("table.t", h("thead", h("tr", h("th", "Factor (per 100 g)"), h("th.num", "Value"), h("th.num", "Good → poor"), h("th.num", "Weight"), h("th", "Score"))),
        h("tbody", fac)),
      h("div.formula", "Healthiness = Σ weight × factor score / Σ weight", hh.missing.length ? ` · missing: ${hh.missing.join(", ")}` : "")) : null,
    on("s") ? h("section",
      h("div.bd-head", h("span.swatch", { style: { background: CRIT_META.s.color } }), h("b", CRIT_META.s.long),
        h("span.bd-score", f2(ss.score)), h("span.small.muted", co2(ss.kg_co2e_per_kg) + (ss.kg_co2e_per_serving != null ? ` · ${f2(ss.kg_co2e_per_serving)} kg per serving` : ""))),
      fp.length ? h("div.fp-bars", fp.map((b) => h("div.fp-row",
        h("span.fp-name", b.name, h("span.small.muted", ` · ${human(b.category)}`)),
        h("div.minibar", h("span", { style: { width: pct(b.share), background: "var(--c-s)" } })),
        h("span.small", `${pct(b.share)}`), h("span.small.muted", `${Math.round(b.grams)} g × ${b.factor}`)))) : null,
      h("div.formula", `Footprint = Σ grams × food-group factor / total grams (Poore & Nemecek 2018). Score: log scale, ${ss.anchors.best} → 1, ${ss.anchors.worst} → 0.`)) : null,
    on("t") ? h("section",
      h("div.bd-head", h("span.swatch", { style: { background: CRIT_META.t.color } }), h("b", CRIT_META.t.long),
        h("span.bd-score", f2(tt.score)), h("span.small.muted", `${tt.n_ingredients} ingredients`)),
      h("div.chips", tt.ingredients.map((n) => h("span.tag", n))),
      h("div.formula", `Score = (${tt.anchors.worst} − n) / (${tt.anchors.worst} − ${tt.anchors.best}), clipped to 0–1.`)) : null,
    VALUES.filter((k) => bd[k]?.kind === "field").map(fieldSection));
}

// The details panel: "How it's scored" when scores are visible, otherwise just the food details.
const hasDetails = (r) => (can("utility") && r.contributions) || (can("score_breakdown") && r.breakdown) ||
  can("explanations") || (can("food_details") && r.ingredients_detail);
const detailsLabel = () => (can("utility") || can("score_breakdown") || can("explanations") ? "How it's scored" : "Details");

function recipeDetails(r, compareWith) {
  const ev = r.evidence || {};
  const rk = ev.ranking || {};
  const explain = can("explanations");
  const rows = visibleCriteria().map((k) => {
    const c = rk.contributions?.[k];
    const w = r.effective_weights?.[k];
    return h("tr",
      h("td", h("span.row", h("span.swatch", { style: { background: CRIT_META[k].color } }), CRIT_META[k].long)),
      h("td.num", c ? f2(c.score) : h("span.tag.warn", "missing")),
      h("td.num", pct(w)),
      h("td.num", c ? f2(c.contribution) : "–"),
      h("td.bar-cell", h("div.minibar", h("span", { style: { width: `${(c?.score || 0) * 100}%`, background: CRIT_META[k].color } }))));
  });
  const cmpSel = h("select", h("option", { value: "" }, "Compare with…"),
    compareWith.filter((o) => o.recipe_id !== r.recipe_id).map((o) => h("option", { value: o.recipe_id }, `#${o.final_rank} ${o.title}`)));
  const cmpBox = h("div");
  cmpSel.addEventListener("change", () => {
    const o = compareWith.find((x) => x.recipe_id === cmpSel.value);
    clear(cmpBox, o ? compareTable(r, o) : null);
  });
  const rs = r.reasons || {};
  return h("div.details",
    can("utility") && r.utility != null && rows.length ? h("div",
      h("div.section-label", "Why it ranked here"),
      h("table.t", h("thead", h("tr", h("th", "Criterion"), h("th.num", "Score"), h("th.num", "Weight"), h("th.num", "Contribution"), h("th", ""))), h("tbody", rows)),
      h("div.small.muted", { style: { marginTop: "6px" } },
        `Utility ${f2(r.utility)} = Σ weight × score (taste keeps a fixed share). Utility rank ${rk.utility_rank}, final rank ${rk.final_rank}.`,
        rk.diversity?.max_similarity_to_earlier ? ` Similarity to earlier picks ${f2(rk.diversity.max_similarity_to_earlier)}.` : "")) : null,
    can("score_breakdown") && r.breakdown ? h("div", h("div.section-label", "How each score is calculated"), scoreBreakdown(r.breakdown)) : null,
    explain && rs.ranked_here?.length ? h("div", h("div.section-label", "Ranking notes"), h("ul.reason-list", rs.ranked_here.map((x) => h("li", x)))) : null,
    explain && rs.fits?.length ? h("div", h("div.section-label", "Fits you because"), h("ul.reason-list.fit", rs.fits.map((x) => h("li", x)))) : null,
    explain && rs.compromises?.length ? h("div", h("div.section-label", "Compromises"), h("ul.reason-list.comp", rs.compromises.map((x) => h("li", x)))) : null,
    explain && rs.gaps?.length ? h("div", h("div.section-label", "Uncertain or estimated"), h("ul.reason-list", rs.gaps.map((x) => h("li", x)))) : null,
    r.ingredients_detail ? h("div", h("div.section-label", "Ingredients"),
      h("ul.ing-list", r.ingredients_detail.map((i) => h("li", i.text || i.name, h("span.small.muted", ` · ≈${Math.round(i.grams)} g`))))) : null,
    compareWith.length > 1 && visibleCriteria().length ? h("div", h("div.section-label", "Compare"), h("div", { style: { margin: "6px 0" } }, cmpSel), cmpBox) : null);
}

export function compareTable(a, b) {
  const showU = can("utility") && a.utility != null && b.utility != null && !Number.isNaN(a.utility);
  return h("table.t",
    h("thead", h("tr", h("th", ""), h("th.num", a.title), h("th.num", b.title), h("th.num", "Δ"))),
    h("tbody", visibleCriteria().map((k) => {
      const x = a.scores[k], y = b.scores[k];
      const d = x == null || y == null ? null : x - y;
      return h("tr", h("td", CRIT_META[k].long), h("td.num", f2(x)), h("td.num", f2(y)),
        h("td.num" + (d > 0.04 ? ".delta-up" : d < -0.04 ? ".delta-down" : ""), d == null ? "–" : (d > 0 ? "+" : "") + f2(d)));
    }), showU ? h("tr", h("td", h("b", "Utility")), h("td.num", h("b", f2(a.utility))), h("td.num", h("b", f2(b.utility))),
      h("td.num", (a.utility - b.utility > 0 ? "+" : "") + f2(a.utility - b.utility))) : null));
}

export function changeSummary(cmp, titles) {
  if (!cmp) return null;
  const parts = [];
  if (!cmp.changed_inputs.length) return null;
  const inputs = cmp.changed_inputs.map((c) =>
    typeof c.before === "number" ? `${c.label} ${f2(c.before)} → ${f2(c.after)}` : `${c.label}: ${human(c.before)} → ${human(c.after)}`);
  parts.push(h("div", h("b", "What changed: "), inputs.join("; ")));
  const name = (id) => titles[id] || cmp.detail?.[id]?.title || id;
  const res = [];
  if (cmp.entered.length) res.push(h("span.delta-up", `${cmp.entered.length} new: ${cmp.entered.map(name).join(", ")}`));
  if (cmp.left.length) res.push(h("span.delta-down", `${cmp.left.length} left: ${cmp.left.map(name).join(", ")}`));
  if (cmp.moved.length) res.push(h("span", `${cmp.moved.length} moved`));
  if (!res.length) res.push(h("span", "The list stayed the same."));
  parts.push(h("div.row.wrap", { style: { marginTop: "3px", gap: "4px 14px" } }, res));
  const drivers = Object.entries(cmp.detail || {}).filter(([, d]) => d.main_driver && Math.abs(d.main_driver.contribution_change) >= 0.005).slice(0, 2)
    .map(([, d]) => `${d.title}: ${CRIT_META[d.main_driver.criterion].long.toLowerCase()} contribution ${d.main_driver.contribution_change > 0 ? "+" : ""}${f2(d.main_driver.contribution_change)}`);
  if (drivers.length) parts.push(h("div.small.muted", { style: { marginTop: "3px" } }, "Main driver — " + drivers.join("; ")));
  return h("div.change", parts);
}

export function decisionCard(d, { onResolved } = {}) {
  const b = d.body;
  const kindLabel = { goal_proposal: "Proposal", goal_options: "Pick an option", tradeoff_question: "Question", taste_question: "Question", profile_change: "Needs your confirmation" }[d.kind] || d.kind;
  const box = h("div.decision.k-" + d.kind);
  const resolve = async (action, answer) => {
    try {
      const out = await api(userPath(`/decisions/${d.decision_id}`), { method: "POST", body: { action, answer } });
      toast(action === "accept" ? "Saved. A new version was created; you can revert it in Profile." : "Recorded");
      emit("decision", out);
      onResolved?.(out);
      const today = String(answer || "").startsWith("today");
      clear(box, h("div.small.muted", `${kindLabel} · ${out.decision.status}`), h("h4", d.title),
        h("div.resolved", action === "dismiss" ? "Dismissed. Your saved goal is unchanged." :
          answer === "keep_goal" ? "Keeping your goal. What was learned from your choices is kept; I'll only ask again if they drift further." :
          today ? "Used for today only. Nothing lasting was saved or learned." :
          action === "answer" ? "Thanks — recorded as evidence. Your saved goal was not changed." : "Applied as a new version."));
    } catch (e) { toast(e.message, "err"); }
  };
  const kids = [h("div.row.between", h("span.tag." + (d.kind.includes("question") ? "info" : "accent"), kindLabel),
    h("span.small.muted", ago(d.ts))), h("h4", d.title), h("p", b.explanation)];
  if (d.kind === "goal_proposal") {
    kids.push(h("div.stack.tight", { style: { marginTop: "10px" } },
      h("div.small.muted", "Saved goal"), weightBar(b.stated, VALUES),
      h("div.small.muted", "Proposed"), weightBar(b.proposed_weights, VALUES)));
    const cf = b.counterfactual || {};
    if (cf.entered?.length || cf.left?.length) {
      kids.push(h("div.small", { style: { marginTop: "8px" } },
        cf.entered?.length ? h("div.delta-up", "Would enter: " + cf.entered.join(", ")) : null,
        cf.left?.length ? h("div.delta-down", "Would leave: " + cf.left.join(", ")) : null));
    }
    kids.push(h("div.small.muted", { style: { marginTop: "6px" } }, `Based on ${b.evidence_events?.length || 0} recorded events.`));
  }
  if (d.kind === "profile_change") {
    kids.push(h("div.small.muted", { style: { marginTop: "6px" } }, `Eligible recipes: ${b.eligible_before} → ${b.eligible_after}`));
  }
  if (d.kind === "goal_options") {
    const avg = (a) => VALUES.map((k) => `${CRIT_META[k].label} ${f2(a[k])}`).join(" · ");
    kids.push(h("div.stack.tight", { style: { marginTop: "10px" } },
      h("div.small.muted", "Saved goal now"), weightBar(b.base.weights, VALUES),
      h("details.attach-details", h("summary", "Current top five"), h("ol.small", b.base.recipes.map((r) => h("li", r.title))),
        h("div.small.muted", "Average scores: " + avg(b.base.avg_scores)))));
    for (const o of b.options) {
      const delta = VALUES.filter((k) => Math.abs(o.avg_change[k]) >= 0.005)
        .map((k) => h("span" + (o.avg_change[k] > 0 ? ".delta-up" : ".delta-down"), `${CRIT_META[k].label} ${o.avg_change[k] > 0 ? "+" : ""}${f2(o.avg_change[k])}`));
      kids.push(h("div.card", { style: { padding: "8px 10px", marginTop: "8px" } },
        h("div.row.between", h("b.small", o.label), h("span.small.muted", `${o.entered.length} new`)),
        weightBar(o.weights, VALUES),
        h("div.row.wrap.small", { style: { gap: "4px 12px", marginTop: "4px" } }, delta.length ? delta : h("span.muted", "Top five averages barely change")),
        h("ol.small", { style: { margin: "6px 0 0 18px" } }, o.recipes.map((r) => h("li" + (o.entered.includes(r.recipe_id) ? ".delta-up" : ""), r.title))),
        d.status === "open" ? h("div.btn-row",
          h("button.btn.primary.sm", { onclick: () => resolve("accept", o.id) }, "Save this goal"),
          h("button.btn.sm", { onclick: () => resolve("answer", `today:${o.id}`) }, "Only today")) : null));
    }
  }
  if (d.status !== "open") {
    kids.push(h("div.resolved", `Status: ${d.status}${d.resolved_ts ? " · " + ago(d.resolved_ts) : ""}`));
  } else if (d.kind === "goal_proposal") {
    kids.push(h("div.btn-row", h("button.btn.primary.sm", { onclick: () => resolve("accept") }, "Update my goal"),
      h("button.btn.sm", { onclick: () => resolve("answer", "today") }, "Only today"),
      h("button.btn.sm", { onclick: () => resolve("answer", "keep_goal") }, "Keep my goal")));
  } else if (d.kind === "goal_options") {
    kids.push(h("div.btn-row", h("button.btn.sm", { onclick: () => resolve("dismiss") }, "Keep my goal")));
  } else if (d.kind === "profile_change") {
    kids.push(h("div.btn-row", h("button.btn.primary.sm", { onclick: () => resolve("accept") }, "Confirm"),
      h("button.btn.sm", { onclick: () => resolve("dismiss") }, "Cancel")));
  } else {
    kids.push(h("div.btn-row", b.options.map((o) => h("button.btn.sm", { onclick: () => resolve("answer", o.id) }, o.label)),
      h("button.btn.sm.ghost", { onclick: () => resolve("dismiss") }, "Not now")));
  }
  return clear(box, kids);
}
