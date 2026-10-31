// My dishes (opened from the account menu, top right): what the user saved for later, liked
// and cooked, so they can find it again.
// Saved dishes stay out of Discover while they are here (ranking.hide_saved).
import { ago, api, ask, can, clear, h, human, icon, safeGet, safeSet, state, toast, userPath } from "../lib.js";
import { scoreChips, sendFeedback, thumb } from "../components.js";

const TABS = [
  ["saved", "Saved for later", "bookmark", "Dishes you saved. They wait here instead of taking a place in Discover; remove one to let Discover show it again."],
  ["liked", "Liked", "heart", ""],
  ["cooked", "Cooked", "check", ""],
];
const reasonLabel = (id) => [...(state.system?.interactions?.choose_reasons || []), ...(state.system?.interactions?.reject_reasons || [])]
  .find((r) => r.id === id)?.label || human(id);

export function dishesView() {
  let tab = safeGet("foodrec.dishes_tab") || "saved";
  let data = null;
  const tabsEl = h("div.seg");
  const body = h("div", h("div.card-body", h("div.skeleton", { style: { height: "90px" } })));

  async function load() {
    try {
      data = await api(userPath("/dishes"));
      render();
    } catch (e) {
      clear(body, h("div.empty", h("h3", "Could not load your dishes"),
        h("p", e.status === 404 ? "The running FoodRec server is older than this page. Restart it: foodrec stop, then foodrec."
          : e.message)));
    }
  }

  function row(r, col) {
    const status = h("span.small.muted");
    const when = col === "saved" ? `saved ${ago(r.ts)}` : col === "liked" ? `liked ${ago(r.ts)}`
      : `cooked ${r.count > 1 ? `${r.count} times, last ` : ""}${ago(r.ts)}`;
    const remove = async () => {
      try {
        await api(userPath("/dishes/remove"), { method: "POST", body: { recipe_id: r.recipe_id, collection: col } });
        toast(col === "saved" ? "Removed from Saved; Discover may show it again" : "Removed from this list");
        load();
      } catch (e) { toast(e.message, "err"); }
    };
    return h("article.recipe.dish-row",
      h("div.rank-col", thumb(r)),
      h("div",
        h("div.row.between", { style: { alignItems: "flex-start" } },
          h("div.grow",
            h("h3", r.url ? h("a.title-link", { href: r.url, target: "_blank", rel: "noopener" }, r.title) : r.title),
            h("div.meta",
              h("span.m", r.cuisine === "Other" ? human(r.meal) : `${r.cuisine} · ${human(r.meal)}`),
              r.n_ingredients != null ? h("span.m", icon("list"), `${r.n_ingredients} ingredients`) : null,
              r.kcal != null ? h("span.m", icon("flame"), `${Math.round(r.kcal)} kcal`) : null,
              h("span.m", icon("clock"), when)))),
        r.scores && Object.keys(r.scores).length ? scoreChips(r) : null,
        r.why?.length ? h("div.row.wrap", { style: { gap: "4px", marginTop: "4px" } }, r.why.map((w) => h("span.tag", reasonLabel(w)))) : null,
        h("div.recipe-actions",
          col !== "cooked" && can("feedback") ? h("button.btn.sm", { onclick: async () => {
            try {
              const out = await sendFeedback(r, null, "choose", null);
              if (!out.duplicate) { clear(status, icon("check"), " recorded as cooked"); load(); }
            } catch { /* toast shown */ }
          } }, icon("check"), "I'll cook this") : null,
          col === "cooked" && can("feedback") ? h("button.btn.sm", { onclick: async () => {
            try { const out = await sendFeedback(r, null, "save", null); if (!out.duplicate) load(); } catch { /* toast shown */ }
          }, title: "Keep it in Saved for later" }, icon("bookmark"), "Save for later") : null,
          h("span.grow"), status,
          can("assistant") && can("explanations") ? h("button.btn.sm.ghost", { onclick: () => ask(`Tell me about ${r.title}`) }, icon("spark"), "Ask") : null,
          h("button.btn.sm.ghost", { onclick: remove, title: "Take it off this list (nothing that was learned changes)" }, icon("x"), "Remove"))));
  }

  function render() {
    clear(tabsEl, TABS.map(([k, label, ic]) => h("button" + (k === tab ? ".on" : ""), {
      onclick: () => { tab = k; safeSet("foodrec.dishes_tab", k); render(); },
    }, icon(ic), ` ${label}`, h("span.count", { style: { marginLeft: "6px" } }, data?.[k]?.length ?? 0))));
    const [, , , hint] = TABS.find(([k]) => k === tab);
    const rows = data?.[tab] || [];
    clear(body,
      hint ? h("div.card-head", h("div.small.muted", hint)) : null,
      rows.length ? rows.map((r) => row(r, tab))
        : h("div.card-body", h("div.empty", h("h3", "Nothing here yet"),
          h("p", tab === "saved" ? "Use Save on a recipe in Discover to keep it for later." :
            tab === "liked" ? "Use Like on a recipe in Discover." : "Use “I'll cook this” on a recipe."))));
  }

  load();
  return h("div",
    h("div.page-head", h("div", h("h1", "My dishes"), h("p", "What you saved for later, liked and cooked, so you can find it again.")), tabsEl),
    h("section.card", body));
}
