# Configuration, access roles, export and scenarios

Everything that controls how FoodRec runs lives in one JSON file, **`foodrec.config.json`** in the
repository root: the language model, the dataset, the criteria users weigh, the feedback options,
access roles and the study population. The **Config page** (part of the administrator's page, `/config`, separate from the app)
edits the same file, and every setting on the page is a key in the file. Use another file with
`foodrec --config path/to/study.json serve` or `FOODREC_CONFIG=...`.

```bash
foodrec config open       # open the Config page (starts the app if it is not running)
foodrec config show       # every setting, its value and where it came from (config / env / default)
foodrec config check      # validate the file
foodrec config init       # write a fresh file with all defaults (--force to overwrite)
```

Every section of the Config page has a **Guide** button with a full explanation (the guides are the
markdown files in `foodrec/web/guides/`, readable in the repository as well).

## Starting, opening and stopping

| Command | What it does |
|---|---|
| `foodrec` (= `foodrec open`) | opens the app in the browser; starts it first if it is not running |
| `foodrec config open` | opens the Config page; starts the app first if it is not running |
| `foodrec serve` | runs the server only, without a browser (e.g. on a study server) |
| `foodrec status` | is the app running, on which port, with which process id |
| `foodrec stop` | stops the running app, from any terminal |

The running app also restarts itself when its own Python code changes (after an edit or `git pull`), so the
server never stays older than the web pages; a change that does not compile is not used. `foodrec stop`
also finds servers started by older versions.

Only one FoodRec runs per port. If one is already running, `foodrec` and `foodrec config open` just open
the browser, and `foodrec serve` says so instead of starting a second copy (a second copy could not use
the port, and would find the embedded vector store locked). If another program uses the port, the command
says so before loading anything; pick another port with `--port` or `server.port`. All commands accept
`--host` / `--port`, and `--no-browser` prints the link only.

## The Config page and reloading

`foodrec config open` checks whether the app is running on the configured host and port. If it is, it
opens the browser at `/config`; if not, it starts the app and opens the page when it is ready
(`--no-browser` prints the link only). The link carries this installation's admin token
(`data/runtime/.admin_session`, readable only by you), so the page opens as administrator without
choosing a user. An administrator who signs in at the app goes straight to this page, which has two parts,
**Monitor** and **Config**, and nothing of the user side. **Sign out** is in the account menu. After a restart caused by a config change,
the app comes back as a plain server and no extra tab is opened.

The running app **watches the config file** (`server.auto_reload`, on by default). Whether a change comes
from the page or from editing the file by hand:

| Change | What happens |
|---|---|
| nothing (saved again, formatting only) | nothing |
| invalid JSON or invalid settings | the app keeps its last valid config; the Config page shows the problem |
| live settings, roles, study, participant accounts | applied in place, no interruption |
| a restart setting, `criteria` or `interactions` | the app restarts itself with the same command line (a few seconds); the Config page waits and reloads |

## Precedence

`built-in default < environment / .env < foodrec.config.json`

A value of `null` in the file means *"not set here"*: the environment variable or the default applies.
Service addresses (`llm.url`, `services.*`) ship as `null`, so a `.env` keeps working. Secrets never go in
the file: `NEO4J_PASSWORD` and `FOODREC_ADMIN_TOKEN` are read from the environment only.

## Settings

| Section | Key | Applies | Notes |
|---|---|---|---|
| `llm` | `enabled`, `provider`, `model`, `url`, `base_url`, `api_key_env`, `effort`, `max_tokens`, `fallbacks`, `ctx`, `think`, `timeout_s`, `seed` | at once | See *Language models* below |
| `llm` | `embed_provider`, `embed_model`, `embed_on_cpu` | after restart | |
| `server` | `host`, `port` | after restart | `null` = `FOODREC_HOST` / `FOODREC_PORT` |
| `server` | `auto_reload` | at once | Watch the config file |
| `dataset` | `catalogue_path`, `knowledge_dir`, `data_dir` | after restart | Your own dataset: see below |
| `services` | `neo4j_uri`, `neo4j_user`, `neo4j_database`, `qdrant` | after restart | `null` = environment |
| `ranking` | `top_k`, `default_diversity`, `default_mode`, `max_missing_share` | at once | |
| `ranking` | `min_nutrient_coverage` | after restart | |
| `ranking` | `hide_saved`, `fatigue_per_view`, `fatigue_max`, `fatigue_half_life_days`, `cuisine_fatigue_per_slot`, `cuisine_fatigue_max`, `history_lists`, `discovery_slots`, `discovery_days`, `freshness_window_days` | at once | *Fresh lists* below |
| `supervisor` | `max_agent_steps`, `ask_gap`, `ask_min_events`, `propose_gap`, `propose_min_events`, `taste_conflict_mean`, `taste_min_evidence`, `cooldown_events` | at once | when the supervisor asks or proposes |
| `cycle` | `enabled`, `meals`, `recommendations`, `days` | at once | meal cycles on Discover |

The Config page marks each setting **live** or **restart**. After saving, it lists the changes that are still
waiting for a restart.

## Language models

| `llm.provider` | Models | Key |
|---|---|---|
| `ollama` (default) | any pulled model, e.g. `qwen3.5:2b-q4_K_M`, `qwen3:4b` | none |
| `openai` | any OpenAI-compatible API at `llm.base_url`: OpenAI, Groq, Together, OpenRouter, vLLM, LM Studio, Ollama's `/v1` | `llm.api_key_env`, default `OPENAI_API_KEY` |
| `anthropic` | the Claude API, e.g. `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5` (`pip install -e ".[anthropic]"`) | `llm.api_key_env`, default `ANTHROPIC_API_KEY` (or an `ant auth login` profile) |

`llm.api_key_env` is the **name** of the environment variable that holds the key. Paste the key itself
into the field under it on the Config page: the server writes it to `.env` (added or updated, other lines
kept, file readable only by you, ignored by git) and uses it at once. The config file never contains the
key. Editing `.env` by hand works too (then restart). Structured output uses each provider's JSON-schema feature (the OpenAI-compatible client falls back
to JSON mode or an instruction when a server does not support schemas). For Anthropic, `llm.effort`
(default `low`) sets how much the model thinks, and `llm.fallbacks` (default on) enables the server-side
refusal fallback on the models that support it. A failed or declined call falls back to rules and templates
for that turn. Embeddings come from `llm.embed_provider` (`ollama`, `openai` or `none`), independent of the
chat provider.

