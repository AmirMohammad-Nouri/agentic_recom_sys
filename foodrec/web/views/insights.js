// Insights: what you said (stated weights) vs. what you do (observed weights), and why.
import { api, clear, CRIT_META, f2, h, human, icon, pct, state, toast, userPath, VALUES } from "../lib.js";

function ring(value, label, sub) {
  const r = 34, c = 2 * Math.PI * r;
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 84 84");
  svg.setAttribute("class", "ring");
  const mk = (cls, dash) => {
    const el = document.createElementNS(ns, "circle");
    el.setAttribute("cx", 42); el.setAttribute("cy", 42); el.setAttribute("r", r);
    el.setAttribute("class", cls);
    if (dash != null) { el.setAttribute("stroke-dasharray", `${dash} ${c}`); el.setAttribute("transform", "rotate(-90 42 42)"); }
    return el;
  };
  svg.append(mk("ring-track"), mk("ring-value", (value ?? 0) * c));
  return h("div.ring-box", h("div.ring-wrap", svg, h("div.ring-num", value == null ? "–" : pct(value))),
    h("div", h("div.ring-label", label), sub ? h("div.small.muted", sub) : null));
}

// One criterion: stated and observed as two bars on the same 0-100 % scale.
function compareRow(c) {
  const color = CRIT_META[c.criterion].color;
  const gap = c.gap;
  const tone = Math.abs(gap) < 0.05 ? "ok" : Math.abs(gap) < 0.10 ? "mid" : "far";
  return h("div.sv-row",
    h("div.sv-name", h("span.swatch", { style: { background: color } }), h("b", c.label),
      h("div.small.muted", CRIT_META[c.criterion].what)),
    h("div.sv-bars",
      h("div.sv-line", h("span.sv-tag", "You chose"),
        h("div.sv-track", h("span.sv-fill.stated", { style: { width: pct(c.stated), borderColor: color } })), h("span.sv-val", pct(c.stated))),
      h("div.sv-line", h("span.sv-tag", "You do"),
        h("div.sv-track", h("span.sv-fill", { style: { width: pct(c.observed), background: color } })), h("span.sv-val", pct(c.observed)))),
    h("div.sv-gap." + tone, { title: "observed − stated" },
      h("div", (gap >= 0 ? "+" : "−") + Math.abs(Math.round(gap * 100)) + " pts"),
      h("div.small", tone === "ok" ? "on track" : gap > 0 ? "you lean more" : "you lean less")));
}

export function insightsView() {
  const summary = h("div.card-body");
  const rows = h("div.card-body");
  const evidence = h("div");
  const taste = h("div");

  async function load() {
    const ins = await api(userPath("/insight"));
    if (!ins.criteria.length) {
      clear(rows, h("div.empty", "No user model yet."));
      return;
    }
    const th = state.system.thresholds;
    const goals = Object.fromEntries((state.system.options.health_goals || []).map((g) => [g.id, g.label]));
    const c = ins.counts;
    clear(summary, h("div.match-summary",
      ring(ins.preference_match, "Preference match", "how closely your choices follow the values you set"),
      h("div.match-facts",
        h("div.fact", h("span.small.muted", "Confidence"), h("b", pct(ins.confidence)),
          h("span.small.muted", `${ins.events_since_goal} event${ins.events_since_goal === 1 ? "" : "s"} since the goal was saved`)),
        h("div.fact", h("span.small.muted", "Evidence so far"), h("b", `${c.choices} choices`),
          h("span.small.muted", `${c.rejections} reasoned rejections · ${c.answers} answers · ${c.reasons} reasons`)),
        h("div.fact", h("span.small.muted", "Health goal"), h("b", goals[ins.health_goal] || human(ins.health_goal)),
          h("span.small.muted", "sets the weights of the healthiness factors")),
        h("div.fact", h("span.small.muted", "Taste"), h("b", `fixed ${pct(ins.taste_share)} share`),
          h("span.small.muted", "learned, not a slider")))));
    clear(rows, ins.criteria.map(compareRow),
      h("div.hint", { style: { marginTop: "12px" } },
        `Preference match = 1 − ½ Σ |chose − do|. The supervisor asks a question when a gap reaches ${Math.round(th.ask_gap * 100)} points ` +
        `(after ${th.ask_min_events} events) and proposes a change at ${Math.round(th.propose_gap * 100)} points (after ${th.propose_min_events}). ` +
        "Your saved goal is never changed automatically."));
    clear(evidence, ins.evidence?.length ? h("table.t",
      h("thead", h("tr", h("th", "What happened"), h("th", "Recipe"), h("th", "Effect on observed weights"))),
      h("tbody", ins.evidence.map((e) => h("tr",
        h("td", human(e.kind), e.reason ? h("div.small.muted", human(e.reason)) : null,
          e.winner ? h("div.small.muted", `${CRIT_META[e.winner].long} over ${CRIT_META[e.loser].long}`) : null,
          e.flag ? h("span.tag.warn", human(e.flag)) : null),
        h("td", e.recipe || "–"),
        h("td", h("div.deltas", VALUES.map((k) => {
          const d = (e.after?.[k] ?? 0) - (e.before?.[k] ?? 0);
          return h("span" + (d > 0.004 ? ".delta-up" : d < -0.004 ? ".delta-down" : ".muted"),
            `${CRIT_META[k].label} ${d >= 0 ? "+" : "−"}${Math.abs(Math.round(d * 100))}`);
        })))))))
      : h("div.card-body.small.muted", "No behavioural evidence yet. Choose, like or reject recipes to teach the model."));
    clear(taste, ins.taste.length ? h("table.t",
      h("thead", h("tr", h("th", "Concept"), h("th.num", "Taste"), h("th.num", "Likes / dislikes"), h("th", "Source"), h("th", "Correct it"))),
      h("tbody", ins.taste.map((t) => h("tr",
        h("td", h("span.tag", t.concept.split(":")[0]), " ", t.concept.split(":")[1]),
        h("td.num", h("div.row", { style: { justifyContent: "flex-end" } },
          h("div.minibar", { style: { width: "60px" } }, h("span", { style: { width: pct(t.mean), background: t.mean >= 0.5 ? "var(--accent)" : "var(--danger)" } })),
          f2(t.mean))),
        h("td.num", `${f2(t.likes)} / ${f2(t.dislikes)}`),
        h("td", t.corrected ? h("span.tag.info", "your correction") : t.declared ? h("span.tag.accent", "declared") : h("span.tag", "learned")),
        h("td", h("div.seg", [["Like", 0.85], ["Neutral", 0.5], ["Dislike", 0.15]].map(([lab, m]) =>
          h("button", { onclick: () => correct(t.concept, m), title: `Set taste to ${m}` }, lab))))))))
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
      h("p", "The values you chose next to how your actual choices weigh them, and the evidence behind every number."))),
    h("div.stack",
      h("section.card", summary),
      h("section.card", h("div.card-head", h("h2", "You chose vs. what you do"), h("span.small.muted", "share of the three values")), rows),
      h("div.two-col",
        h("section.card", h("div.card-head", h("h2", "Recent evidence"), h("span.small.muted", "points of observed weight")), evidence),
        h("section.card", h("div.card-head", h("h2", "Taste"), h("span.small.muted", "(1 + likes) / (2 + likes + dislikes)")), taste))));
}
