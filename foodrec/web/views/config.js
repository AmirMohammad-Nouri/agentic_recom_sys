// Config page (/config, separate from the app): every setting of foodrec.config.json.
// Tabs: Settings, Criteria & interactions, Access roles, Users & study, Export, JSON file.
// Every section has a guide button (ⓘ) that opens a guide from /static/guides/<id>.md.
// Saving writes the file; the server applies "live" settings at once and restarts itself
// for the others (the page then waits for the new process and reloads).
import { ago, api, clear, download, h, icon, sessionGet, sessionSet, toast } from "../lib.js";
import { markdown } from "../markdown.js";

const TABS = [["settings", "Settings"], ["criteria", "Criteria & interactions"], ["roles", "Access roles"],
  ["users", "Users & study"], ["export", "Export"], ["json", "JSON file"]];
const clone = (x) => JSON.parse(JSON.stringify(x));
const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));
const CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"];
// settings that only matter for some providers
const ONLY_FOR = { base_url: ["openai", "anthropic"], api_key_env: ["openai", "anthropic"], effort: ["anthropic"],
  fallbacks: ["anthropic"], max_tokens: ["openai", "anthropic"], ctx: ["ollama"], seed: ["ollama", "openai"] };

// ---- guides --------------------------------------------------------------------------------
export async function openGuide(id) {
  const body = h("div.modal-body", h("div.skeleton", { style: { height: "120px" } }));
  const overlay = h("div.overlay", { onclick: (e) => { if (e.target === overlay) overlay.remove(); } },
    h("div.modal.wide.guide", h("div.modal-head", h("div.row.between", h("h2", "Guide"),
      h("button.icon-btn", { onclick: () => overlay.remove(), "aria-label": "Close" }, icon("close")))), body,
    h("div.modal-foot", h("span.small.muted", h("span.mono", `foodrec/web/guides/${id}.md`)),
      h("button.btn", { onclick: () => overlay.remove() }, "Close"))));
  document.body.append(overlay);
  try {
    const res = await fetch(`/static/guides/${id}.md`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    clear(body, markdown(await res.text()));
  } catch (e) { clear(body, h("p", `This guide could not be loaded (${e.message}).`)); }
}
const guideBtn = (id, label = "Guide") => h("button.btn.ghost.sm.guide-btn", { type: "button", title: `Open the ${label.toLowerCase()}`,
  onclick: () => openGuide(id) }, icon("info"), label);

function card(title, guide, ...kids) {
  return h("section.card", h("div.card-head", h("h2", title), guide ? guideBtn(guide) : null), h("div.card-body.stack", kids));
}

// ---- restart: wait for the new process, then reload -----------------------------------------
async function waitForRestart(oldBoot) {
  const msg = h("p.muted", "The new process is starting (loading the catalogue can take a few seconds).");
  const overlay = h("div.overlay", h("div.modal", h("div.modal-body.stack", h("div.row", h("span.spinner"), h("b", "Restarting FoodRec with the new settings…")), msg)));
  document.body.append(overlay);
  const t0 = Date.now();
  for (;;) {
    await new Promise((r) => setTimeout(r, 1000));
    try {
      const res = await fetch("/api/system");
      if (res.ok && (await res.json()).boot_id !== oldBoot) { location.reload(); return; }
    } catch { /* still restarting */ }
    if (Date.now() - t0 > 120000) { clear(msg, "This takes longer than expected. Check the terminal where FoodRec runs."); }
  }
}

export function configView() {
  let view = null;      // GET /api/config
  let draft = null;     // the file being edited
  let tab = sessionGet("foodrec.config_tab") || "settings";
  let dirty = false;
  const body = h("div");
  const head = h("div.row", { style: { gap: "8px", alignItems: "center" } });
  const tabsEl = h("div.seg.cfg-tabs");

  function fromView(v) {
    // runtime settings: what the FILE says (null = environment/default); the rest: effective
    const d = { config_version: v.effective.config_version };
    if (v.raw.$comment) d.$comment = v.raw.$comment;
    for (const f of v.fields) (d[f.section] ||= {})[f.key] = f.file_value ?? null;
    for (const k of ["access", "study", "criteria", "interactions"]) d[k] = clone(v.effective[k]);
    return d;
  }

  async function load() {
    try {
      view = await api("/api/config");
      draft = fromView(view);
      dirty = false;
      render();
    } catch (e) {
      if (e.status === 401 || e.status === 403) return tokenForm(e.message);
      clear(body, h("div.empty", h("h3", "Config is not available"), h("p", e.message)));
    }
  }

  function tokenForm(why) {
    const inp = h("input", { type: "password", placeholder: "admin token", style: { width: "280px" } });
    clear(body, h("section.card", h("div.card-body.stack",
      h("h3", "Administrator access"),
      h("p.muted", why),
      h("p", "Open this page with ", h("span.mono", "foodrec config open"), " (its link signs you in), sign in to the app as a user with the configure feature, or enter FOODREC_ADMIN_TOKEN here. The token is kept for this browser tab only."),
      h("div.row", inp, h("button.btn.primary", { onclick: () => { sessionSet("foodrec.admin_token", inp.value.trim()); load(); } }, "Continue")))));
  }

  const touch = () => { dirty = true; renderHead(); };

  async function save() {
    try {
      const errs = (await api("/api/config/validate", { method: "POST", body: { config: draft } })).errors;
      if (errs.length) { toast(errs[0], "err"); renderProblems(errs); return; }
      const oldBoot = view.boot_id;
      const res = await api("/api/config", { method: "PUT", body: { config: draft } });
      if (res.restarting) return waitForRestart(oldBoot);
      view = res;
      draft = fromView(view);
      dirty = false;
      toast(view.restart_pending.length ? `Saved. ${view.restart_pending.length} change(s) apply after a restart.` : "Saved and applied");
      render();
    } catch (e) { toast(e.message, "err"); }
  }

  function renderProblems(errs) {
    const box = body.querySelector(".cfg-problems");
    if (box) clear(box, errs.length ? h("div.change", h("b", "Problems: "), h("ul", errs.map((x) => h("li", x)))) : null);
  }

  function renderHead() {
    clear(head,
      dirty ? h("span.tag.warn", "unsaved changes") : h("span.small.muted", `version ${view?.version || "–"}`),
      h("button.btn", { disabled: !dirty, onclick: () => { draft = fromView(view); dirty = false; render(); } }, "Discard"),
      h("button.btn.primary", { disabled: !dirty, onclick: save }, "Save to file"));
    clear(tabsEl, TABS.map(([k, label]) => h("button" + (k === tab ? ".on" : ""), {
      onclick: () => { tab = k; sessionSet("foodrec.config_tab", k); render(); },
    }, label)));
  }

  function render() {
    renderHead();
    const pending = view.restart_pending || [];
    clear(body,
      h("div.small.muted", { style: { marginBottom: "10px" } }, "File: ", h("span.mono", view.path || "none"),
        view.exists ? "" : " (not created yet: saving creates it)",
        view.auto_reload ? " · edits to the file (here or by hand) are applied automatically" : " · automatic reload is off",
        view.admin_token ? " · protected by an admin token" : ""),
      view.config_problem ? h("div.change.bad", h("b", "The file on disk was not applied: "), view.config_problem,
        h("div.small", "The app keeps running with the last valid configuration until the file is fixed.")) : null,
      pending.length ? h("div.change", h("b", "Waiting for a restart: "), pending.map((p) => `${p.label} (running ${p.running}, saved ${p.saved})`).join("; "),
        h("div", { style: { marginTop: "6px" } }, h("button.btn.sm", { onclick: async () => {
          try { const old = view.boot_id; await api("/api/config/restart", { method: "POST" }); waitForRestart(old); } catch (e) { toast(e.message, "err"); }
        } }, icon("reset"), "Restart now"))) : null,
      view.errors.length ? h("div.change", h("b", "The file has problems: "), h("ul", view.errors.map((x) => h("li", x)))) : null,
      view.model_spec?.errors?.length ? h("div.change", h("b", "Criteria/interactions in use fell back to defaults: "), h("ul", view.model_spec.errors.map((x) => h("li", x)))) : null,
      h("div.cfg-problems"),
      { settings: settingsTab, criteria: criteriaTab, roles: rolesTab, users: usersTab, export: exportTab, json: jsonTab }[tab]());
  }

  // ---- settings -----------------------------------------------------------------------------
  const provider = () => draft.llm.provider ?? view.fields.find((f) => f.key === "provider").running;
  function relevant(f) {
    if (f.section !== "llm") return true;
    const p = provider();
    if (ONLY_FOR[f.key]) return ONLY_FOR[f.key].includes(p);
    if (f.key === "url") return p === "ollama" || (draft.llm.embed_provider ?? "ollama") === "ollama";
    if (f.key === "embed_on_cpu") return (draft.llm.embed_provider ?? "ollama") === "ollama";
    return true;
  }

  function fieldInput(f) {
    const cur = draft[f.section][f.key];
    const set = (v) => { draft[f.section][f.key] = v; touch(); };
    const inherit = h("label.switch", { title: "Leave this setting to the environment variable or the built-in default" },
      h("input", { type: "checkbox", checked: cur === null, onchange: (e) => { set(e.target.checked ? null : (f.running ?? "")); render(); } }),
      f.env ? "from environment" : "default");
    let input;
    if (cur === null) input = h("span.small.muted.mono", String(f.running ?? "–"));
    else if (f.type === "bool") {
      input = h("label.switch", h("input", { type: "checkbox", checked: !!cur, onchange: (e) => { set(e.target.checked); render(); } }), cur ? "on" : "off");
    } else if (f.type === "choice") {
      input = h("select", { onchange: (e) => { set(e.target.value); render(); } }, f.options.map((o) => h("option", { value: o, selected: o === cur }, o)));
    } else if (f.type === "int" || f.type === "float") {
      input = h("input", { type: "number", value: cur, min: f.min, max: f.max, step: f.type === "int" ? 1 : "any", style: { width: "120px" },
        onchange: (e) => set(num(e.target.value)) });
    } else {
      const list = f.section === "llm" && f.key === "model" ? "cfg-models" : f.section === "llm" && f.key === "api_key_env" ? "cfg-keys" : null;
      input = h("input", { type: "text", value: cur, list, style: { width: "100%" }, onchange: (e) => set(e.target.value) });
    }
    let extra = null;
    if (f.section === "llm" && f.key === "api_key_env") extra = apiKeyBox(cur || f.running);
    return h("div.cfg-field",
      h("div", h("div.row", { style: { gap: "6px" } }, h("b.small", f.label),
        h("span.tag" + (f.apply === "live" ? ".accent" : ".warn"), { title: f.apply === "live" ? "Applied when saved" : "Applied by a restart (automatic when the file is watched)" }, f.apply === "live" ? "live" : "restart")),
        h("div.small.muted.mono", `${f.section}.${f.key}` + (f.env ? ` · env ${f.env}` : "")),
        f.help ? h("div.hint", f.help) : null),
      h("div.stack.tight", input, extra,
        h("div.row.small.muted", { style: { gap: "10px" } }, inherit,
          h("span", "running: ", h("span.mono", String(f.running ?? "–")), ` (${f.source})`))));
  }

  // The key itself: written to .env (git-ignored) by the server and used at once; the config
  // file only names the variable.
  function apiKeyBox(varName) {
    const name = (varName || (provider() === "anthropic" ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY")).trim();
    const isSet = !!view.api_keys?.[name];
    const key = h("input", { type: "password", autocomplete: "off", placeholder: isSet ? "•••••••• (set; type to replace)" : `paste the ${name} value`,
      style: { flex: "1", minWidth: "220px" } });
    const store = async (value) => {
      try {
        const res = await api("/api/config/secret", { method: "POST", body: { name, value } });
        view.api_keys = { ...(view.api_keys || {}), [name]: res.set };
        if (res.llm_status) view.llm_status = res.llm_status;
        toast(res.set ? `${name} saved in .env and in use` : `${name} removed from .env`);
        render();
      } catch (e) { toast(e.message, "err"); }
    };
    return h("div.stack.tight.key-box",
      h("div.small", h("span.dot." + (isSet ? "ok" : "warn")), " ", h("b.mono", name), isSet ? " is set" : " is not set"),
      h("div.row", { style: { gap: "6px" } }, key,
        h("button.btn.sm.primary", { onclick: () => (key.value.trim() ? store(key.value.trim()) : toast("Paste the key first", "err")) },
          isSet ? "Update in .env" : "Save to .env"),
        isSet ? h("button.btn.sm.ghost", { onclick: () => { if (confirm(`Remove ${name} from .env?`)) store(null); } }, "Remove") : null),
      h("div.hint", "Stored in the .env file of the repository (ignored by git), not in the config file. It applies at once; no restart."));
  }

  function settingsTab() {
    const p = provider();
    const models = p === "anthropic" ? CLAUDE_MODELS : (view.installed_models || []);
    const llm = view.llm_status || {};
    return h("div.stack",
      h("datalist", { id: "cfg-models" }, models.map((m) => h("option", { value: m }))),
      h("datalist", { id: "cfg-keys" }, ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "TOGETHER_API_KEY"].map((m) => h("option", { value: m }))),
      Object.entries(view.sections).map(([sec, label]) => h("section.card",
        h("div.card-head", h("h2", label), h("span.grow"),
          sec === "llm" ? h("span.small.muted", h("span.dot." + (llm.available ? "ok" : "warn")), " ",
            llm.available ? `${llm.provider} · ${llm.model} available` : (llm.reason || "model not available")) : null,
          guideBtn(sec)),
        h("div.card-body.stack", view.fields.filter((f) => f.section === sec && relevant(f)).map(fieldInput),
          sec === "llm" ? h("div.hint", `Fields that ${p} does not use are hidden. Switch the provider to see them.`) : null,
          sec === "dataset" ? h("div.row.wrap", { style: { gap: "8px" } },
            h("a.btn.sm", { href: "/static/guides/recipes_template.json", download: "recipes_template.json" }, icon("ext"), "Download the recipe template"),
            h("a.btn.sm", { href: "/static/guides/hummus_columns.csv", download: "hummus_columns.csv" }, icon("ext"), "Spreadsheet columns (CSV)"),
            guideBtn("dataset", "Dataset template and steps")) : null))));
  }

  // ---- criteria & interactions --------------------------------------------------------------
  const enabledCriteria = () => (draft.criteria.items || []).filter((c) => c.enabled !== false);

  function criterionCard(c, idx) {
    const items = draft.criteria.items;
    const builtin = c.kind !== "field";
    const upd = (k, v, rerender = false) => { c[k] = v; touch(); if (rerender) render(); };
    const statsBox = h("div.small.muted");
    const showStats = async (useAnchors) => {
      try {
        const s = await api(`/api/config/field-stats?field=${encodeURIComponent(c.field)}`);
        if (!s.count) { clear(statsBox, `No recipe in the current catalogue has ${c.field}.`); return; }
        clear(statsBox, `${s.count} of ${s.of} recipes have it · min ${s.min} · 10% ${s.p10} · median ${s.median} · 90% ${s.p90} · max ${s.max}`);
        if (useAnchors) {
          c.best = c.direction === "lower" ? s.p10 : s.p90;
          c.worst = c.direction === "lower" ? s.p90 : s.p10;
          touch(); render();
        }
      } catch (e) { clear(statsBox, e.message); }
    };
    return h("div.card.crit-card", { style: { borderLeft: `4px solid ${c.color || `var(--c-${c.id})`}` } },
      h("div.row.wrap", { style: { gap: "10px", alignItems: "center" } },
        h("label.switch", h("input", { type: "checkbox", checked: c.enabled !== false, onchange: (e) => upd("enabled", e.target.checked, true) }), "enabled"),
        h("input", { type: "text", value: c.label, style: { width: "180px" }, title: "Label shown to users", onchange: (e) => upd("label", e.target.value) }),
        h("span.small.muted", "id"),
        builtin ? h("span.mono", c.id) : h("input.mono", { type: "text", value: c.id, style: { width: "70px" }, title: "Short id (lowercase); used in the saved weights and exports",
          onchange: (e) => upd("id", e.target.value.trim().toLowerCase()) }),
        h("span.tag", builtin ? `built-in: ${c.kind}` : "field"),
        h("span.grow"),
        h("span.small.muted", "default weight"),
        h("input", { type: "number", min: 0, max: 1, step: 0.05, value: c.default_weight ?? 0, style: { width: "80px" }, onchange: (e) => upd("default_weight", num(e.target.value) ?? 0) }),
        h("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(c.color || "") ? c.color : "#3a8a5c", title: "Colour", onchange: (e) => upd("color", e.target.value) }),
        builtin ? null : h("button.btn.ghost.sm", { title: "Remove this criterion", onclick: () => { items.splice(idx, 1); touch(); render(); } }, icon("trash"))),
      h("div.cfg-grid",
        h("label.field", h("span.field-label", "Description (shown to users and the assistant)"),
          h("textarea", { rows: 2, value: c.description || "", onchange: (e) => upd("description", e.target.value) })),
        h("label.field", h("span.field-label", "Words that name it in chat (comma-separated)"),
          h("input", { type: "text", value: (c.keywords || []).join(", "), onchange: (e) => upd("keywords", e.target.value.split(",").map((x) => x.trim()).filter(Boolean)) }))),
      builtin ? null : h("div.stack.tight",
        h("div.row.wrap", { style: { gap: "8px", alignItems: "flex-end" } },
          h("label.field", h("span.field-label", "Recipe field"),
            h("input.mono", { type: "text", value: c.field || "", list: "cfg-fields", style: { width: "260px" }, onchange: (e) => upd("field", e.target.value.trim()) })),
          h("label.field", h("span.field-label", "Better is"),
            h("select", { onchange: (e) => upd("direction", e.target.value) }, [["lower", "lower"], ["higher", "higher"]].map(([v, t]) => h("option", { value: v, selected: c.direction === v }, t)))),
          h("label.field", h("span.field-label", "Best (score 1)"), h("input", { type: "number", step: "any", value: c.best, style: { width: "100px" }, onchange: (e) => upd("best", num(e.target.value)) })),
          h("label.field", h("span.field-label", "Worst (score 0)"), h("input", { type: "number", step: "any", value: c.worst, style: { width: "100px" }, onchange: (e) => upd("worst", num(e.target.value)) })),
          h("label.field", h("span.field-label", "Scale"),
            h("select", { onchange: (e) => upd("scale", e.target.value) }, ["linear", "log"].map((v) => h("option", { value: v, selected: c.scale === v }, v)))),
          h("label.field", h("span.field-label", "Unit"), h("input", { type: "text", value: c.unit || "", style: { width: "100px" }, onchange: (e) => upd("unit", e.target.value) }))),
        h("div.row.wrap", { style: { gap: "6px" } },
          h("button.btn.sm", { onclick: () => showStats(false) }, "Show the values in the catalogue"),
          h("button.btn.sm", { onclick: () => showStats(true), title: "Best = the 10% best value, worst = the 10% worst value" }, "Set anchors from the data")),
        statsBox));
  }

  function reasonTable(kind) {
    const list = (draft.interactions[`${kind}_reasons`] ||= []);
    const crits = enabledCriteria();
    const upd = (r, k, v) => { r[k] = v; touch(); };
    const cols = kind === "choose" ? ["Id", "Label", "About", "Weight", "Taste bonus", "Shown", "Ask: lasting or today", ""]
      : ["Id", "Label", "About", "Weight", "Taste dislike", "Effect", "Shown", "Ask: lasting or today", ""];
    return h("div", { style: { overflowX: "auto" } }, h("table.t.reasons", h("thead", h("tr", cols.map((c) => h("th", c)))),
      h("tbody", list.map((r, i) => h("tr",
        h("td", h("input.mono", { type: "text", value: r.id, style: { width: "130px" }, onchange: (e) => upd(r, "id", e.target.value.trim()) })),
        h("td", h("input", { type: "text", value: r.label, style: { width: "190px" }, onchange: (e) => upd(r, "label", e.target.value) })),
        h("td", h("select", { onchange: (e) => upd(r, "criterion", e.target.value || null) },
          h("option", { value: "", selected: !r.criterion }, "taste"),
          crits.map((c) => h("option", { value: c.id, selected: r.criterion === c.id }, c.label)))),
        h("td", h("input", { type: "number", min: 0, max: 5, step: 0.1, value: r.weight ?? 1, style: { width: "70px" }, onchange: (e) => upd(r, "weight", num(e.target.value) ?? 1) })),
        kind === "choose"
          ? h("td", h("input", { type: "number", min: 0, max: 1, step: 0.05, value: r.taste_bonus ?? 0, style: { width: "70px" }, onchange: (e) => upd(r, "taste_bonus", num(e.target.value) ?? 0) }))
          : h("td", h("input", { type: "number", min: 0, max: 2, step: 0.1, value: r.taste_strength ?? 0.5, style: { width: "70px" }, onchange: (e) => upd(r, "taste_strength", num(e.target.value) ?? 0.5) })),
        kind === "reject" ? h("td", h("select", { onchange: (e) => upd(r, "effect", e.target.value || null) },
          h("option", { value: "", selected: !r.effect }, "learn"), h("option", { value: "recency", selected: r.effect === "recency" }, "recency only"))) : null,
        h("td", h("input", { type: "checkbox", checked: r.show !== false, onchange: (e) => upd(r, "show", e.target.checked) })),
        h("td", h("input", { type: "checkbox", checked: r.ask_scope ?? r.effect !== "recency", onchange: (e) => upd(r, "ask_scope", e.target.checked) })),
        h("td", h("button.btn.ghost.sm", { title: "Remove", onclick: () => { list.splice(i, 1); touch(); render(); } }, icon("trash"))))))),
      h("button.btn.sm", { style: { marginTop: "6px" }, onclick: () => {
        list.push(kind === "choose" ? { id: `reason_${list.length + 1}`, label: "New reason", criterion: crits[0]?.id || null, weight: 1, taste_bonus: 0 }
          : { id: `reason_${list.length + 1}`, label: "New reason", criterion: crits[0]?.id || null, weight: 1, taste_strength: 0.5 });
        touch(); render();
      } }, icon("plus"), "Add a reason"));
  }

  function criteriaTab() {
    const cr = draft.criteria, it = draft.interactions;
    const enabled = enabledCriteria();
    const totW = enabled.reduce((a, c) => a + (c.default_weight || 0), 0) || 1;
    const auto = [...(view.model_spec?.running?.interactions?.choose_reasons || []), ...(view.model_spec?.running?.interactions?.reject_reasons || [])].filter((r) => r.auto);
    const learning = [["eta", "Learning rate: how fast choices move the revealed weights"], ["nudge", "Size of an explicit reason or answer"],
      ["anchor", "Share of the saved goal mixed back in after each update"], ["floor", "Minimum weight before an update (lets zero weights grow)"],
      ["today_nudge", "Weight added for the rest of the day by an 'only today' reason"], ["conf_k", "Confidence = n / (n + conf_k)"]];
    return h("div.stack",
      h("datalist", { id: "cfg-fields" }, (view.numeric_fields || []).map((f) => h("option", { value: f }))),
      h("div.change", h("b", "Changes here restart the app"), " (criteria and interactions are fixed while it runs). Users keep their saved weights: a removed criterion is dropped, a new one starts at 0 for them, and the rest is rescaled. Open the guides before a study."),
      card("Criteria (onboarding values)", "criteria",
        h("div.row.wrap", { style: { gap: "12px", alignItems: "center" } },
          h("label.field", h("span.field-label", "Taste share"),
            h("input", { type: "number", min: 0, max: 0.9, step: 0.05, value: cr.taste_share, style: { width: "90px" }, onchange: (e) => { cr.taste_share = num(e.target.value) ?? 0.25; touch(); } })),
          h("div.hint", "Fixed share of the utility for learned taste; the criteria below share the rest by the user's weights.")),
        h("div.small.muted", "Default weights (the starting sliders): " + enabled.map((c) => `${c.label} ${Math.round(100 * (c.default_weight || 0) / totW)}%`).join(" · ")),
        (cr.items || []).map(criterionCard),
        h("div.btn-row", h("button.btn", { onclick: () => {
          const used = new Set((cr.items || []).map((c) => c.id));
          let n = 1; while (used.has(`c${n}`)) n++;
          cr.items.push({ id: `c${n}`, kind: "field", label: "New criterion", enabled: true, default_weight: 0.2, field: "nutrition_per_100g.kcal",
            direction: "lower", best: 100, worst: 400, scale: "linear", unit: "", description: "", keywords: [] });
          touch(); render();
        } }, icon("plus"), "Add a criterion from a recipe field"))),
      card("Feedback strength", "interactions",
        h("div.hint", "How strongly each kind of feedback counts as liking the dish (taste model)."),
        h("div.row.wrap", { style: { gap: "12px" } }, ["like", "save", "choose"].map((k) => h("label.field", h("span.field-label", k),
          h("input", { type: "number", min: 0, max: 3, step: 0.1, value: it.feedback_strength?.[k] ?? 1, style: { width: "90px" },
            onchange: (e) => { (it.feedback_strength ||= {})[k] = num(e.target.value) ?? 0; touch(); } }))))),
      card("“I'll cook this” reasons", "interactions", reasonTable("choose")),
      card("“Not for me” reasons", "interactions", reasonTable("reject"),
        auto.length ? h("div.hint", "Added automatically because a criterion had no reason: " + auto.map((r) => r.label).join(", ") + ". Add your own to replace them.") : null),
      card("Learning parameters", "interactions",
        h("div.cfg-grid", learning.map(([k, d]) => h("label.field", h("span.field-label", h("span.mono", k), " — ", d),
          h("input", { type: "number", step: "any", value: it.learning?.[k], style: { width: "110px" }, onchange: (e) => { (it.learning ||= {})[k] = num(e.target.value); touch(); } }))))));
  }

  // ---- access roles -------------------------------------------------------------------------
  function rolesTab() {
    const acc = draft.access;
    const names = Object.keys(acc.roles);
    const crits = [{ id: "p", label: "Taste match" }, ...enabledCriteria()];
    const cell = (get, setv, disabled) => h("td.num", h("input", {
      type: "checkbox", checked: get(), disabled, onchange: (e) => { setv(e.target.checked); touch(); render(); },
    }));
    const feat = (r, k) => r.features?.[k] ?? !view.features.find((f) => f.id === k).admin_only;
    const visible = (r) => (r.visible_criteria === "all" || r.visible_criteria === undefined ? crits.map((c) => c.id) : r.visible_criteria);
    const thead = h("tr", h("th", "Feature"), names.map((n) => h("th.num", { style: { minWidth: "120px" } },
      h("input", { type: "text", value: acc.roles[n].label || n, style: { width: "110px" }, title: `Label of role '${n}'`,
        onchange: (e) => { acc.roles[n].label = e.target.value; touch(); } }),
      h("div.small.muted.mono", n),
      names.length > 1 ? h("button.btn.ghost.sm", { title: "Delete this role", onclick: () => {
        if (!confirm(`Delete the role '${n}'? Users with it fall back to the default role.`)) return;
        delete acc.roles[n];
        for (const [u, r] of Object.entries(acc.users || {})) if (r === n) delete acc.users[u];
        if (acc.default_role === n) acc.default_role = Object.keys(acc.roles)[0];
        draft.study.assign_roles = (draft.study.assign_roles || []).filter((r) => r !== n);
        if (draft.study.population?.roles) delete draft.study.population.roles[n];
        touch(); render();
      } }, icon("trash")) : null)));
    const featRows = view.features.map((f) => h("tr",
      h("td", h("b.small", f.label), h("div.small.muted", f.description)),
      names.map((n) => cell(() => feat(acc.roles[n], f.id), (v) => { (acc.roles[n].features ||= {})[f.id] = v; }))));
    const allRow = h("tr", h("td", h("b.small", "Show every criterion"), h("div.small.muted", "Including criteria added later")),
      names.map((n) => cell(() => acc.roles[n].visible_criteria === "all" || acc.roles[n].visible_criteria === undefined,
        (v) => { acc.roles[n].visible_criteria = v ? "all" : crits.map((c) => c.id); })));
    const critRows = crits.map((c) => h("tr",
      h("td", h("b.small", `Show score: ${c.label}`), h("div.small.muted", "The score chip and its share in the breakdowns")),
      names.map((n) => cell(() => visible(acc.roles[n]).includes(c.id), (v) => {
        const cur = new Set(visible(acc.roles[n]));
        v ? cur.add(c.id) : cur.delete(c.id);
        acc.roles[n].visible_criteria = crits.map((x) => x.id).filter((x) => cur.has(x));
      }, acc.roles[n].visible_criteria === "all" || acc.roles[n].visible_criteria === undefined))));
    const descRow = h("tr", h("td", h("b.small", "Description")), names.map((n) => h("td",
      h("textarea", { rows: 3, style: { width: "100%", fontSize: "12px" }, value: acc.roles[n].description || "",
        onchange: (e) => { acc.roles[n].description = e.target.value; touch(); } }))));
    const newName = h("input", { type: "text", placeholder: "new_role_id", style: { width: "160px" } });
    const copyFrom = h("select", names.map((n) => h("option", { value: n, selected: n === "full" }, `copy of ${acc.roles[n].label || n}`)));
    return h("div.stack",
      h("section.card", h("div.card-body.row.wrap", { style: { gap: "14px" } },
        h("div.field", h("span.field-label", "Default role"),
          h("select", { onchange: (e) => { acc.default_role = e.target.value; touch(); } },
            names.map((n) => h("option", { value: n, selected: n === acc.default_role }, acc.roles[n].label || n))),
          h("span.hint", "For users without a fixed or assigned role.")),
        h("span.grow"),
        h("div.field", h("span.field-label", "Add a role"), h("div.row", newName, copyFrom,
          h("button.btn", { onclick: () => {
            const id = newName.value.trim().replace(/[^a-zA-Z0-9_-]/g, "_");
            if (!id || acc.roles[id]) return toast(id ? "That role exists" : "Give the role an id", "err");
            acc.roles[id] = { ...clone(acc.roles[copyFrom.value]), label: id };
            touch(); render();
          } }, icon("plus"), "Add"))),
        guideBtn("roles"))),
      h("section.card", { style: { overflowX: "auto" } },
        h("div.card-head", h("h2", "What each role sees"), h("span.grow"), h("span.small.muted", "Enforced by the server, not only hidden in the interface"), guideBtn("roles")),
        h("table.t.matrix", h("thead", thead), h("tbody", featRows, allRow, critRows, descRow))));
  }

  // ---- users & study ------------------------------------------------------------------------
  function usersTab() {
    const st = draft.study, acc = draft.access;
    acc.users ||= {};
    st.participants ||= [];
    st.population ||= { size: 20, prefix: "p", digits: 3, roles: {}, shuffle: true, seed: 7, password_words: 2 };
    const pop = st.population;
    pop.roles ||= {};
    const roleNames = Object.keys(acc.roles);
    const label = (n) => acc.roles[n]?.label || n;
    const usersBox = h("div", h("div.skeleton", { style: { height: "60px" } }));
    const setv = (o, k, v, re = false) => { o[k] = v; touch(); if (re) render(); };

    // study settings
    const assignBox = h("div.chips", roleNames.map((n) => {
      const on = (st.assign_roles || []).includes(n);
      return h("button.chip" + (on ? ".on" : ""), { type: "button", onclick: () => {
        const s = new Set(st.assign_roles || []);
        s.has(n) ? s.delete(n) : s.add(n);
        st.assign_roles = roleNames.filter((x) => s.has(x));
        touch(); render();
      } }, label(n));
    }));
    const studyCard = card("Study", "study",
      h("div.field", h("span.field-label", "Name"), h("input", { type: "text", value: st.name || "", onchange: (e) => setv(st, "name", e.target.value) }),
        h("span.hint", "Shown in the browser tab, on the sign-in page and on the registration message.")),
      h("div.field", h("span.field-label", "Description"), h("textarea", { rows: 2, value: st.description || "", onchange: (e) => setv(st, "description", e.target.value) })),
      h("label.switch", h("input", { type: "checkbox", checked: !!st.login_required, onchange: (e) => setv(st, "login_required", e.target.checked, true) }),
        h("b", "Participants sign in with a username and password")),
      st.login_required && !st.participants.some((p) => acc.roles[p.role]?.features?.configure) ?
        h("div.hint", "No participant account has an admin role: administrators open this page with ", h("span.mono", "foodrec config open"), " or FOODREC_ADMIN_TOKEN.") : null,
      h("div.field", h("span.field-label", "Who may start"),
        h("select", { onchange: (e) => setv(st, "registration", e.target.value) },
          [["open", "Anyone with a new user id"], ["listed_only", "Only listed ids and participants"], ["closed", "Nobody new (registration closed)"]]
            .map(([v, t]) => h("option", { value: v, selected: v === st.registration }, t)))),
      h("div.field", h("span.field-label", "Maximum number of users"),
        h("input", { type: "number", min: 1, value: st.max_users ?? "", placeholder: "unlimited", style: { width: "140px" },
          onchange: (e) => setv(st, "max_users", e.target.value ? Math.max(1, Math.round(+e.target.value)) : null) }),
        h("span.hint", "Counts onboarded users; administrators, listed ids and participants are not limited.")),
      h("div.field", h("span.field-label", "Role for new users without an account"),
        h("select", { onchange: (e) => setv(st, "assignment", e.target.value, true) },
          [["default", "The default role"], ["round_robin", "Balanced: rotate through the roles below"], ["random", "Random (seeded) among the roles below"]]
            .map(([v, t]) => h("option", { value: v, selected: v === st.assignment }, t))),
        st.assignment !== "default" ? assignBox : null,
        h("span.hint", "Assigned once, at onboarding, and stored. Participants and fixed roles always win.")));

    // population generator
    const tot = Object.values(pop.roles).reduce((a, v) => a + (+v || 0), 0);
    const counts = (() => { // same largest-remainder rule as the server
      const size = +pop.size || 0, sh = Object.entries(pop.roles).filter(([, v]) => +v > 0);
      const t = sh.reduce((a, [, v]) => a + +v, 0);
      if (!t) return {};
      const ex = Object.fromEntries(sh.map(([r, v]) => [r, (size * v) / t]));
      const c = Object.fromEntries(Object.entries(ex).map(([r, x]) => [r, Math.floor(x)]));
      let left = size - Object.values(c).reduce((a, b) => a + b, 0);
      for (const r of Object.keys(ex).sort((a, b) => (ex[b] - c[b]) - (ex[a] - c[a]))) { if (left-- <= 0) break; c[r]++; }
      return c;
    })();
    const popCard = card("Create the study population", "population",
      h("div.row.wrap", { style: { gap: "12px", alignItems: "flex-end" } },
        h("label.field", h("span.field-label", "Number of users"), h("input", { type: "number", min: 0, max: 10000, value: pop.size, style: { width: "110px" }, onchange: (e) => setv(pop, "size", Math.max(0, Math.round(+e.target.value || 0)), true) })),
        h("label.field", h("span.field-label", "Username prefix"), h("input", { type: "text", value: pop.prefix, style: { width: "90px" }, onchange: (e) => setv(pop, "prefix", e.target.value.trim(), true) })),
        h("label.field", h("span.field-label", "Digits"), h("input", { type: "number", min: 1, max: 6, value: pop.digits, style: { width: "70px" }, onchange: (e) => setv(pop, "digits", Math.round(+e.target.value || 3), true) })),
        h("label.field", h("span.field-label", "Words per password"), h("input", { type: "number", min: 1, max: 4, value: pop.password_words ?? 2, style: { width: "70px" }, onchange: (e) => setv(pop, "password_words", Math.round(+e.target.value || 2)) })),
        h("label.switch", h("input", { type: "checkbox", checked: pop.shuffle !== false, onchange: (e) => setv(pop, "shuffle", e.target.checked) }), "mix roles over usernames"),
        h("label.field", h("span.field-label", "Seed"), h("input", { type: "number", value: pop.seed ?? 7, style: { width: "80px" }, onchange: (e) => setv(pop, "seed", Math.round(+e.target.value || 0)) }))),
      h("table.t", h("thead", h("tr", h("th", "Role"), h("th.num", "Percent"), h("th.num", "Users"))),
        h("tbody", roleNames.map((n) => h("tr", h("td", label(n), h("span.small.muted.mono", ` ${n}`)),
          h("td.num", h("input", { type: "number", min: 0, max: 100, step: 1, value: pop.roles[n] ?? 0, style: { width: "80px" },
            onchange: (e) => { const v = +e.target.value || 0; if (v) pop.roles[n] = v; else delete pop.roles[n]; touch(); render(); } })),
          h("td.num", counts[n] ?? 0))),
        h("tr", h("td", h("b", "Total")), h("td.num" + (Math.abs(tot - 100) > 0.01 ? ".delta-down" : ""), h("b", `${tot}%`)), h("td.num", h("b", pop.size))))),
      h("div.small.muted", `Usernames ${pop.prefix}${"1".padStart(pop.digits, "0")} … ${pop.prefix}${String(pop.size || 1).padStart(pop.digits, "0")}. Existing usernames keep their password.`),
      h("div.btn-row",
        h("button.btn.primary", { disabled: Math.abs(tot - 100) > 0.01 || !pop.size, onclick: async () => {
          if (st.participants.length && !confirm(`Replace the ${st.participants.length} participant(s) with ${pop.size} generated ones? Passwords of usernames that stay are kept.`)) return;
          try {
            const res = await api("/api/config/population", { method: "POST", body: { population: pop, participants: st.participants } });
            st.participants = res.participants;
            touch(); render();
            toast(`${res.participants.length} participants generated: ${Object.entries(res.counts).map(([r, n]) => `${label(r)} ${n}`).join(", ")}. Save to keep them.`);
          } catch (e) { toast(e.message, "err"); }
        } }, "Generate participants"),
        Math.abs(tot - 100) > 0.01 ? h("span.small", { style: { color: "var(--danger)" } }, "The percentages must add up to 100.") : null));

    // participant accounts
    const parts = st.participants;
    const byRole = parts.reduce((a, p) => ({ ...a, [p.role]: (a[p.role] || 0) + 1 }), {});
    const csv = () => {
      const rows = [["username", "password", "role"], ...parts.map((p) => [p.username, p.password, p.role])];
      const blob = new Blob([rows.map((r) => r.map((x) => `"${String(x).replace(/"/g, '""')}"`).join(",")).join("\n") + "\n"], { type: "text/csv" });
      const a = h("a", { href: URL.createObjectURL(blob), download: `${(st.name || "study").replace(/\W+/g, "_")}_credentials.csv` });
      document.body.append(a); a.click(); a.remove();
    };
    const partsCard = card(`Participant accounts (${parts.length})`, "population",
      parts.length ? h("div.small.muted", Object.entries(byRole).map(([r, n]) => `${label(r)} ${n}`).join(" · ")) : h("div.small.muted", "No accounts yet. Generate them above or add one."),
      parts.length ? h("div", { style: { maxHeight: "420px", overflow: "auto" } }, h("table.t", h("thead", h("tr", h("th", "Username"), h("th", "Password"), h("th", "Role"), h("th", "Note"), h("th", ""))),
        h("tbody", parts.map((p, i) => h("tr",
          h("td", h("input.mono", { type: "text", value: p.username, style: { width: "120px" }, onchange: (e) => setv(p, "username", e.target.value.trim()) })),
          h("td", h("input.mono", { type: "text", value: p.password, style: { width: "170px" }, onchange: (e) => setv(p, "password", e.target.value) })),
          h("td", h("select", { onchange: (e) => setv(p, "role", e.target.value, true) }, roleNames.map((n) => h("option", { value: n, selected: n === p.role }, label(n))))),
          h("td", h("input", { type: "text", value: p.note || "", placeholder: "optional", style: { width: "140px" }, onchange: (e) => { if (e.target.value) p.note = e.target.value; else delete p.note; touch(); } })),
          h("td", h("button.btn.ghost.sm", { title: "Remove", onclick: () => { parts.splice(i, 1); touch(); render(); } }, icon("trash")))))))) : null,
      h("div.btn-row",
        h("button.btn.sm", { onclick: () => {
          const used = new Set(parts.map((p) => p.username));
          let n = parts.length + 1; let name;
          do { name = `${pop.prefix || "p"}${String(n++).padStart(pop.digits || 3, "0")}`; } while (used.has(name));
          const w = ["amber", "basil", "cedar", "maple", "river", "willow", "olive", "pebble"];
          parts.push({ username: name, password: `${w[Math.floor(Math.random() * w.length)]}-${w[Math.floor(Math.random() * w.length)]}-${10 + Math.floor(Math.random() * 90)}`, role: acc.default_role });
          touch(); render();
        } }, icon("plus"), "Add an account"),
        parts.length ? h("button.btn.sm", { onclick: csv }, "Download usernames and passwords (CSV)") : null),
      h("div.hint", "Passwords are stored in the config file as written, because you hand them out: keep the file private while it holds real credentials."));

    // fixed roles and users seen by the app
    (async () => {
      try {
        const res = await api("/api/admin/users");
        const known = new Map(res.users.map((u) => [u.user_id, u]));
        const ids = [...new Set([...Object.keys(acc.users), ...res.users.map((u) => u.user_id)])];
        clear(usersBox, ids.length ? h("table.t", h("thead", h("tr", h("th", "User id"), h("th", "Fixed role"), h("th", "Effective role"),
          h("th", "Onboarded"), h("th.num", "Events"), h("th", "Last active"), h("th", ""))),
        h("tbody", ids.map((uid) => {
          const u = known.get(uid);
          return h("tr", h("td.mono", uid),
            h("td", h("select", { onchange: (e) => { if (e.target.value) acc.users[uid] = e.target.value; else delete acc.users[uid]; touch(); render(); } },
              h("option", { value: "", selected: !acc.users[uid] }, "— not fixed —"), roleNames.map((n) => h("option", { value: n, selected: n === acc.users[uid] }, label(n))))),
            h("td.small", u ? `${res.roles[u.role] || u.role} (${u.role_source})` : h("span.muted", "not seen yet")),
            h("td.small", u?.onboarded ? "yes" : "no"), h("td.num", u?.events ?? 0),
            h("td.small.muted", u?.last_active ? ago(u.last_active) : "–"),
            h("td", u ? h("button.btn.ghost.sm", { title: "Download this user's export", onclick: () => download(`/api/export?format=json&users=${encodeURIComponent(uid)}`).catch((e) => toast(e.message, "err")) }, "Export") : null));
        }))) : h("div.small.muted", "No users yet."),
        h("div.small.muted", { style: { padding: "8px 0" } }, `${res.onboarded} onboarded user(s)` + (st.max_users ? ` of at most ${st.max_users}` : "") + "."));
      } catch (e) { clear(usersBox, h("div.small", e.message)); }
    })();
    const newId = h("input", { type: "text", placeholder: "user id", style: { width: "160px" } });
    const newRole = h("select", roleNames.map((n) => h("option", { value: n, selected: n === acc.default_role }, label(n))));
    return h("div.stack",
      h("div.two-col", studyCard, popCard),
      partsCard,
      card("Users and fixed roles", "study",
        h("div.row.wrap", { style: { gap: "6px" } }, newId, newRole,
          h("button.btn", { onclick: () => { const v = newId.value.trim(); if (!v) return; acc.users[v] = newRole.value; newId.value = ""; touch(); render(); } }, icon("plus"), "Fix a role")),
        h("div.hint", "A fixed role (access.users) wins over everything, e.g. for administrators. Participants above get the role of their account."),
        usersBox));
  }

  // ---- export -------------------------------------------------------------------------------
  function exportTab() {
    const users = h("input", { type: "text", placeholder: "all users, or: p001,p002", style: { width: "100%" } });
    const raw = h("input", { type: "checkbox", checked: true });
    const go = (fmt) => {
      const q = new URLSearchParams({ format: fmt, raw: raw.checked });
      if (users.value.trim()) q.set("users", users.value.replace(/\s+/g, ""));
      download(`/api/export?${q}`).then(() => toast("Export downloaded")).catch((e) => toast(e.message, "err"));
    };
    return h("div.two-col",
      card("Download", "export",
        h("div.field", h("span.field-label", "Users"), users),
        h("label.switch", raw, "Include the raw event log"),
        h("div.btn-row", h("button.btn.primary", { onclick: () => go("json") }, "JSON"), h("button.btn", { onclick: () => go("zip") }, "ZIP with CSV tables")),
        h("div.hint", "Command line: ", h("span.mono", "foodrec export --out study.zip"))),
      card("What is in it", "export", h("p.small", "One record per user: role and what it could see, preferences and their history, criteria scores, every list shown with all and visible scores, interactions, supervisor decisions, conversation with agent steps, interface events, the event log, a merged timeline and a summary. The guide lists every field.")));
  }

  // ---- raw JSON -----------------------------------------------------------------------------
  function jsonTab() {
    const ta = h("textarea", { rows: 30, spellcheck: false, style: { width: "100%", fontFamily: "var(--mono)", fontSize: "12px" },
      value: JSON.stringify(draft, null, 2) });
    const msg = h("div.small");
    const parse = () => { try { return JSON.parse(ta.value); } catch (e) { clear(msg, h("span", { style: { color: "var(--danger)" } }, e.message)); return null; } };
    return h("section.card", h("div.card-head", h("h2", "foodrec.config.json"), h("span.grow"),
      h("span.small.muted", "null = use the environment or the default"), guideBtn("json")),
      h("div.card-body.stack", ta, msg, h("div.btn-row",
        h("button.btn", { onclick: async () => {
          const d = parse();
          if (!d) return;
          const errs = (await api("/api/config/validate", { method: "POST", body: { config: d } })).errors;
          clear(msg, errs.length ? h("ul", errs.map((x) => h("li", x))) : h("span.delta-up", "Valid"));
        } }, "Check"),
        h("button.btn.primary", { onclick: () => { const d = parse(); if (d) { draft = d; dirty = true; save(); } } }, "Save this JSON"))));
  }

  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
  load();
  return h("div",
    h("div.page-head", h("div", h("h1", "Config"),
      h("p", "Model, dataset, criteria, access roles and the study. Saved to the config file in the repository; edits to that file by hand are picked up too.")),
      head),
    h("div", { style: { marginBottom: "14px" } }, tabsEl),
    body);
}