The model never ranks recipes, so the lists are the same with every model. Compare models on the same
conversation with `foodrec scenario run scenarios/model_comparison.json --matrix llm.model=a,b`.

## Criteria and interactions

`criteria` defines what users weigh at onboarding; `interactions` the feedback options and how much they
count. Both are read when the app starts, so changing them restarts it.

```json
"criteria": {"taste_share": 0.25, "items": [
  {"id": "h", "kind": "healthiness", "label": "Healthiness", "enabled": true, "default_weight": 0.4},
  {"id": "s", "kind": "sustainability", "label": "Sustainability", "enabled": true, "default_weight": 0.3},
  {"id": "t", "kind": "convenience", "label": "Convenience", "enabled": true, "default_weight": 0.3},
  {"id": "e", "kind": "field", "label": "Low energy", "field": "nutrition_per_100g.kcal",
   "direction": "lower", "best": 80, "worst": 400, "scale": "linear", "unit": "kcal/100 g",
   "default_weight": 0.2, "keywords": ["energy", "calorie"]}]}
```

The built-in kinds keep their formulas and ids (`h`, `s`, `t`) and can be switched off or relabelled. A
`field` criterion scores any numeric recipe field between a best and a worst anchor, including the
`extra.<name>` values of your own dataset (price, preparation time ...). The Config page lists the numeric
fields of the loaded catalogue and can set the anchors from the data.

```json
"interactions": {
  "feedback_strength": {"like": 1.0, "save": 0.6, "choose": 0.8},
  "choose_reasons": [{"id": "health", "label": "It is healthy", "criterion": "h", "weight": 1.0}, ...],
  "reject_reasons": [{"id": "not_my_taste", "label": "Not my taste", "criterion": null, "taste_strength": 1.0},
                     {"id": "had_recently", "label": "Had it recently", "criterion": null, "effect": "recency"}, ...],
  "learning": {"eta": 1.0, "nudge": 0.2, "anchor": 0.05, "floor": 0.02, "today_nudge": 0.10, "conf_k": 5}}
```

