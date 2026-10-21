// Insights: what the system believes about you, how sure it is, and why.
import { api, clear, CRIT_META, f2, h, human, pct, state, toast, userPath, when } from "../lib.js";

const MAX = 0.7; // scale upper bound for weights

function scale(c) {
  const x = (v) => `${Math.min(100, (v / MAX) * 100)}%`;
  const color = CRIT_META[c.criterion].color;
  return h("div.scale", { title: `Saved ${f2(c.stated)} · revealed ${f2(c.inferred)} (90% interval ${f2(c.lo)}–${f2(c.hi)})` },
    h("div.track"),
    h("div.band", { style: { left: x(c.lo), width: `calc(${x(c.hi)} - ${x(c.lo)})`, background: color } }),
    h("div.mean", { style: { left: x(c.inferred), background: color } }),
    h("div.stated", { style: { left: x(c.stated) } }));
}

export function insightsView() {
  const prefs = h("div.card-body");
  const evidence = h("div.card-body");
  const taste = h("div");

  async function load() {
    const ins = await api(userPath("/insight"));
    if (!ins.criteria.length) {
      clear(prefs, h("div.empty", "No preference model yet."));
      return;
    }
    const th = state.system.thresholds;
    clear(prefs,
      h("div.row.between.small.muted", { style: { marginBottom: "6px" } },
        h("span", "▼ saved goal   ▌ revealed estimate   ▬ 90% interval"), h("span", "0 – " + MAX)),
      ins.criteria.map((c) => h("div.pref-row",
        h("div", h("div", { style: { fontWeight: 550 } }, c.label),
          h("div.small.muted", `saved ${f2(c.stated)} · revealed ${f2(c.inferred)}`)),
        scale(c),
        h("div.conf", h("div", "confidence ", h("b", pct(c.confidence))),
          c.prob_differs >= th.conflict_prob ? h("span.tag.warn", { title: `P(differs) = ${pct(c.prob_differs)}` }, "possible conflict")
            : h("span.small", `P(differs) ${pct(c.prob_differs)}`)))),
      h("div.hint", { style: { marginTop: "10px" } },
        `Revealed estimates come from ${ins.counts.choices} choices, ${ins.counts.rejections} reasoned rejections and ` +
        `${ins.counts.statements} explicit goal statements. A conflict is flagged at P ≥ ${pct(th.conflict_prob)}; ` +
        `the saved goal is never changed automatically.`));
    clear(evidence, ins.evidence?.length ? h("table.t",
      h("thead", h("tr", h("th", "Evidence"), h("th", "Recipe"), h("th", "Detail"), h("th", "Event"))),
      h("tbody", ins.evidence.map((e) => h("tr",
        h("td", human(e.kind)),
        h("td", e.recipe || "–"),
        h("td", [e.reason ? `reason: ${human(e.reason)}` : null, e.winner ? `${CRIT_META[e.winner].long} over ${CRIT_META[e.loser].long}` : null,
          e.weights ? "weights stated" : null, e.flag ? h("span.tag.warn", human(e.flag)) : null].filter(Boolean).map((x, i) => [i ? " · " : "", x])),
        h("td.mono.muted", e.event_id || "–")))))
      : h("div.small.muted", "No behavioural evidence yet. Choose, like or reject recipes to teach the model."));
    clear(taste, ins.taste.length ? h("table.t",
      h("thead", h("tr", h("th", "Concept"), h("th.num", "Belief"), h("th.num", "Evidence"), h("th", "Source"), h("th", "Correct it"))),
      h("tbody", ins.taste.map((t) => h("tr",
        h("td", h("span.tag", t.concept.split(":")[0]), " ", t.concept.split(":")[1]),
        h("td.num", h("div.row", { style: { justifyContent: "flex-end" } },
          h("div.minibar", { style: { width: "60px" } }, h("span", { style: { width: pct(t.mean), background: t.mean >= 0.5 ? "var(--accent)" : "var(--danger)" } })),
          f2(t.mean))),
        h("td.num", f2(t.evidence)),
        h("td", t.corrected ? h("span.tag.info", "your correction") : t.declared ? h("span.tag.accent", "declared") : h("span.tag", "inferred"),
          h("div.small.muted", t.provenance.slice(0, 3).map((p) => human(p.kind)).join(" · "))),
        h("td", h("div.seg", [["Like", 0.85], ["Neutral", 0.5], ["Dislike", 0.15]].map(([lab, m]) =>
          h("button", { onclick: () => correct(t.concept, m), title: `Set belief to ${m}` }, lab))))))))
      : h("div.card-body.small.muted", "No taste beliefs yet."));
  }

  async function correct(concept, mean) {
    try {
      await api(userPath("/beliefs"), { method: "POST", body: { concept, mean } });
      toast(`Corrected ${concept.split(":")[1]}; recorded as your correction`);
      load();
    } catch (e) { toast(e.message, "err"); }
  }

  load();
  return h("div",
    h("div.page-head", h("div", h("h1", "Insights"),
      h("p", "What the system believes about you, how confident it is, and the evidence behind each belief."))),
    h("div.stack",
      h("section.card", h("div.card-head", h("h2", "Priorities: what you said vs. what you do")), prefs),
      h("div.two-col",
        h("section.card", h("div.card-head", h("h2", "Taste beliefs"), h("span.small.muted", "Beta belief per concept, 0 dislike – 1 like")), taste),
        h("section.card", h("div.card-head", h("h2", "Recent evidence")), evidence))));
}
