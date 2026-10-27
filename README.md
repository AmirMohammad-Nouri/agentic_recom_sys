# FoodRec: transparent, agentic food recommendation

FoodRec recommends five recipes from the real **HUMMUS** dataset (9,891 food.com recipes),
ranked by three values the user controls: **healthiness, sustainability and convenience**,
plus a fixed share for learned taste. A **supervisor agent**
watches over the learning process: it notices when what you *say* you want and what you
*do* drift apart. It then searches your history in a **knowledge graph** and either asks a
targeted question or proposes a change you can accept. The question or proposal is shown
with the evidence behind it, and nothing changes until you accept.

Like Claude's thinking view, every step the supervisor takes appears **live under your
prompt**: what it loaded, how it understood you, which tools it called with which arguments,
what came back, and why it answered as it did.

> **The agent manages the learning process; it does not pick the food.** The ranking is done
> by a deterministic multi-criteria engine you can audit. The language model only classifies,
> chooses among fixed options and phrases answers, and its phrasing is rejected if it states
> anything the evidence does not support.

> **Data.** Nutrients and the FSA score come from the HUMMUS dataset. Footprints are *estimated*
> from ingredient amounts × food-group CO₂e factors (Poore & Nemecek 2018). Cuisine, meal type
> and food groups are labelled by your local Qwen model (or keyword rules) when the catalogue is
> built. Allergens and diets are detected from ingredient names: verify before relying on them.
> See [The catalogue](#the-catalogue-hummus) and the plain-language
> [user model guide](docs/user-model.md).

---

## Contents

1. [Quick start](#quick-start)
2. [Choosing the supervisor model (4 GB GPU)](#choosing-the-supervisor-model-4-gb-gpu)
3. [How it works](#how-it-works)
4. [The decision system](#the-decision-system)
5. [Core concepts](#core-concepts)
6. [Transparency model](#transparency-model)
7. [Knowledge graph](#knowledge-graph)
8. [Vector database and retrieval](#vector-database-and-retrieval)
9. [The interface](#the-interface)
10. [Configuration and degradation](#configuration-and-degradation)
11. [API](#api)
12. [Testing](#testing)
13. [Project layout](#project-layout)
14. [The catalogue (HUMMUS)](#the-catalogue-hummus)
15. [Limitations](#limitations)

---

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate        # or: uv venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest                                          # 60 tests, no services needed
foodrec                                                   # http://127.0.0.1:8000
```

That already works **without any external service**. The status pill in the top bar shows
`rule-based · local graph`. To enable the full agentic stack:

```bash
# 1. supervisor model + embeddings (https://ollama.com)
ollama pull qwen3.5:2b-q4_K_M
ollama pull nomic-embed-text

# 2. Neo4j knowledge graph (optional; Docker)
cp .env.example .env               # set NEO4J_URI=bolt://localhost:7687 and a password
docker compose up -d neo4j

foodrec
```

The repository ships the built catalogue (`data/hummus/catalogue.json.gz`). On start the app
imports it into its SQLite database (once per catalogue version), so a fresh device only needs
that file. To rebuild it with your local Qwen model, see [The catalogue](#the-catalogue-hummus).

On first open, a five-step onboarding asks for allergies and diets (strict rules), likes and
dislikes, a **health goal** (eat healthier, lose weight, gain weight, build muscle,
heart-friendly, less sugar) with an optional ingredient limit, a few dishes you like (searchable),
and how much each of the three values should count.

---

## Choosing the supervisor model (4 GB GPU)

**Yes, a small Qwen3.5 model works as the supervisor on an RTX 3050 Ti (4 GB),** because the
architecture asks very little of it. (There is no "Qwen 3.5M" model. The Qwen3.5 small series
is 0.8B / 2B / 4B / 9B. Qwen is Alibaba's own model family, not a Llama derivative.)

| Model (Ollama) | Weights | Fits 4 GB with 8k context? | Use it for |
| --- | --- | --- | --- |
| `qwen3.5:0.8b` | ~1 GB | yes, easily | Only if speed matters most; weak at structured extraction |
| **`qwen3.5:2b-q4_K_M`** (default) | ~1.9 GB | **yes**, ~1 GB left for KV cache | **Recommended.** Reliable JSON-schema intent parsing and phrasing |
| `qwen3.5:2b` (default quant) | ~2.7 GB | yes, but tight | Slightly better quality, less headroom |
| `qwen3.5:4b` | ~3.4 GB | only with partial CPU offload | Better reasoning, noticeably slower on 4 GB |

The design keeps the model inside the zone where small models are dependable:

* **Typed choices, not free tool loops.** The model picks an intent from 10 options and fills a
  JSON schema that Ollama enforces (`format`). Every field is validated, and unknown values are
  dropped and reported. A deterministic plan, not the model, decides which skill runs and with
  which arguments.
* **No arithmetic, no ranking, no database queries.** Scores come from the engine, and graph
  access goes through parameterised templates.
* **Validated phrasing.** The model rewrites recorded facts. Any number, recipe name or
  medical claim not in the evidence makes the system fall back to the template.
* **Embeddings on the CPU** (`FOODREC_EMBED_CPU=1`), so the GPU is left entirely to the supervisor.
* **Thinking off by default** (`FOODREC_LLM_THINK=0`) for latency. Switch "Show model reasoning"
  on in the assistant to stream Qwen's own reasoning into the timeline, labelled *unverified*.

Expect roughly 1–4 s per chat turn on that GPU (two short model calls). Recommendations from
the Discover page never call the model.

---

## How it works

```
User interaction ─▶ Event log (SQLite, append-only) ─▶ User model (taste + observed weights)
                                   │                          │
                                   ├─▶ Knowledge graph        ▼
                                   │   (projection)     Supervisor policy ─▶ ask / propose / stay quiet
                                   │                          │
User message ─▶ Supervisor agent: perceive → understand → plan → act (skills) → compose → validate
                                   │                          │
                                   ▼                          ▼
                     Recommendation engine          Hybrid retrieval (BM25 + Qdrant)
           rules → candidates → scores → utility → diversity → evidence records
```

| Layer | File(s) | Responsibility |
| --- | --- | --- |
| Engine | `engine.py`, `constraints.py`, `features.py`, `ranker.py`, `diversity.py`, `preference.py`, `explain.py`, `compare.py`, `router.py`, `telemetry.py` | Deterministic, auditable ranking and evidence |
| User model | `learner.py` | Taste counts and stated-vs-observed weights with provenance, rebuilt by replaying events |
| Ingest | `ingest/` | HUMMUS spreadsheet → portable catalogue file (parsing, local-model labels, footprints) |
| Store | `store.py` | Events, versioned profile and goals, immutable request snapshots, decisions, messages, documents |
| Service | `service.py` | The only writer: log → learner → graph for every change |
| Agent | `agent/` | `supervisor.py` (turns), `policy.py` (monitoring), `skills.py` (tools), `intent.py` (parsing), `trace.py` |
| Knowledge | `knowledge/` | Graph (Neo4j or in-process), documents, hybrid retrieval |
| Model | `llm.py` | Ollama client: schema-constrained chat, streamed thinking, embeddings |
| API + UI | `api.py`, `web/` | FastAPI, NDJSON trace streaming, no-build ES-module front end |

Details: [docs/architecture.md](docs/architecture.md).

---

## The decision system

Who decides what is fixed in code:

| Decision | Made by | Can the model influence it? |
| --- | --- | --- |
| Is a recipe eligible? | Mandatory rules | No |
| How is it scored and ranked? | Engine (weighted utility + MMR diversity) | No |
| What does the user's message ask for? | Model (JSON schema), validated; rules as fallback | Yes, within 10 fixed intents |
| Which skills run, with which arguments? | Deterministic plan from validated slots | No |
| Is one more lookup needed? | Model picks from an allowed list (max 3 skills per turn) | Yes, bounded |
| Is there a gap between goal and behaviour? | Policy: observed − stated, fixed thresholds | No |
| Ask a question or propose a change? | Policy: gap size and number of events | No |
| Change the saved goal or a rule? | **The user only** | No |
| Which words the answer uses | Model, then validated against evidence | Yes, rejected if unsupported |

### Monitoring loop

After every like, save, choice or rejection the supervisor runs:

1. **Compare** the saved goal (stated weights) with the observed weights: gap = observed − stated.
2. **Restraint**: one priority question at a time, and a topic you answered or dismissed is left
   alone for 6 feedback events.
3. **Search history** in the knowledge graph for interactions that name that criterion as the reason.
4. **How big is the gap?**
   * **≥ 0.15 after ≥ 6 events → proposal.** Move the weight halfway toward the observed value, run
     a **counterfactual** ("these recipes would enter, these would leave"), then wait for *Accept*
     or *Keep my goal*.
   * **≥ 0.10 after ≥ 3 events → targeted question.** "When convenience and healthiness pull in
     different directions, which usually wins for you?" Your answer is recorded as evidence and
     does not edit the goal.
   * **smaller → stay quiet** and keep recommending.
5. **Taste conflicts**: a declared liking contradicted by several rejections gives "Do you still
   want Italian as a preference?"

The full specification, with all thresholds, is in [docs/supervisor.md](docs/supervisor.md).

---

## Core concepts

**Three values plus taste.** Every recipe has four scores between 0 and 1 (higher is better),
each a fixed formula you can open on the recipe card (*How it's scored*):

| Criterion | How it is computed |
| --- | --- |
| **Healthiness** | Nine factors per 100 g (energy, fat, saturated fat, sugars, sodium, cholesterol, fibre, protein share of energy, FSA score), each linear between a good and a poor anchor (FSA traffic lights, WCRF, EU claims). Weighted mean; the **health goal** sets the weights (gain weight reverses energy). A–E grade is FoodRec's banding, not Nutri-Score. |
| **Sustainability** | kg CO₂e per kg of dish = Σ grams × food-group factor / grams (water excluded). Log scale: 0.5 → 1, 30 → 0. |
| **Convenience** | Number of ingredients (`ingredients_title`): 3 → 1, 15 → 0, linear. |
| **Taste match** | Learned from likes and rejections (see below). Not a slider. |

**Weighted utility** `U = 0.25 · taste + 0.75 · (w_h·h + w_s·s + w_t·t)`, where the three values
w sum to 1. Reference check: values 0.5/0.3/0.2 and scores 0.8/0.9/0.55/0.85 give **0.78875**.
Taste keeps a fixed share so the three sliders mean exactly what they say, and a dish you would
never eat cannot win on numbers alone.

**Mandatory rules vs. preferences.** Allergies, diets, "never include" ingredients and a
*strict* ingredient limit filter eligibility before ranking. Missing allergen data is never
treated as safe. Rules are never relaxed automatically; an empty result explains which rule
removes the most recipes.

**Request-only filters.** "Something high in protein with at most 8 ingredients" and the meal
selector on Discover become visible filters for that request only. They are never saved.

**Missing values.** The default policy *renormalises* the remaining weights for that recipe and
discloses it. A missing value is never treated as average.

**Diversity.** Greedy maximal marginal relevance. Each card shows when a recipe moved up for
variety, which higher-utility recipe it displaced, and why.

**Computation modes.** *Lightweight* uses cached taste beliefs. *Extended* adds a pass over
your like/save/choose/reject history for taste match. Neither calls a language model.

**User model (simple and replayable).** *Taste*: for each cuisine, ingredient and food group,
taste = (1 + likes) / (2 + likes + dislikes). *Values*: observed weights start at your stated
weights; choosing a dish multiplies each weight by exp(how much better the chosen dish scores on
it than the five shown), reasons and answers add a small push, and saving a goal moves them
halfway to it. **Preference match** = 1 − ½ Σ |stated − observed|. Full walk-through:
[docs/user-model.md](docs/user-model.md).

**Versioning.** Profile and goal saves create new versions; nothing is overwritten. Every
recommendation is stored as an immutable snapshot with its profile, goal, catalogue and scorer
versions. Before/after comparisons are computed only from recorded snapshots.

Definitions and parameters: [docs/features.md](docs/features.md).

---

## Transparency model

| Question | Where it is answered |
| --- | --- |
| **WHY** this recipe? | Card → *How it's scored*: the nine health factors, the footprint by ingredient, the ingredient count, taste concepts |
| **WHAT** drove the rank? | Per-criterion score × weight = contribution, utility rank vs. final rank, diversity effect |
| **WHERE** does a belief come from? | Insights: every update lists the event and how many points it moved; assistant: "what do you know about me?" |
| **UNCERTAINTY** | Confidence n/(n+5) for observed weights; estimated footprints and incomplete allergen data flagged per recipe |
| **WHAT IF** | Live preview while dragging sliders; "what if I cared more about…?" counterfactuals |
| **WHAT CHANGED** | Banner after each change: inputs changed, recipes entered/left/moved, main driver |
| **CONTROL** | Correct any belief, undo/reset settings, restore any goal version, dismiss any proposal |
| **HOW the agent worked** | Live thinking timeline in every answer; the assistant's Supervisor tab; Activity log |

Each step in the timeline has a kind with its own icon (context, reasoning, decision, tool,
model thinking, check, answer), a source chip (policy, rules, model, ranking engine, knowledge
graph, validator…), a duration, and an **evidence** table with the records behind it (raw JSON
one click away). The header summarises the turn: "Thought for 2.2 s · 5 steps".

---

## Knowledge graph

A projection of the catalogue and the event log. It can be rebuilt at any time and runs in
**Neo4j** (`NEO4J_URI`) or in-process with identical query templates.

```
(Recipe)-[:CONTAINS {grams}]->(Ingredient)-[:HAS_ALLERGEN]->(Allergen)
(Recipe)-[:OF_CUISINE]->(Cuisine)   (Ingredient)-[:IN_CATEGORY]->(Category {co2e_kg_per_kg})
(Recipe)-[:SUITS]->(Diet)           (Recipe)-[:SIMILAR_TO {score}]->(Recipe)
(User)-[:PERFORMED]->(Interaction {type, reason, ts, meal, place})-[:ON]->(Recipe)
(User)-[:DECLARED {kind, strict}]->(Allergen|Diet|Ingredient|Cuisine)
(User)-[:SET_GOAL]->(Goal)   (User)-[:RECEIVED]->(Request)-[:RECOMMENDED {rank}]->(Recipe)
(User)-[:SUBJECT_OF]->(Decision)-[:BASED_ON]->(Interaction)
```

The supervisor queries it by **time, person, place, meal, decision, food, ingredient and
cuisine** through fixed templates (`timeline`, `ingredient_profile`, `recipe_context`,
`alternatives`, `concept_evidence`, `decisions`, `recommended`, `neighborhood`). Example:
"what did I reject last week?" → `timeline(user, since=now−7d, types=[reject])`. Explore it in
**Knowledge → Graph**: search any node, click to inspect, double-click to expand.

---

## Vector database and retrieval

**Qdrant** (embedded on disk by default, or a server via `FOODREC_QDRANT=http://…`) stores
embeddings of every recipe, food group, method note (`data/knowledge/*.md`) and user document.
Search is **hybrid**: BM25 and dense cosine, fused with Reciprocal Rank Fusion. Every hit shows
its BM25 rank, vector rank and fused score. Add vetted documents in **Knowledge → Library**;
they are chunked and searchable immediately. Without an embedding model a hashing embedder is
used and labelled *lexical, not semantic*.

---

## The interface

* **Assistant (on every page).** A docked panel on the right, opened from the top bar or the
  *Ask* button, that stays open as you move between pages. *Chat* streams the thinking timeline
  above each answer; *Supervisor* shows open questions and proposals and how each list and each
  review was worked out. Recipe cards have an *Ask* button that sends the question to it.
* **Discover.** Three value sliders (taste's fixed share is shown), a meal selector (breakfast,
  lunch, dinner, snack, dessert, drink), diversity, computation mode, Apply / Save as goal / Undo
  / Reset, and a live preview. The top five show the photo, health grade, ingredient count, kcal,
  CO₂e, three score chips, Like / Save / *I'll cook this* (with reason) / *Not for me* (with
  reason), and *How it's scored* with the full calculation.
* **Insights.** Preference match as a ring, then for each value "You chose" vs. "You do" bars with
  the gap, the evidence that moved them, and taste beliefs with one-click correction.
* **Knowledge.** The graph explorer, plus the library with a retrieval tester.
* **Activity.** The event log and every supervisor decision with its outcome.
* **Profile.** Rules, health goal, ingredient limit, profile versions, and goal versions with *Restore*.

Light and dark themes, keyboard-accessible controls, works down to phone width. Plain ES
modules, no build step; the only external script is the pinned graph renderer (vis-network 9.1.9).

---

## Configuration and degradation

All settings are environment variables (or `.env`; see `.env.example`).

| Variable | Default | Meaning |
| --- | --- | --- |
| `FOODREC_LLM_MODEL` | `qwen3.5:2b-q4_K_M` | Supervisor model in Ollama |
| `FOODREC_LLM`, `OLLAMA_URL` | `1`, `http://127.0.0.1:11434` | Enable/locate Ollama |
| `FOODREC_LLM_CTX`, `FOODREC_LLM_THINK` | `8192`, `0` | Context window; default thinking mode |
| `FOODREC_EMBED_MODEL`, `FOODREC_EMBED_CPU` | `nomic-embed-text`, `1` | Embeddings, on CPU |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | empty | Neo4j; empty uses the in-process graph |
| `FOODREC_QDRANT` | `local` | `local`, `memory`, or a Qdrant URL |
| `FOODREC_DATA_DIR` | `data/runtime` | SQLite (`foodrec_v2.db`), Qdrant files, index metadata |
| `FOODREC_CATALOGUE` | `data/hummus/catalogue.json.gz` | Portable catalogue imported on start |
| `FOODREC_MAX_STEPS` | `3` | Maximum skills per chat turn |

| Missing service | Fallback |
| --- | --- |
| Ollama or the model | Rule-based intent parser, template answers (all traces say `source: rules`) |
| Embedding model | Hashing embeddings |
| Qdrant | numpy index |
| Neo4j | In-process graph, same templates |

Click the status pill for the live state of every component.

---

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/system` | Component status, options, skills, thresholds |
| GET | `/api/recipes?q=&limit=&ids=&meal=` | Search the catalogue |
| GET | `/api/recipes/{rid}/breakdown?goal=` | How the three scores of one recipe are calculated |
| POST | `/api/users/{id}/onboard` | Create profile v1 and goal v1 |
| GET/PUT | `/api/users/{id}/state`, `/profile` | Current state; save a new profile version |
| POST | `/api/users/{id}/goal`, `/goal/revert` | Save or restore a goal version |
| POST | `/api/users/{id}/recommend` | Top five + evidence + comparison + pipeline trace (stored snapshot) |
| POST | `/api/users/{id}/preview` | Counterfactual for unsaved settings (nothing stored) |
| POST | `/api/users/{id}/feedback` | Record feedback; returns the supervisor's review trace |
| GET/POST | `/api/users/{id}/decisions[/{did}]` | List; accept / dismiss / answer |
| POST | `/api/users/{id}/review` | Run the monitoring loop now |
| POST | `/api/users/{id}/chat` | Chat turn, streamed as NDJSON trace steps |
| GET | `/api/users/{id}/insight`, `/events`, `/messages` | Beliefs with provenance; event log; conversation |
| POST | `/api/users/{id}/beliefs` | Correct a taste belief |
| GET | `/api/compare?before=&after=`, `/api/requests/{rid}` | Compare / read snapshots |
| GET | `/api/graph/stats`, `/search`, `/neighborhood` | Knowledge graph |
| GET/POST/DELETE | `/api/knowledge/search`, `/documents` | Retrieval and user documents |

Interactive docs at `/docs`.

---

## Testing

```bash
python -m pytest
```

* `tests/test_reference.py`: 33 deterministic reference cases (utility 0.78875, normalisation,
  missing values, the anchors of each criterion, health goals, breakdown = score, fail-closed
  allergens, strict vs. soft limits, empty-result diagnosis, meal filters, diversity,
  comparisons, mode parity, narrative validation, and the user model: replay, like ratio,
  observed weights, inconsistent reasons, preference match, trade-off answers, goal saves).
* `tests/test_agent.py`: intent parsing, model-output validation, offline and model chat
  turns (with a scripted fake model), streaming, unsupported-number rejection, confirmation of
  profile changes, one-question-at-a-time, cool-down, proposals with counterfactuals, taste
  conflicts, replay after restart, graph templates, hybrid retrieval, and the HTTP API.
  They run on a 148-recipe sample of the catalogue (`data/fixture/catalogue.json.gz`).

The Neo4j backend implements the same templates in Cypher but needs a running Neo4j, so it
is not covered by the automatic tests. Check it with `docker compose up -d neo4j` and the app.

---

## Project layout

```
foodrec/
  api.py  service.py  store.py  config.py  llm.py          application, persistence, model client
  engine.py constraints.py features.py ranker.py diversity.py preference.py
  explain.py compare.py router.py telemetry.py learner.py catalogue.py   recommendation core
  ingest/     build.py parse.py classify.py foodgroups.py               HUMMUS → catalogue file
  agent/      supervisor.py policy.py skills.py intent.py trace.py
  knowledge/  graph.py neo4j_graph.py retrieval.py documents.py
  web/        index.html app.css app.js lib.js dock.js trace.js components.js views/*.js
data/
  hummus/     hummus_recipes.xlsx, catalogue.json.gz, labels_*.json (label caches)
  fixture/    148-recipe sample used by the tests
  knowledge/  method notes indexed for retrieval
docs/         user-model.md architecture.md supervisor.md features.md background/
tests/
```

---

## The catalogue (HUMMUS)

`python -m foodrec.ingest` turns `data/hummus/hummus_recipes.xlsx` into one portable file,
`data/hummus/catalogue.json.gz` (≈6 MB): `{meta, groups, recipes}`. Building needs
`pip install -e ".[ingest]"` (pandas, openpyxl); running the app does not.

```bash
python -m foodrec.ingest                       # label with your local Qwen via Ollama (FOODREC_LLM_MODEL)
python -m foodrec.ingest --model qwen3.5:4b    # another local model
python -m foodrec.ingest --no-llm              # keyword rules only, no model needed (~30 s)
python -m foodrec.ingest --fixture 150         # small sample for the tests
```

What the build does, per recipe:

1. **Parse** each ingredient line (amount, unit, pack size such as "(8 ounce) can") into grams,
   drop notes and duplicates, and count ingredients from `ingredients_title`.
2. **Label** with the local model, in batches with a JSON schema that only allows fixed options:
   each unique ingredient name → one of ~45 food groups (beef, poultry, cheese, pulses, …), and
   each recipe → cuisine and meal type (or *non_food*, which drops household and pet "recipes").
   Every answer is validated; anything invalid falls back to the keyword rules. The build reports
   how often the model and the rules agree.
3. **Compute** the footprint (grams × food-group factor, with a per-ingredient breakdown),
   allergens and diet flags (fail closed: unknown stays unknown), and the three nearest
   neighbours for the knowledge graph.

Labels are cached in `data/hummus/labels.json` after every batch, so a run can be stopped and
resumed, and **copying the cache (or just the finished `.json.gz`) to another device skips the
model there**. `labels_rules.json` is the cache of the rules-only build.

**On start** the app reads `FOODREC_CATALOGUE` and imports it into the `recipes` and
`catalogue_meta` tables of its SQLite database; if that version is already stored the import is
skipped. The app then always loads recipes from the database, so a device with the database but
without the file still runs. The new database file is `foodrec_v2.db`; an older `foodrec.db`
is left untouched.

## Limitations

* Footprints are estimates from food-group averages and parsed amounts, not a life-cycle
  assessment. Allergens and diets are detected from ingredient names and can be wrong.
* Healthiness is an operational nutrient measure for ranking, not medical or dietetic advice.
* Taste match and observed weights are model estimates, not observed ratings. The learning rate
  and policy thresholds are design choices that still need calibration with users.
* The supervisor's model phrasing is validated for numbers, recipe names and medical claims, not
  for every possible misleading statement. The template answer is always available as ground truth.
* Single-process server and SQLite: suitable for a demo and user studies, not for multi-user production.
* Energy telemetry exists only where the machine exposes a RAPL counter.