How each value changes the results is explained in the Criteria and Interactions guides
(`foodrec/web/guides/criteria.md`, `interactions.md`). In short: users keep their saved weights when
criteria change (a removed criterion is dropped, a new one starts at 0, the rest is rescaled); every
enabled criterion gets a choose and a reject reason (added automatically when missing); reasons with a
criterion move the revealed weights, reasons without one are about taste. Scenario files can use other
criteria in their `config`: they then run in a separate process automatically.

## Fresh lists and My dishes

Without help the same dishes would stay on top of Discover day after day. These mechanisms keep the lists
fresh, all disclosed on the card, stored with each list and adjustable in `ranking`:

* **Saved for later** dishes (the Save button) wait on the **My dishes** page (in the account menu, top right,
  where the user id is) instead of taking a Discover place (`hide_saved`). My dishes also lists what the user **liked** and **cooked**; any dish can be removed
  from a list there (nothing that was learned changes), and removing it from Saved lets Discover show it again.
* **Dish fatigue**: every past session in which a dish was shown and ignored lowers its utility a little
  (`fatigue_per_view`, capped by `fatigue_max`, fading with `fatigue_half_life_days`).
* **Cuisine fatigue**: a cuisine that filled many recent list places lowers all its dishes a little
  (`cuisine_fatigue_per_slot`, `cuisine_fatigue_max`), so other cuisines come up.
* **Variety across lists**: the diversity step also keeps a new list apart from the last `history_lists` lists.
* **New for you** (off by default): `discovery_slots` places for the best dish of a cuisine not shown within
  `discovery_days`.

Lists of the last hour do not count yet, so a list stays stable while the user works with it. Measured on
HUMMUS over two simulated weeks (`scenarios/fresh_lists.json`, 30 lists): 27 different dishes and 5 cuisines
without freshness, 55 and 7 with the defaults, 61 and 10 with one New-for-you place. Set the fatigue values
and `history_lists` to 0 to get the old, fixed ranking.

## Your own (private) dataset

1. Put your recipes in the simple JSON format (example: `data/examples/my_recipes.json`; the format is described in
   `foodrec/ingest/simple.py`). Only `title`, `ingredients` and `nutrition_per_100g.kcal` are required. A
   spreadsheet in the HUMMUS column layout (`.xlsx` or `.csv`) works with `--xlsx` instead.
   Numbers in a recipe's `"extra": {...}` (price, preparation time, a rating ...) are kept as
   `extra.<name>` and can be used as field criteria. The Config page (Settings → Dataset) downloads the
   template; its guide lists every field.
2. Build the catalogue. Food groups, CO₂e, allergens, diets, cuisine and meal type are derived as for HUMMUS:
   ```bash
   python -m foodrec.ingest --recipes my_recipes.json --name my_study --out data/my_study/catalogue.json.gz
   ```
3. In the config, set `dataset.catalogue_path` to that file and `dataset.data_dir` to a **new** folder
   (e.g. `data/my_study/runtime`), so users, events and the vector index stay separate from HUMMUS. Restart.

Keep private data out of git: `data/*/runtime/` and `out/` are ignored. Add your own catalogue path to
`.gitignore` too if it must not be committed.

## Access roles

`access.roles` defines what each role sees. `access.users` gives specific user ids a fixed role, and
`access.default_role` covers everyone else. Shipped roles: `admin`, `full`, `no_explanations`, `baseline`.
On the Config page, **Access roles** is a matrix of features by roles.

| Feature | What it controls |
|---|---|
| `assistant` | The chat dock |
| `agent_trace` | Step-by-step traces under answers and in the supervisor feed |
| `explanations` | Why-reasons on cards, and explaining skills in chat (why, compare, beliefs, alternatives) |
| `score_breakdown` | "How it's scored": factor tables and formulas |
| `utility` | The utility number and contribution bar |
| `food_details` | Nutrition, kcal, CO₂e, health grade, ingredient list |
| `images` | Recipe thumbnails |
| `feedback` | Like / save / I'll cook this / not for me |
| `supervisor` | Supervisor questions and goal proposals after feedback |
| `weight_controls` | Priority sliders, saving goals, what-if previews |
| `page_insights`, `page_knowledge`, `page_activity`, `page_profile` | Those pages |
| `export_own` | Downloading one's own record |
| `switch_user` | Changing the user id in the top bar |
| `configure` | The Config page, all exports, study administration (off unless set) |

`visible_criteria` lists the scores a role sees (`p` taste, `h` healthiness, `s` sustainability,
`t` convenience). `blocked_skills` can remove further chat skills.

