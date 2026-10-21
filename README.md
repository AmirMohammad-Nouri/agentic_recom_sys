# FoodRec: transparent, agentic food recommendation

FoodRec recommends five recipes ranked by priorities the user controls. A **supervisor agent**
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

> **Data warning.** `data/fixture/` is a SYNTHETIC catalogue of 54 recipes. Nutrient values are
> rounded approximations. Prices and CO₂e values are placeholders (`provenance:
> fixture_placeholder`). Do not cite results from it; see [Using real data](#using-real-data).

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
14. [Using real data](#using-real-data)
15. [Limitations](#limitations)

---

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate        # or: uv venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest                                          # 52 tests, no services needed
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

On first open, a five-step onboarding asks for allergies and diets (strict rules), likes and
dislikes, time and budget (each strict *or* a preference), a few dishes you like, and starting
priorities.

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
  on in the Assistant to stream Qwen's own reasoning into the trace, labelled *unverified*.

Expect roughly 1–4 s per chat turn on that GPU (two short model calls). Recommendations from
the Discover page never call the model.

---

## How it works

```
User interaction ─▶ Event log (SQLite, append-only) ─▶ Learner (taste + revealed trade-offs)
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
| Learner | `learner.py` | Bayesian beliefs with provenance, rebuilt by replaying events |
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
| Is there a conflict between goal and behaviour? | Policy: posterior probabilities and thresholds | No |
| Ask a question or propose a change? | Policy: evidence strength rule | No |
| Change the saved goal or a rule? | **The user only** | No |
| Which words the answer uses | Model, then validated against evidence | Yes, rejected if unsupported |

### Monitoring loop

After every like, save, choice or rejection the supervisor runs:

1. **Compare** the saved goal with the revealed weights. Conflict if P(|θ_k − w_k| > 0.08) ≥ 0.70.
2. **Restraint**: one priority question at a time, and a topic you answered or dismissed is left
   alone for 6 feedback events.
3. **Search history** in the knowledge graph for interactions that name that criterion as the reason.
4. **Enough evidence?** P ≥ 0.90 *and* ≥ 4 informative events since your last explicit statement:
   * **yes → proposal.** Move the weight halfway toward the revealed value, run a **counterfactual**
     ("these recipes would enter, these would leave"), then wait for *Accept* or *Keep my goal*.
   * **no → targeted question.** "When affordability and convenience pull in different directions,
     which usually wins for you?" Your answer is recorded as evidence and does not edit the goal.
5. **Taste conflicts**: a declared liking contradicted by several rejections gives "Do you still
   want Italian as a preference?"

The full specification, with all thresholds, is in [docs/supervisor.md](docs/supervisor.md).

---

## Core concepts

**Five criteria**, each scored 0–1 with higher better and scaled once on the frozen catalogue:
preference match (a model estimate, not a rating), healthiness (Nutri-Score 2015 points, an
operational measure, not clinical), sustainability (kg CO₂e per serving), affordability
(EUR per serving), convenience (minutes).

**Weighted utility** `U = Σ w_k · score_k` with weights normalised to sum to 1. Reference check:
0.30/0.25/0.20/0.15/0.10 × 0.80/0.90/0.55/0.70/0.85 = **0.765**. If all weights are zero, the
last valid configuration is kept and the user is told how to restore one.

**Mandatory rules vs. preferences.** Allergies, diets, "never include" ingredients and *strict*
limits filter eligibility before ranking. Missing allergen data is never treated as safe. Rules
are never relaxed automatically; an empty result explains which rule removes the most recipes.
A preference for quick meals stays a preference.

**Request-only filters.** "Something high in protein under 20 minutes" becomes visible filters
for that request only. They are never saved and never change your weights.

**Missing values.** The default policy *renormalises* the remaining weights for that recipe and
discloses it. A missing value is never treated as average.

**Diversity.** Greedy maximal marginal relevance. Each card shows when a recipe moved up for
variety, which higher-utility recipe it displaced, and why.

**Computation modes.** *Lightweight* uses cached taste beliefs. *Extended* adds a pass over
your like/save/choose/reject history for preference match. Both use the same rules, candidates
and weights; neither calls a language model; neither guarantees better results.

**Two preference models.** A Beta *taste model* per cuisine, ingredient and category, plus a
Gaussian *choice model* of how you actually trade the five criteria off, updated from slate
choices, reasons, criterion-specific rejections, goal statements and trade-off answers. Both
are pure functions of the event log and keep provenance (which event moved which belief).

**Versioning.** Profile and goal saves create new versions; nothing is overwritten. Every
recommendation is stored as an immutable snapshot with its profile, goal, catalogue and scaler
versions. Before/after comparisons are computed only from recorded snapshots.

Definitions and parameters: [docs/features.md](docs/features.md).

---

## Transparency model

| Question | Where it is answered |
| --- | --- |
| **WHY** this recipe? | Card → *Evidence*: rules passed, taste concepts with beliefs, soft-target fits |
| **WHAT** drove the rank? | Per-criterion score × weight = contribution, utility rank vs. final rank, diversity effect |
| **WHERE** does a belief come from? | Insights: every belief lists the events that moved it; Assistant: "why do you think…?" |
| **UNCERTAINTY** | 90 % intervals and confidence per criterion; placeholder and missing data flagged per recipe |
| **WHAT IF** | Live preview while dragging sliders; "what if I cared more about…?" counterfactuals |
| **WHAT CHANGED** | Banner after each change: inputs changed, recipes entered/left/moved, main driver |
| **CONTROL** | Correct any belief, undo/reset settings, restore any goal version, dismiss any proposal |
| **HOW the agent worked** | Live trace under every answer; Supervisor column on Discover; Activity log |

Each trace step has a kind (`perceive`, `reason`, `decide`, `tool`, `model`, `check`,
`answer`), a source (`policy`, `rules`, `model`, `engine`, `graph`, `validator`…), a duration
and an expandable **evidence** payload with the raw records behind it.

---

## Knowledge graph

A projection of the catalogue and the event log. It can be rebuilt at any time and runs in
**Neo4j** (`NEO4J_URI`) or in-process with identical query templates.

```
(Recipe)-[:CONTAINS {grams}]->(Ingredient)-[:HAS_ALLERGEN]->(Allergen)
(Recipe)-[:OF_CUISINE]->(Cuisine)   (Ingredient)-[:IN_CATEGORY]->(Category)
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
embeddings of every recipe, ingredient, method note (`data/knowledge/*.md`) and user document.
Search is **hybrid**: BM25 and dense cosine, fused with Reciprocal Rank Fusion. Every hit shows
its BM25 rank, vector rank and fused score. Add vetted documents in **Knowledge → Library**;
they are chunked and searchable immediately. Without an embedding model a hashing embedder is
used and labelled *lexical, not semantic*.

---

## The interface

* **Discover.** Priority sliders with visible normalised weights, a diversity control, a computation
  mode switch, the meal/place context recorded with feedback, Apply / Save as goal / Undo / Reset,
  and a live preview of what a change *would* do. Below that, the top five, each with a contribution
  bar, key reason, compromise, Like / Save / *I'll cook this* (with reason) / *Not for me* (with
  reason), and an *Evidence* drawer with a full score table and recipe-to-recipe comparison. The
  **Supervisor** column shows how each list was computed, what the supervisor concluded after each
  piece of feedback, and open questions and proposals.
* **Assistant.** Chat with the supervisor. Its work streams live above each answer, with a cursor
  on the running step, then collapses to "Worked for 1.4 s · 8 steps". Results, comparisons,
  counterfactuals and decisions arrive as interactive cards.
* **Insights.** Saved vs. revealed priorities with uncertainty bands, recent evidence, and taste
  beliefs with provenance and one-click correction.
* **Knowledge.** The graph explorer, plus the library with a retrieval tester.
* **Activity.** The event log and every supervisor decision with its outcome.
* **Profile.** Rules and preferences, profile versions, and goal versions with *Restore*.

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
| `FOODREC_DATA_DIR` | `data/runtime` | SQLite, Qdrant files, index metadata |
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

* `tests/test_reference.py`: 25 deterministic reference cases (score arithmetic incl. 0.765,
  normalisation, all-zero weights, missing-value policies, fail-closed allergens and diets,
  strict vs. soft limits, empty-result diagnosis, duplicates, diversity, comparisons, mode
  parity, narrative validation, learner replay, goal immutability, trade-off evidence).
* `tests/test_agent.py`: intent parsing, model-output validation, offline and model chat
  turns (with a scripted fake model), streaming, unsupported-number rejection, confirmation of
  profile changes, one-question-at-a-time, cool-down, proposals with counterfactuals, taste
  conflicts, replay after restart, graph templates, hybrid retrieval, and the HTTP API.

The Neo4j backend implements the same templates in Cypher but needs a running Neo4j, so it
is not covered by the automatic tests. Check it with `docker compose up -d neo4j` and the app.

---

## Project layout

```
foodrec/
  api.py  service.py  store.py  config.py  llm.py          application, persistence, model client
  engine.py constraints.py features.py ranker.py diversity.py preference.py
  explain.py compare.py router.py telemetry.py learner.py catalogue.py   recommendation core
  agent/      supervisor.py policy.py skills.py intent.py trace.py
  knowledge/  graph.py neo4j_graph.py retrieval.py documents.py
  web/        index.html app.css app.js lib.js trace.js components.js views/*.js
data/
  fixture/    synthetic catalogue (build_fixture.py regenerates it)
  knowledge/  method notes indexed for retrieval
docs/         architecture.md supervisor.md features.md background/ (brief and ideas)
tests/
```

---

## Using real data

The loader expects the record shape in `data/fixture/recipes.json`: `id`, `title`, `cuisine`,
`meal`, `ingredients[{name, grams, category, allergens}]`, `diet{…}`, `allergens`,
`allergens_complete`, `nutrition_per_100g{energy_kj, sugars_g, sat_fat_g, sodium_mg, fibre_g,
protein_g, fruit_veg_nuts_pct}`, `serving_grams`, `kcal_per_serving`, `prep_minutes`,
`cost_eur_per_serving` + provenance, `co2e_kg_per_serving` + provenance, `missing`. Point
`FOODREC_CATALOGUE` and `FOODREC_INGREDIENTS` at your files. Ingredient composition can come
from a food composition database such as USDA FoodData Central; footprints need a sourced
dataset (never generate environmental values with a model). Candidate catalogues are listed
in the brief (FoodNexus, XFoodRec, WiseFood). Check coverage and licences first.

---

## Limitations

* The catalogue is synthetic; nothing here is a finding.
* Healthiness is an operational Nutri-Score measure applied to dishes, not a clinical assessment;
  the 2023 update is not implemented.
* Preference match and revealed weights are model estimates, not observed ratings or calibrated
  probabilities. Policy thresholds are design choices that still need calibration.
* The supervisor's model phrasing is validated for numbers, recipe names and medical claims, not
  for every possible misleading statement. The template answer is always available as ground truth.
* Single-process server and SQLite: suitable for a demo and user studies, not for multi-user production.
* Energy telemetry exists only where the machine exposes a RAPL counter.
