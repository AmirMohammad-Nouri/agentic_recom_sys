# Configuration, access roles, export and scenarios

Everything that controls how FoodRec runs lives in one JSON file, **`foodrec.config.json`** in the
repository root. The **Config** page in the app (visible to roles with the `configure` feature) edits the
same file. Use another file with `foodrec --config path/to/study.json serve` or `FOODREC_CONFIG=...`.

```bash
foodrec config show       # every setting, its value and where it came from (config / env / default)
foodrec config check      # validate the file
foodrec config init       # write a fresh file with all defaults (--force to overwrite)
```

## Precedence

`built-in default < environment / .env < foodrec.config.json`

A value of `null` in the file means *"not set here"*: the environment variable or the default applies.
Service addresses (`llm.url`, `services.*`) ship as `null`, so a `.env` keeps working. Secrets never go in
the file: `NEO4J_PASSWORD` and `FOODREC_ADMIN_TOKEN` are read from the environment only.

## Settings

| Section | Key | Applies | Notes |
|---|---|---|---|
| `llm` | `enabled`, `url`, `model`, `ctx`, `think`, `timeout_s`, `seed` | at once | Switch the supervisor model here to test another LLM |
| `llm` | `embed_model`, `embed_on_cpu` | after restart | |
| `dataset` | `catalogue_path`, `knowledge_dir`, `data_dir` | after restart | Your own dataset: see below |
| `services` | `neo4j_uri`, `neo4j_user`, `neo4j_database`, `qdrant` | after restart | `null` = environment |
| `ranking` | `top_k`, `default_diversity`, `default_mode`, `max_missing_share` | at once | |
| `ranking` | `min_nutrient_coverage` | after restart | |
| `supervisor` | `max_agent_steps` | at once | |

The Config page marks each setting **live** or **restart**. After saving, it lists the changes that are still
waiting for a restart.

## Your own (private) dataset

1. Put your recipes in the simple JSON format (example: `data/examples/my_recipes.json`; the format is described in
   `foodrec/ingest/simple.py`). Only `title`, `ingredients` and `nutrition_per_100g.kcal` are required. A
   spreadsheet in the HUMMUS column layout (`.xlsx` or `.csv`) works with `--xlsx` instead.
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

**Admin protection.** Without `FOODREC_ADMIN_TOKEN`, the server trusts the `X-User` header that the browser
sends, so anyone who can reach the server can choose an admin id. That is fine on `127.0.0.1`. For any
shared deployment, set `FOODREC_ADMIN_TOKEN`; the Config page then asks for it.

## Study settings

```json
"study": {"name": "Pilot study", "description": "",
          "registration": "open",            // open | listed_only | closed
          "max_users": 40,                   // onboarded users; admins and listed ids are not limited
          "assignment": "round_robin",       // default | round_robin | random (seeded)
          "assign_roles": ["full", "baseline"], "seed": 7}
```

A role is assigned **once, at onboarding**, and stored in the database. A fixed role in `access.users` always
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
| `repeat` | `times`, `steps` |
| `note` | `text` (shown in the report) |
| `expect` | `that` + one or more of `equals`, `not_equals`, `gt`, `gte`, `lt`, `lte`, `contains`, `not_contains`, `in`, `exists`, `matches` |

**Recipe references:** `"#2"` (rank in the last list), `"best:t"` / `"worst:h"` (the recipe in the last list
with the highest or lowest score on a criterion, for simulating consistent behaviour), a recipe id, or a title.

**`expect` paths** read from `result` (last list), `chat`, `feedback`, `review`, `resolved`, `what_if`,
`insight`, `state` (fresh user state), `open_decisions`, `error` (error of the previous step), and
`last_step`. Use dots and indexes: `result.results[0].title`, `chat.skills`, `state.goal.version`,
`open_decisions.length`. Any step can set `"times": n`, `"user": "..."`, `"label": "..."`, and
`"allow_error": true` (for steps that should be refused).

Example scenarios are in `scenarios/`: `sara_goal_drift`, `reza_safety_rules`, `study_access_roles` and
`model_comparison`.
