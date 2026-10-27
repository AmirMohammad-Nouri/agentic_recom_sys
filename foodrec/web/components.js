// Reusable UI pieces: recipe card, decision card, change summary, comparison table.
import { api, ask, co2, CRIT, CRIT_META, clear, emit, f2, grade, h, human, icon, menu, pct, state, toast, userPath, VALUES, weightBar, ago } from "./lib.js";

const REJECT = [["not_my_taste", "Not my taste"], ["not_healthy_enough", "Not healthy enough"],
  ["not_sustainable", "Not sustainable enough"], ["too_complicated", "Too many ingredients"], ["had_recently", "Had it recently"]];
const CHOOSE = [["taste", "It sounds tasty"], ["health", "It is healthy"], ["climate", "It is climate-friendly"],
  ["easy", "It is easy to make"], [null, "No particular reason"]];

export async function sendFeedback(r, requestId, type, reason) {
  try {
    const out = await api(userPath("/feedback"), {
      method: "POST",
      body: { request_id: requestId, recipe_id: r.recipe_id, type, reason, context: state.context || {} },
    });
    emit("review", { ...out.review, recipe: r.title, type, reason, ts: Date.now() / 1000 });
    if (out.review.decisions.length) toast("The supervisor has a question for you");
    return out;
  } catch (e) {
    toast(e.message, "err");
    throw e;
  }
}

export const personalFits = (r) => r.evidence.fit.filter((f) => f.type !== "constraint").map((f) => f.text);
const ruleCount = (r) => r.evidence.fit.filter((f) => f.type === "constraint").length;

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
  return h("div.score-chips", VALUES.map((k) => {
    const v = r.scores?.[k];
    return h("span.score-chip", { title: `${CRIT_META[k].long}: ${CRIT_META[k].what}` },
      h("span.sc-bar", h("span", { style: { width: pct(v || 0), background: CRIT_META[k].color } })),
      h("span.sc-label", CRIT_META[k].label), h("b", f2(v)));
  }));
}

export function recipeCard(r, { requestId, compareWith = [] } = {}) {
  const ev = r.evidence;
  const rk = ev.ranking;
  const top = rk.top_contributor;
  const dv = rk.diversity;
  const status = h("span.done");
  const detailBox = h("div.hidden");

  const contribW = {};
  const total = Object.values(rk.contributions).reduce((a, c) => a + c.contribution, 0) || 1;
  for (const k of CRIT) contribW[k] = (rk.contributions[k]?.contribution || 0) / total;

  const act = async (type, reason) => {
    try {
      await sendFeedback(r, requestId, type, reason);
      clear(status, icon("check"), ` ${human(type === "choose" ? "chosen" : type + "d")}${reason ? " · " + human(reason) : ""}`);
    } catch { /* toast already shown */ }
  };

  return h("article.recipe",
    h("div.rank-col", h("div.rank", r.final_rank), thumb(r)),
    h("div",
      h("div.row.between", { style: { alignItems: "flex-start" } },
        h("div.grow",
          h("h3", r.url ? h("a.title-link", { href: r.url, target: "_blank", rel: "noopener" }, r.title) : r.title),
          h("div.meta",
            h("span.m", r.cuisine === "Other" ? human(r.meal) : `${r.cuisine} · ${human(r.meal)}`),
            h("span.m", { title: "Health grade" }, grade(r.health_grade)),
            h("span.m", icon("list"), `${r.n_ingredients} ingredients`),
            h("span.m", icon("flame"), r.kcal == null ? "kcal unknown" : `${Math.round(r.kcal)} kcal`),
            h("span.m", icon("leaf"), co2(r.co2e_kg_per_kg)))),
        h("div.util", h("div", f2(r.utility)), h("div.small.muted", "utility"))),
      scoreChips(r),
      h("div.contrib", { title: "Share of the utility contributed by each criterion" }, weightBar(contribW)),
      h("div.reason",
        top ? [h("b", CRIT_META[top].long), ` contributes most (${f2(rk.contributions[top].contribution)} of ${f2(r.utility)}). `] : null,
        dv.promoted ? h("span.tag.info", { title: r.reasons.ranked_here.find((x) => x.includes("diversity")) || "" },
          `moved up from utility rank ${rk.utility_rank} for variety`) : null,
        dv.demoted ? h("span.tag", `utility rank ${rk.utility_rank}`) : null),
      personalFits(r).length ? h("ul.reason-list.fit", personalFits(r).slice(0, 2).map((x) => h("li", x))) : null,
      ruleCount(r) ? h("div.small.muted", { style: { marginTop: "4px" }, title: r.evidence.fit.filter((f) => f.type === "constraint").map((f) => f.text).join("\n") },
        `Passes your ${ruleCount(r)} mandatory rule${ruleCount(r) === 1 ? "" : "s"}`) : null,
      r.reasons.compromises.length ? h("ul.reason-list.comp", r.reasons.compromises.slice(0, 1).map((x) => h("li", x))) : null,
      h("div.recipe-actions",
        h("button.btn.sm", { onclick: () => act("like") }, icon("heart"), "Like"),
        h("button.btn.sm", { onclick: () => act("save") }, icon("bookmark"), "Save"),
        h("button.btn.sm", { onclick: (e) => menu(e.currentTarget, [{ label: "Why this one?" },
          ...CHOOSE.map(([k, t]) => ({ text: t, run: () => act("choose", k) }))]) }, icon("check"), "I'll cook this"),
        h("button.btn.sm.ghost", { onclick: (e) => menu(e.currentTarget, [{ label: "What is wrong with it?" },
          ...REJECT.map(([k, t]) => ({ text: t, run: () => act("reject", k) }))]) }, icon("x"), "Not for me"),
        h("span.grow"),
        status,
        h("button.btn.sm.ghost", { onclick: () => ask(`Why is #${r.final_rank} ${r.title} recommended?`), title: "Ask the assistant about this recipe" },
          icon("spark"), "Ask"),
        h("button.btn.sm.ghost", {
          onclick: (e) => {
            detailBox.classList.toggle("hidden");
            if (!detailBox.firstChild) detailBox.append(recipeDetails(r, compareWith));
            e.currentTarget.lastChild.textContent = detailBox.classList.contains("hidden") ? "How it's scored" : "Hide";
          },
        }, icon("info"), "How it's scored")),
      detailBox));
}