**Enforcement happens on the server, not only in the UI.** Hidden fields are removed from API responses,
endpoints for disabled features return 403, and the supervisor skips skills the role does not allow.
Stored request snapshots keep the full data plus an `access` record of what the user could see, so the
export can report both.

**Who is the administrator.** In `local` mode (the default) the administrator is the account in
`access.admin`; it signs in like everyone else and is the only one who sees Config. `FOODREC_ADMIN_TOKEN`
and the `foodrec config open` link also open the Config page. See *Modes and sign-in* below.

## Modes and sign-in

`server.mode` decides who uses the app and how:

| Mode | Who | Config page |
|---|---|---|
| `local` (default) | everyone signs in: the **administrator account** (`access.admin`) or a **participant account** (`study.participants`) | administrator only, from the account menu or `foodrec config open` |
| `demo` | public showcase, no sign-in: each browser is a visitor and picks **User view** or **Admin view** in the account menu | admin view only, **read-only**: nothing can be saved, no API keys, no other visitors' data |
| `open` | no sign-in; the browser names the user (switch in the account menu) | anyone whose role has `configure`; for development and automated tests only |

**The administrator account.** The repository ships `"access": {"admin": {"username": "admin", "password":
null}}`, and in `local` mode **the app does not start until a password is set**:

```bash
foodrec admin password            # asks twice, stores a hash
```

or write it once in the file (`"password": "choose-a-password"`); the app replaces it with
`"password_hash"` when it starts or when the file changes. The Config page (Users & study →
Administrator account) changes it the same way. Changing a password, or removing a participant, signs
that person out at once. Sign-in attempts are limited to 10 per 5 minutes per address.

## Study settings and the study population

```json
"study": {"name": "Pilot study", "description": "",
          "registration": "open",            // open | listed_only | closed
          "max_users": 40,                   // onboarded users; admins and listed ids are not limited
          "assignment": "round_robin",       // default | round_robin | random (seeded)
          "assign_roles": ["full", "baseline"], "seed": 7}
```

**Study population.** Instead of adding participants one by one, set `study.population` (number of users,
percent per role, username prefix) and generate the accounts on the Config page (**Users & study → Create
the study population**) or with `foodrec study generate --size 40 --role full=50 --role baseline=50
--write`. Each account gets a readable password (`maple-river-42`); usernames, passwords and roles can be
edited before saving, and regenerating keeps existing passwords. Participants sign in with these accounts
and can only use their own account; they never see Config:

```json
"study": {"population": {"size": 40, "prefix": "p", "digits": 3, "roles": {"full": 50, "baseline": 50},
                         "shuffle": true, "seed": 7, "password_words": 2},
          "participants": [{"username": "p001", "password": "maple-river-42", "role": "baseline"}, ...]}
```

Passwords are stored as written because the study team hands them out: keep the file private while it
holds real credentials. `foodrec study list` prints them; the page downloads them as CSV.

Role precedence: a fixed role in `access.users` > the participant account's role > an assignment > the
default role. A role is assigned **once, at onboarding**, and stored in the database. A fixed role in `access.users` always
wins over an assignment. The **Users & study** tab lists every user with their effective role, and bulk-adds
participant ids.

## Export

The standard output for analysis has one record per user:

```bash
foodrec export --out study.json               # or --out study.zip for CSV tables + export.json
foodrec export --users p01,p02 --no-raw --out two.json
```

It is also available on the Config page (**Export** tab), at `GET /api/export?format=json|zip&users=...`
(admin), and as `GET /api/users/{id}/export` for users with `export_own`.

| Field | Content |
|---|---|
| `user_id`, `access` | Role, its features, the criteria it could see, and where the role came from |
| `preferences` | Declared profile and goal weights, plus every earlier version |
| `criteria` | Per criterion: saved weight, weight revealed by behaviour, gap, visible?, mean score shown / chosen |
| `taste_beliefs` | Learned taste concepts with evidence |
| `recommendations` | Every list: weights, filters, role; per recipe all `scores` **and** `visible_scores` |
| `interactions` | Feedback with rank in list, reason, scope, meal/place, and the scores visible at the time |
| `decisions` | Supervisor questions and proposals, with the user's resolution |
| `conversation` | Chat turns with the agent steps behind each answer |
| `ui_events` | Page views, details opened, assistant opened/closed, suggestion clicks |
| `my_dishes` | What is in Saved for later, Liked and Cooked at export time |
| `events` | The raw append-only event log |
| `log` | One chronological timeline merging everything above |
| `summary` | Counts, proposal acceptance rate, mean rank / utility chosen, preference match, active minutes |

