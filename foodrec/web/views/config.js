// Config: the same settings as foodrec.config.json, editable here (roles with "configure" only).
// Tabs: Settings (model, dataset, ranking ...), Access roles, Users & study, Export, JSON.
import { api, ago, clear, download, h, icon, sessionGet, sessionSet, state, toast, userPath } from "../lib.js";

const TABS = [["settings", "Settings"], ["roles", "Access roles"], ["users", "Users & study"], ["export", "Export"], ["json", "JSON file"]];
const clone = (x) => JSON.parse(JSON.stringify(x));

export function configView() {
  let view = null;      // GET /api/config
  let draft = null;     // the file being edited
  let tab = sessionGet("foodrec.config_tab") || "settings";
  let dirty = false;
  const body = h("div");
  const head = h("div.row", { style: { gap: "8px", alignItems: "center" } });
  const tabsEl = h("div.seg");

  function fromView(v) {
    // runtime settings: what the FILE says (null = environment/default); access and study: effective
    const d = { $comment: v.raw.$comment, config_version: v.effective.config_version };
    for (const f of v.fields) (d[f.section] ||= {})[f.key] = f.file_value ?? null;
    d.access = clone(v.effective.access);
    d.study = clone(v.effective.study);
    if (!d.$comment) delete d.$comment;
    return d;
  }

  async function load() {
    try {
      view = await api("/api/config");
      draft = fromView(view);
      dirty = false;
      render();
    } catch (e) {
      if (e.status === 401) return tokenForm();
      clear(body, h("div.empty", h("h3", "Config is not available"), h("p", e.message)));
    }
  }

  function tokenForm() {
    const inp = h("input", { type: "password", placeholder: "FOODREC_ADMIN_TOKEN", style: { width: "260px" } });
    clear(body, h("section.card", h("div.card-body.stack",
      h("h3", "Admin token"),
      h("p.muted", "This server requires the admin token (environment variable FOODREC_ADMIN_TOKEN). It is kept for this browser tab only."),
      h("div.row", inp, h("button.btn.primary", { onclick: () => { sessionSet("foodrec.admin_token", inp.value.trim()); load(); } }, "Continue")))));
  }

  const touch = () => { dirty = true; renderHead(); };

  async function save() {
    try {
      const errs = (await api("/api/config/validate", { method: "POST", body: { config: draft } })).errors;
      if (errs.length) { toast(errs[0], "err"); renderProblems(errs); return; }
      view = await api("/api/config", { method: "PUT", body: { config: draft } });
      draft = fromView(view);
      dirty = false;
      state.system = await api("/api/system");
      state.user_state = await api(userPath("/state"));
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
        view.admin_token ? " · protected by an admin token" : " · not protected by a token: anyone who can reach this server and choose an admin user id can change it"),
      pending.length ? h("div.change", { style: { marginBottom: "12px", borderRadius: "8px", border: "1px solid var(--border)" } },
        h("b", "Waiting for a restart: "), pending.map((p) => `${p.label} (running ${p.running}, saved ${p.saved})`).join("; ")) : null,
      view.errors.length ? h("div.change", h("b", "The file has problems: "), h("ul", view.errors.map((x) => h("li", x)))) : null,
      h("div.cfg-problems"),
      { settings: settingsTab, roles: rolesTab, users: usersTab, export: exportTab, json: jsonTab }[tab]());
  }

  // ---- settings ---------------------------------------------------------------------------
  function fieldInput(f) {
    const cur = draft[f.section][f.key];
    const set = (v) => { draft[f.section][f.key] = v; touch(); };
    const inherit = h("label.switch", { title: "Leave this setting to the environment variable or the built-in default" },
      h("input", { type: "checkbox", checked: cur === null, onchange: (e) => { set(e.target.checked ? null : f.running ?? ""); render(); } }),
      f.env ? `from environment` : "default");
    let input;
    if (cur === null) input = h("span.small.muted.mono", String(f.running ?? "–"));
    else if (f.type === "bool") {
      input = h("label.switch", h("input", { type: "checkbox", checked: !!cur, onchange: (e) => set(e.target.checked) }), cur ? "on" : "off");
    } else if (f.type === "choice") {
      input = h("select", { onchange: (e) => set(e.target.value) }, f.options.map((o) => h("option", { value: o, selected: o === cur }, o)));
    } else if (f.type === "int" || f.type === "float") {
      input = h("input", { type: "number", value: cur, min: f.min, max: f.max, step: f.type === "int" ? 1 : "any", style: { width: "120px" },
        onchange: (e) => set(e.target.value === "" ? null : Number(e.target.value)) });
    } else {
      const list = f.section === "llm" && f.key === "model" ? "cfg-models" : null;
      input = h("input", { type: "text", value: cur, list, style: { width: "100%" }, onchange: (e) => set(e.target.value) });
    }
    return h("div.cfg-field",
      h("div", h("div.row", { style: { gap: "6px" } }, h("b.small", f.label),
        h("span.tag" + (f.apply === "live" ? ".accent" : ".warn"), { title: f.apply === "live" ? "Applied when saved" : "Applied after the app restarts" }, f.apply === "live" ? "live" : "restart")),
        h("div.small.muted.mono", `${f.section}.${f.key}` + (f.env ? ` · env ${f.env}` : "")),
        f.help ? h("div.hint", f.help) : null),
      h("div.stack.tight", input,
        h("div.row.small.muted", { style: { gap: "10px" } }, inherit,
          h("span", `running: `, h("span.mono", String(f.running ?? "–")), ` (${f.source})`))));
  }

  function settingsTab() {
    const models = h("datalist", { id: "cfg-models" }, (view.installed_models || []).map((m) => h("option", { value: m })));
    const llm = view.llm_status || {};
    return h("div.stack", models,
      Object.entries(view.sections).map(([sec, label]) => h("section.card",
        h("div.card-head", h("h2", label),
          sec === "llm" ? h("span.small.muted", h("span.dot." + (llm.available ? "ok" : "warn")), " ",
            llm.available ? `${llm.model} available` : (llm.reason || "model not available"),
            view.installed_models?.length ? ` · ${view.installed_models.length} model(s) installed` : "") : null),
        h("div.card-body.stack", view.fields.filter((f) => f.section === sec).map(fieldInput),
          sec === "dataset" ? h("div.hint", "Your own dataset: build a catalogue with ",
            h("span.mono", "python -m foodrec.ingest --recipes my_recipes.json --name my_study --out data/my_study/catalogue.json.gz"),
            ", set the catalogue file to it and the runtime data folder to a new folder (e.g. data/my_study/runtime), then restart. See docs/configuration.md.") : null))));
  }

  // ---- access roles -----------------------------------------------------------------------
  function rolesTab() {
    const acc = draft.access;
    const names = Object.keys(acc.roles);
    const cell = (role, get, setv, disabled) => h("td.num", h("input", {
      type: "checkbox", checked: get(), disabled, onchange: (e) => { setv(e.target.checked); touch(); },
    }));
    const feat = (r, k) => r.features?.[k] ?? !view.features.find((f) => f.id === k).admin_only;
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
        touch(); render();
      } }, icon("trash")) : null)));
    const featRows = view.features.map((f) => h("tr",
      h("td", h("b.small", f.label), h("div.small.muted", f.description)),
      names.map((n) => cell(n, () => feat(acc.roles[n], f.id), (v) => { (acc.roles[n].features ||= {})[f.id] = v; }))));
    const critRows = view.criteria.map((c) => h("tr",
      h("td", h("b.small", `Show score: ${c.label}`), h("div.small.muted", "The score chip and its share in the breakdowns")),
      names.map((n) => cell(n, () => (acc.roles[n].visible_criteria ?? view.criteria.map((x) => x.id)).includes(c.id), (v) => {
        const cur = new Set(acc.roles[n].visible_criteria ?? view.criteria.map((x) => x.id));
        v ? cur.add(c.id) : cur.delete(c.id);
        acc.roles[n].visible_criteria = view.criteria.map((x) => x.id).filter((x) => cur.has(x));
      }))));
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
          } }, icon("plus"), "Add"))))),
      h("section.card", { style: { overflowX: "auto" } },
        h("div.card-head", h("h2", "What each role sees"), h("span.small.muted", "Enforced by the server, not only hidden in the interface")),
        h("table.t.matrix", h("thead", thead), h("tbody", featRows, critRows, descRow))));
  }

  // ---- users & study ----------------------------------------------------------------------
  function usersTab() {
    const st = draft.study, acc = draft.access;
    acc.users ||= {};
    const roleNames = Object.keys(acc.roles);
    const usersBox = h("div", h("div.skeleton", { style: { height: "60px" } }));
    const assignBox = h("div.chips", roleNames.map((n) => {
      const on = (st.assign_roles || []).includes(n);
      return h("button.chip" + (on ? ".on" : ""), { type: "button", onclick: () => {
        const s = new Set(st.assign_roles || []);
        s.has(n) ? s.delete(n) : s.add(n);
        st.assign_roles = roleNames.filter((x) => s.has(x));
        touch(); render();
      } }, acc.roles[n].label || n);
    }));
    const roleSelect = (uid, cur, { allowNone }) => h("select", { onchange: (e) => {
      if (e.target.value) acc.users[uid] = e.target.value; else delete acc.users[uid];
      touch(); render();
    } }, allowNone ? h("option", { value: "", selected: !cur }, "— not fixed —") : null,
    roleNames.map((n) => h("option", { value: n, selected: n === cur }, acc.roles[n].label || n)));

    (async () => {
      try {
        const res = await api("/api/admin/users");
        const known = new Map(res.users.map((u) => [u.user_id, u]));
        const ids = [...new Set([...Object.keys(acc.users), ...res.users.map((u) => u.user_id)])];
        clear(usersBox, h("table.t", h("thead", h("tr", h("th", "User id"), h("th", "Fixed role (config file)"), h("th", "Effective role"),
          h("th", "Onboarded"), h("th.num", "Events"), h("th", "Last active"), h("th", ""))),
        h("tbody", ids.map((uid) => {
          const u = known.get(uid);
          return h("tr", h("td.mono", uid), h("td", roleSelect(uid, acc.users[uid], { allowNone: true })),
            h("td.small", u ? `${res.roles[u.role] || u.role} (${u.role_source})` : h("span.muted", "not seen yet")),
            h("td.small", u?.onboarded ? "yes" : "no"), h("td.num", u?.events ?? 0),
            h("td.small.muted", u?.last_active ? ago(u.last_active) : "–"),
            h("td", u ? h("button.btn.ghost.sm", { title: "Download this user's export", onclick: () => download(`/api/export?format=json&users=${encodeURIComponent(uid)}`).catch((e) => toast(e.message, "err")) }, "Export") : null));
        }))),
        h("div.small.muted", { style: { padding: "8px 12px" } }, `${res.onboarded} onboarded user(s)` + (st.max_users ? ` of at most ${st.max_users}` : "") + "."));
      } catch (e) { clear(usersBox, h("div.small", e.message)); }
    })();

    const newId = h("input", { type: "text", placeholder: "participant id, e.g. p01", style: { width: "180px" } });
    const newRole = h("select", roleNames.map((n) => h("option", { value: n, selected: n === acc.default_role }, acc.roles[n].label || n)));
    const bulk = h("textarea", { rows: 3, placeholder: "Several ids at once: p01, p02, p03 … (comma, space or new line)", style: { width: "100%" } });
    const addIds = (ids) => {
      const added = ids.map((x) => x.trim()).filter(Boolean);
      for (const id of added) acc.users[id] = newRole.value;
      if (added.length) { touch(); render(); toast(`${added.length} user(s) get the role ${newRole.value} when saved`); }
    };
    return h("div.two-col",
      h("section.card", h("div.card-head", h("h2", "Study")), h("div.card-body.stack",
        h("div.field", h("span.field-label", "Name"), h("input", { type: "text", value: st.name || "", onchange: (e) => { st.name = e.target.value; touch(); } }),
          h("span.hint", "Shown in the browser tab and on the registration message.")),
        h("div.field", h("span.field-label", "Description"), h("textarea", { rows: 2, value: st.description || "", onchange: (e) => { st.description = e.target.value; touch(); } })),
        h("div.field", h("span.field-label", "Who may start"),
          h("select", { onchange: (e) => { st.registration = e.target.value; touch(); } },
            [["open", "Anyone with a new user id"], ["listed_only", "Only the user ids listed below"], ["closed", "Nobody new (registration closed)"]]
              .map(([v, t]) => h("option", { value: v, selected: v === st.registration }, t)))),
        h("div.field", h("span.field-label", "Maximum number of users"),
          h("input", { type: "number", min: 1, value: st.max_users ?? "", placeholder: "unlimited", style: { width: "140px" },
            onchange: (e) => { st.max_users = e.target.value ? Math.max(1, Math.round(+e.target.value)) : null; touch(); } }),
          h("span.hint", "Counts onboarded users; administrators and listed ids are not limited.")),
        h("div.field", h("span.field-label", "Role for new users"),
          h("select", { onchange: (e) => { st.assignment = e.target.value; touch(); render(); } },
            [["default", "The default role"], ["round_robin", "Balanced: rotate through the roles below"], ["random", "Random (seeded) among the roles below"]]
              .map(([v, t]) => h("option", { value: v, selected: v === st.assignment }, t))),
          st.assignment !== "default" ? assignBox : null,
          h("span.hint", "Assigned once, at onboarding, and stored. A fixed role in the list below always wins.")),
        st.assignment === "random" ? h("div.field", h("span.field-label", "Random seed"),
          h("input", { type: "number", value: st.seed ?? 7, style: { width: "100px" }, onchange: (e) => { st.seed = Math.round(+e.target.value); touch(); } })) : null)),
      h("section.card", h("div.card-head", h("h2", "Users")), h("div.card-body.stack",
        h("div.row.wrap", { style: { gap: "6px" } }, newId, newRole,
          h("button.btn", { onclick: () => { addIds([newId.value]); newId.value = ""; } }, icon("plus"), "Add")),
        h("div.stack.tight", bulk, h("div.row", h("button.btn.sm", { onclick: () => addIds(bulk.value.split(/[\s,;]+/)) }, "Add all with the role above"))),
        h("div.hint", "Fixed roles are written to the config file (access.users).")),
        usersBox));
  }

  // ---- export -----------------------------------------------------------------------------
  function exportTab() {
    const users = h("input", { type: "text", placeholder: "all users, or: p01,p02", style: { width: "100%" } });
    const raw = h("input", { type: "checkbox", checked: true });
    const go = (fmt) => {
      const q = new URLSearchParams({ format: fmt, raw: raw.checked });
      if (users.value.trim()) q.set("users", users.value.replace(/\s+/g, ""));
      download(`/api/export?${q}`).then(() => toast("Export downloaded")).catch((e) => toast(e.message, "err"));
    };
    const fields = [
      ["user_id, access", "Role, the features it had and the criteria it could see"],
      ["preferences", "Declared profile (allergies, diets, likes, health goal) and goal weights, with every earlier version"],
      ["criteria", "Per criterion: saved weight, weight revealed by behaviour, gap, visible or not, mean score shown and chosen"],
      ["recommendations", "Every list shown: weights, filters, and per recipe all scores plus the scores that were visible"],
      ["interactions", "Feedback with the recipe's rank and the scores visible when it was given, meal and place"],
      ["decisions", "Supervisor questions and proposals with the user's response"],
      ["conversation", "Chat turns with the agent steps behind each answer"],
      ["ui_events", "Page views, panels opened, assistant opened (interface interaction)"],
      ["events, log", "The raw event log, and one timeline merging everything"],
      ["summary", "Counts, acceptance rate, mean rank chosen, preference match, active minutes"],
    ];
    return h("div.two-col",
      h("section.card", h("div.card-head", h("h2", "Download")), h("div.card-body.stack",
        h("div.field", h("span.field-label", "Users"), users),
        h("label.switch", raw, "Include the raw event log"),
        h("div.btn-row", h("button.btn.primary", { onclick: () => go("json") }, "JSON"), h("button.btn", { onclick: () => go("zip") }, "ZIP with CSV tables")),
        h("div.hint", "Command line: ", h("span.mono", "foodrec export --out study.zip")))),
      h("section.card", h("div.card-head", h("h2", "What is in it")), h("table.t", h("tbody",
        fields.map(([k, d]) => h("tr", h("td.mono.small", k), h("td.small", d)))))));
  }

  // ---- raw JSON ---------------------------------------------------------------------------
  function jsonTab() {
    const ta = h("textarea", { rows: 28, spellcheck: false, style: { width: "100%", fontFamily: "var(--mono, monospace)", fontSize: "12px" },
      value: JSON.stringify(draft, null, 2) });
    const msg = h("div.small");
    const parse = () => { try { return JSON.parse(ta.value); } catch (e) { clear(msg, h("span", { style: { color: "var(--danger)" } }, e.message)); return null; } };
    return h("section.card", h("div.card-head", h("h2", "foodrec.config.json"), h("span.small.muted", "Edit the whole file; null means \"use the environment or the default\".")),
      h("div.card-body.stack", ta, msg, h("div.btn-row",
        h("button.btn", { onclick: async () => {
          const d = parse();
          if (!d) return;
          const errs = (await api("/api/config/validate", { method: "POST", body: { config: d } })).errors;
          clear(msg, errs.length ? h("ul", errs.map((x) => h("li", x))) : h("span.delta-up", "Valid"));
        } }, "Check"),
        h("button.btn.primary", { onclick: () => { const d = parse(); if (d) { draft = d; dirty = true; save(); } } }, "Save this JSON"))));
  }

  load();
  return h("div",
    h("div.page-head", h("div", h("h1", "Config"),
      h("p", "Model, dataset, ranking, access roles and the study. Saved to the config file in the repository; the same file can be edited by hand.")),
      head),
    h("div", { style: { marginBottom: "14px" } }, tabsEl),
    body);
}