// ---- score breakdown: how each of the three scores was calculated ------------------------
export function scoreBreakdown(bd) {
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
    h("section",
      h("div.bd-head", h("span.swatch", { style: { background: "var(--c-h)" } }), h("b", "Healthiness"), grade(hh.grade),
        h("span.bd-score", f2(hh.score)), h("span.small.muted", `goal: ${hh.goal_label}`)),
      h("table.t", h("thead", h("tr", h("th", "Factor (per 100 g)"), h("th.num", "Value"), h("th.num", "Good → poor"), h("th.num", "Weight"), h("th", "Score"))),
        h("tbody", fac)),
      h("div.formula", "Healthiness = Σ weight × factor score / Σ weight", hh.missing.length ? ` · missing: ${hh.missing.join(", ")}` : "")),
    h("section",
      h("div.bd-head", h("span.swatch", { style: { background: "var(--c-s)" } }), h("b", "Sustainability"),
        h("span.bd-score", f2(ss.score)), h("span.small.muted", co2(ss.kg_co2e_per_kg) + (ss.kg_co2e_per_serving != null ? ` · ${f2(ss.kg_co2e_per_serving)} kg per serving` : ""))),
      fp.length ? h("div.fp-bars", fp.map((b) => h("div.fp-row",
        h("span.fp-name", b.name, h("span.small.muted", ` · ${human(b.category)}`)),
        h("div.minibar", h("span", { style: { width: pct(b.share), background: "var(--c-s)" } })),
        h("span.small", `${pct(b.share)}`), h("span.small.muted", `${Math.round(b.grams)} g × ${b.factor}`)))) : null,
      h("div.formula", `Footprint = Σ grams × food-group factor / total grams (Poore & Nemecek 2018). Score: log scale, ${ss.anchors.best} → 1, ${ss.anchors.worst} → 0.`)),
    h("section",
      h("div.bd-head", h("span.swatch", { style: { background: "var(--c-t)" } }), h("b", "Convenience"),
        h("span.bd-score", f2(tt.score)), h("span.small.muted", `${tt.n_ingredients} ingredients`)),
      h("div.chips", tt.ingredients.map((n) => h("span.tag", n))),
      h("div.formula", `Score = (${tt.anchors.worst} − n) / (${tt.anchors.worst} − ${tt.anchors.best}), clipped to 0–1.`)));
}