The ZIP adds flat tables: `users.csv`, `recommendations.csv` (one row per shown recipe, with `visible_*`
columns), `interactions.csv`, `decisions.csv`, `messages.csv`, `ui_events.csv`, `criteria.csv`,
`log.csv` and `events.csv`.

## Scenarios

A scenario is a JSON file of steps that runs against a fresh, isolated copy of the app: an empty database
in a temporary folder, the in-process graph and vector index, and your embedding cache reused. It never
touches live data.

```bash
foodrec scenario check scenarios/*.json
foodrec scenario run scenarios/sara_goal_drift.json
foodrec scenario run scenarios/*.json --out out/scenarios
foodrec scenario run scenarios/model_comparison.json --matrix llm.model=qwen3.5:2b-q4_K_M,qwen3:4b
foodrec scenario run scenarios/sara_goal_drift.json --set llm.enabled=true --set ranking.top_k=8
```

Each run writes `report.json` (every step with output and timing, every assertion), `export.json` (the
standard export of the scenario's users) and `summary.md`. The exit code is 1 if a step errored or an
assertion failed, so scenarios can run in CI.

```json
{"name": "my_scenario", "description": "...", "user": "sara",
 "base_config": "defaults",                         // optional: ignore foodrec.config.json
 "config": {"llm": {"enabled": false}},              // merged over the config file
 "steps": [ {"action": "onboard", "declared": {...}, "weights": {"h": 0.4, "s": 0.4, "t": 0.2}}, ... ]}
```

| Action | Fields |
|---|---|
| `onboard` | `declared`, `weights`, `diversity`, `mode` |
| `recommend` | `meal`, `weights`, `diversity`, `mode` |
| `feedback` | `recipe`, `type` (like/save/choose/reject), `reason`, `scope`, `context`, `review` (default true) |
| `chat` | `text`, `think` |
| `review` | Run the supervisor check |
| `resolve` | `decision` (`latest`, `latest:<kind>` or an id), `do` (accept/dismiss/answer), `answer` |
| `update_profile` | `patch` (keys to change) or `declared` (whole profile) |
| `save_goal`, `revert_goal`, `what_if`, `goal_options` | As in the API |
| `correct_belief`, `add_document`, `insight`, `ui_event` | |
| `set_user` | `user`, optional `role`; `set_role`: `role` |
| `advance_time` | `days` and/or `hours`: move the app clock forward (fatigue fading, "only today", cool-downs) |
| `dishes` | read My dishes into `dishes` (e.g. `dishes.saved.length`) |
| `remove_dish` | `recipe`, `collection` (`saved`, `liked`, `cooked`) |
| `repeat` | `times`, `steps` |
| `note` | `text` (shown in the report) |
| `expect` | `that` + one or more of `equals`, `not_equals`, `gt`, `gte`, `lt`, `lte`, `contains`, `not_contains`, `in`, `exists`, `matches` |

**Recipe references:** `"#2"` (rank in the last list), `"best:t"` / `"worst:h"` (the recipe in the last list
with the highest or lowest score on a criterion, for simulating consistent behaviour), `"saved:1"` (the first
dish on a My dishes list), a recipe id, or a title. Every `recommend` also updates `seen` (`seen.recipes`,
`seen.cuisines`: distinct dishes and cuisines shown so far in the run).

**`expect` paths** read from `result` (last list), `chat`, `feedback`, `review`, `resolved`, `what_if`,
`insight`, `state` (fresh user state), `open_decisions`, `error` (error of the previous step), and
`last_step`. Use dots and indexes: `result.results[0].title`, `chat.skills`, `state.goal.version`,
`open_decisions.length`. Any step can set `"times": n`, `"user": "..."`, `"label": "..."`, and
`"allow_error": true` (for steps that should be refused).

Example scenarios are in `scenarios/`: `sara_goal_drift`, `reza_safety_rules`, `study_access_roles`,
`model_comparison`, `energy_criterion` and `fresh_lists` (two simulated weeks, with `advance_time`) (other criteria and reasons, run in its own process).