function recipeDetails(r, compareWith) {
  const ev = r.evidence;
  const rk = ev.ranking;
  const rows = CRIT.map((k) => {
    const c = rk.contributions[k];
    const w = r.effective_weights[k];
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
  return h("div.details",
    h("div",
      h("div.section-label", "Why it ranked here"),
      h("table.t", h("thead", h("tr", h("th", "Criterion"), h("th.num", "Score"), h("th.num", "Weight"), h("th.num", "Contribution"), h("th", ""))), h("tbody", rows)),
      h("div.small.muted", { style: { marginTop: "6px" } },
        `Utility ${f2(r.utility)} = Σ weight × score (taste keeps a fixed share). Utility rank ${rk.utility_rank}, final rank ${rk.final_rank}.`,
        rk.diversity.max_similarity_to_earlier ? ` Similarity to earlier picks ${f2(rk.diversity.max_similarity_to_earlier)}.` : "")),
    h("div", h("div.section-label", "How each score is calculated"), scoreBreakdown(r.breakdown)),
    r.reasons.ranked_here.length ? h("div", h("div.section-label", "Ranking notes"), h("ul.reason-list", r.reasons.ranked_here.map((x) => h("li", x)))) : null,
    h("div", h("div.section-label", "Fits you because"), h("ul.reason-list.fit", r.reasons.fits.map((x) => h("li", x)))),
    r.reasons.compromises.length ? h("div", h("div.section-label", "Compromises"), h("ul.reason-list.comp", r.reasons.compromises.map((x) => h("li", x)))) : null,
    h("div", h("div.section-label", "Uncertain or estimated"), h("ul.reason-list", r.reasons.gaps.map((x) => h("li", x)))),
    h("div", h("div.section-label", "Ingredients"),
      h("ul.ing-list", r.ingredients_detail.map((i) => h("li", i.text || i.name, h("span.small.muted", ` · ≈${Math.round(i.grams)} g`))))),
    compareWith.length > 1 ? h("div", h("div.section-label", "Compare"), h("div", { style: { margin: "6px 0" } }, cmpSel), cmpBox) : null);
}

export function compareTable(a, b) {
  return h("table.t",
    h("thead", h("tr", h("th", ""), h("th.num", a.title), h("th.num", b.title), h("th.num", "Δ"))),
    h("tbody", CRIT.map((k) => {
      const x = a.scores[k], y = b.scores[k];
      const d = x == null || y == null ? null : x - y;
      return h("tr", h("td", CRIT_META[k].long), h("td.num", f2(x)), h("td.num", f2(y)),
        h("td.num" + (d > 0.04 ? ".delta-up" : d < -0.04 ? ".delta-down" : ""), d == null ? "–" : (d > 0 ? "+" : "") + f2(d)));
    }), h("tr", h("td", h("b", "Utility")), h("td.num", h("b", f2(a.utility))), h("td.num", h("b", f2(b.utility))),
      h("td.num", (a.utility - b.utility > 0 ? "+" : "") + f2(a.utility - b.utility)))));
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
  const kindLabel = { goal_proposal: "Proposal", tradeoff_question: "Question", taste_question: "Question", profile_change: "Needs your confirmation" }[d.kind] || d.kind;
  const box = h("div.decision.k-" + d.kind);
  const resolve = async (action, answer) => {
    try {
      const out = await api(userPath(`/decisions/${d.decision_id}`), { method: "POST", body: { action, answer } });
      toast(action === "accept" ? "Saved. A new version was created; you can revert it in Profile." : "Recorded");
      emit("decision", out);
      onResolved?.(out);
      clear(box, h("div.small.muted", `${kindLabel} · ${out.decision.status}`), h("h4", d.title),
        h("div.resolved", action === "dismiss" ? "Dismissed. The supervisor will not raise this again for a while." :
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
  if (d.status !== "open") {
    kids.push(h("div.resolved", `Status: ${d.status}${d.resolved_ts ? " · " + ago(d.resolved_ts) : ""}`));
  } else if (d.kind === "goal_proposal" || d.kind === "profile_change") {
    kids.push(h("div.btn-row", h("button.btn.primary.sm", { onclick: () => resolve("accept") }, d.kind === "profile_change" ? "Confirm" : "Accept"),
      h("button.btn.sm", { onclick: () => resolve("dismiss") }, d.kind === "profile_change" ? "Cancel" : "Keep my goal")));
  } else {
    kids.push(h("div.btn-row", b.options.map((o) => h("button.btn.sm", { onclick: () => resolve("answer", o.id) }, o.label)),
      h("button.btn.sm.ghost", { onclick: () => resolve("dismiss") }, "Not now")));
  }
  return clear(box, kids);
}
