# FoodRec: transparent, agentic food recommendation

FoodRec recommends recipes from the **HUMMUS** dataset (about 9,600 food.com recipes), ranked by
values each user controls (by default **healthiness, sustainability and convenience**) plus a learned
share for taste. A **supervisor agent** watches the learning process: when what a user *says* they want
and what they *do* drift apart, it looks up their history in a **knowledge graph** and asks a question or
proposes a change, always with the evidence. Nothing about a user's goal changes until they accept.

FoodRec is built for **research on transparent recommendation**: every score can be opened, every step
of the agent is shown live, and a study administrator decides who sees what, creates participant
accounts, and exports one standard record per user.

> **The agent manages the learning process; it does not pick the food.** Ranking is done by a
> deterministic multi-criteria engine you can audit. The language model only classifies messages,
> chooses among fixed options and phrases answers, and its wording is rejected if it states anything
> the evidence does not support.

**Highlights**

* Multi-criteria ranking with mandatory rules (allergies, diets), missing-data policy, diversity, and
  lists that stay fresh over days.
* A supervisor agent with a live, inspectable trace; works with a local model (Ollama), any
  OpenAI-compatible API or the Claude API, and without any model at all.
* Everything configurable in one file or on the **Config page**: model, dataset, the criteria users weigh,
  feedback options, access roles, and the study population.
* Sign-in for everyone, generated participant accounts, a standard **export** for analysis, and scripted
  **scenarios** for reproducible tests.

## Author

**AmirMohammad Nouri** · [GitHub](https://github.com/AmirMohammad-Nouri)

## The complete guide

**[FoodRec: how it works, how it learns, and the math](https://amirmohammad-nouri.github.io/agentic_recom_sys/guide/)**
explains the system in depth, with diagrams and a live simulator:

1. **How FoodRec works**: the components, from HUMMUS to the database, how five recipes are chosen, what one
   click and one chat turn set off, and why nothing is lost.
2. **The user and the user model**: onboarding, a session screen by screen, how taste and values are
   learned, what every interaction changes, and when the supervisor speaks up.
3. **Every equation, explained**: notation, criterion scores, ranking, the taste and value models, the
   supervisor policy, which equation each event runs, and every parameter with the reason for its value.

Read it online (link above), in the app (account menu → **How FoodRec works**, or
`http://127.0.0.1:8000/guide`), or open [`docs/guide/index.html`](docs/guide/index.html) in a browser.
This README covers installation and use; the guide covers the concepts and the mathematics.

---

## Contents

1. [Requirements](#requirements)
2. [Installation: step by step](#installation-step-by-step)
3. [A tour of the app](#a-tour-of-the-app)
4. [Optional: language model and services](#optional-language-model-and-services)
5. [Running a study](#running-a-study)
6. [Configuration](#configuration)
7. [Command reference](#command-reference)
8. [Using your own dataset](#using-your-own-dataset)
9. [Demo and server deployment](#demo-and-server-deployment)
10. [Troubleshooting](#troubleshooting)
11. [How it works](#how-it-works)
12. [API](#api)
13. [Testing](#testing)
14. [Project layout](#project-layout)
15. [The catalogue (HUMMUS)](#the-catalogue-hummus)
16. [Limitations](#limitations)

---

## Requirements

| | |
|---|---|
| Operating system | Linux or macOS. On Windows, use [WSL](https://learn.microsoft.com/windows/wsl/) (Ubuntu). |
| Python | 3.11 or newer (`python3 --version`) |
| Git | to clone the repository |
| Disk / memory | about 1 GB disk; 2 GB RAM without a language model, 6 GB or more with a local one |
| Optional | [Ollama](https://ollama.com) for the local language model and semantic search; Docker for Neo4j |

No external service is needed to start: without a model the assistant uses rules and templates, and the
knowledge graph and vector index run inside the app.

---

## Installation: step by step

### 1. Get the code

```bash
git clone https://github.com/AmirMohammad-Nouri/agentic_recom_sys.git
cd agentic_recom_sys
```

### 2. Create a virtual environment and install

```bash
python3 -m venv .venv
source .venv/bin/activate          # every new terminal: run this again before using foodrec
pip install -e ".[dev]"            # the app, its command line `foodrec`, and the test tools
```

Check it: `foodrec --help` lists the commands. Optionally run the tests: `python -m pytest`
(129 tests, about 15 seconds, no services needed).

### 3. Set the administrator password (required)

> [!IMPORTANT]
> FoodRec asks **everyone** to sign in. The repository ships one administrator account, `admin`,
> **without a password**, and the app refuses to start until you set one:
>
> ```bash
> foodrec admin password        # asks twice; stores only a hash in foodrec.config.json
> ```
>
> Alternatively, write it once into `foodrec.config.json` and the app replaces it with a hash when it
> starts:
>
> ```json
> "access": { "admin": { "username": "admin", "password": "choose-a-good-password" } }
> ```
>
> The administrator is the only account that sees **Config**. Run `foodrec admin password` again at any
> time to change the password (this signs the administrator out everywhere).

### 4. Start FoodRec

```bash
foodrec
```

The first start imports the recipe catalogue into a local database (a few seconds), then opens
`http://127.0.0.1:8000` in your browser. FoodRec keeps running in this terminal; stop it with `Ctrl+C`,
or with `foodrec stop` from another terminal. Running `foodrec` again while it runs just opens the browser.

### 5. Sign in and set up your profile

1. Sign in as `admin` with the password from step 3.
2. A five-step **onboarding** asks for allergies and diets (strict rules), likes and dislikes, a health
   goal, a few dishes you like, and how much each value should count.
3. You land on **Discover** with your first recommendations.

### 6. Open the Config page

Click the account button (top right) → **Config**, or run `foodrec config open` in a terminal. Every
setting has a **Guide** button explaining what it does and how it changes the results.

That is a complete installation. The next sections are optional.

---

## A tour of the app

| Where | What you can do |
|---|---|
| **Discover** | Five recipes ranked for you. Move the value sliders and **Apply** to try them on this list, or **Save as goal** to keep them as a new goal version. Choose a meal, react to dishes (Like, Save, *I'll cook this*, *Not for me* with a reason), and open *How it's scored* on any card. |
| **Assistant** (right side, every page) | Ask in plain language: "something easy with lentils", "why is #2 first?", "compare #1 and #3", "what if I cared more about sustainability?". Every step the agent takes is shown live above the answer. The *Supervisor* tab shows its questions and proposals. |
| **Account menu** (top right) | **My dishes**: saved for later, liked and cooked dishes. **Config** (administrator only). **How FoodRec works** (the complete guide). Sign out. |
| **Insights** | What your choices reveal compared with your saved goal, with the evidence behind every change; taste beliefs you can correct. |
| **Knowledge** | The knowledge graph explorer, your document library and a search tester. |
| **Activity** | The full event log and every supervisor decision. |
| **Profile** | Rules and preferences, profile versions, and goal versions you can restore. |

Saved dishes wait in My dishes instead of taking a place in Discover. Dishes you are shown but ignore,
and cuisines that keep appearing, slowly give way so the lists stay fresh over days.

---

## Optional: language model and services

### A local language model (Ollama)

```bash
# install Ollama from https://ollama.com, then:
ollama pull qwen3.5:2b-q4_K_M      # the supervisor model (about 2 GB)
ollama pull nomic-embed-text       # embeddings for semantic search
```

Restart FoodRec (`foodrec stop`, then `foodrec`). The status pill in the top bar shows the model once it
is available. The default model fits a 4 GB GPU:

| Model (Ollama) | Size | Fits 4 GB with 8k context | Use it for |
|---|---|---|---|
| `qwen3.5:0.8b` | ~1 GB | yes | fastest; weaker at structured extraction |
| **`qwen3.5:2b-q4_K_M`** (default) | ~1.9 GB | yes | **recommended**: reliable intent parsing and phrasing |
| `qwen3.5:4b` | ~3.4 GB | with partial CPU offload | better reasoning, slower on 4 GB |

Expect 1 to 4 seconds per chat turn. Discover never calls the model.

### A hosted model (OpenAI-compatible or Claude)

On the Config page → Settings → Language model, choose the provider (`openai` for any OpenAI-compatible
API such as OpenAI, Groq, OpenRouter, vLLM or LM Studio; `anthropic` for Claude), the model, and for
OpenAI-compatible servers the base URL. Paste the API key into the field below it: it is written to `.env`
(ignored by git), never to the config file. Claude needs `pip install -e ".[anthropic]"`.

### Neo4j and Qdrant servers

The built-in graph and vector index are enough. To use the servers instead:

```bash
cp .env.example .env               # set NEO4J_URI=bolt://localhost:7687 and NEO4J_PASSWORD
docker compose up -d neo4j         # knowledge graph (browser at http://localhost:7474)
docker compose --profile qdrant up -d   # optional Qdrant server; then set the vector store to its URL
```

| Missing component | What FoodRec does instead |
|---|---|
| Ollama or the model | rule-based message parsing and template answers (traces say `source: rules`) |
| embedding model | lexical (hashing) embeddings |
| Qdrant | an in-memory index |
| Neo4j | an in-process graph with the same queries |

Click the status pill for the live state of every component.

---

## Running a study

1. **Decide the conditions** (Config → **Access roles**). A role switches features on or off: AI
   explanations, score breakdowns, the assistant, the supervisor, food details, which criteria are shown,
   and more. The server enforces them, not only the interface. Shipped roles: `full`, `no_explanations`,
   `baseline` and `admin`.
2. **Create the participants** (Config → **Users & study → Create the study population**). Enter the
   number of users and the percentage per role (for example 40 users, 50 % `full`, 50 % `baseline`) and
   click **Generate participants**. Each account gets a username (`p001` …) and a readable password;
   both can be edited. **Save to file**, then **Download usernames and passwords (CSV)** to hand them out.
   Command line: `foodrec study generate --size 40 --role full=50 --role baseline=50 --write`.
3. **Participants sign in** with their account, complete the onboarding, and see the app as their role
   allows. They never see Config and can only use their own account.
4. **Export the data** (Config → **Export**, or `foodrec export --out study.zip`). Each user's record holds
   their role and what they could see, preferences and their history, the scores of every list shown
   (all and visible), every interaction, supervisor decisions, the conversation with the agent's steps,
   interface events and a summary. The ZIP adds flat CSV tables. Passwords are never exported.
5. **Test before you run it** with scripted scenarios (`scenarios/*.json`):
   ```bash
   foodrec scenario run scenarios/study_access_roles.json
   foodrec scenario run scenarios/model_comparison.json --matrix llm.model=qwen3.5:2b-q4_K_M,qwen3:4b
   ```
   Each run starts from an empty database, writes a report and an export to `out/scenarios/`, and fails
   if an expectation does not hold.

> [!WARNING]
> Participant passwords are stored readable in `foodrec.config.json`, because you hand them out. Do not
> commit that file once it holds real accounts (`git status` shows it as modified; leave it out of
> `git add`).

---

## Configuration

Everything lives in **`foodrec.config.json`**, and the Config page edits the same file. Anything you can
change on the page can be written in the file, and the other way round. The running app watches the file:
settings marked *live* apply at once, the others restart the app automatically, and unchanged content
does nothing.

| Section | What it controls |
|---|---|
| `llm` | provider (Ollama, OpenAI-compatible, Claude), model, embeddings |
| `dataset` | recipe catalogue, method notes, data folder |
| `services` | Neo4j and the vector store |
| `server` | mode (`local`, `demo`, `open`), host, port, automatic reload |
| `ranking` | list size, missing-data policy, fresh lists, "New for you" places |
| `supervisor` | how many tools one chat turn may use |
| `criteria` | the values users weigh: switch built-ins off, add criteria from any numeric recipe field (energy, protein, price …) |
| `interactions` | feedback reasons and how strongly each one counts |
| `access` | roles and what they see; the administrator account |
| `study` | name, population, participant accounts |

Precedence: built-in default < environment / `.env` < config file. `null` in the file means "use the
environment or the default". Secrets (API keys, `NEO4J_PASSWORD`, `FOODREC_ADMIN_TOKEN`) stay in the
environment and are never written to the file. Full reference:
[docs/configuration.md](docs/configuration.md).

| Server mode | Who | Config page |
|---|---|---|
| `local` (default) | everyone signs in: the administrator account or participant accounts | administrator only |
| `demo` | public showcase: no sign-in; visitors switch between a User and an Admin view | admin view, read-only |
| `open` | no sign-in; for development and automated tests only | roles with *configure* |

---

## Command reference

| Command | What it does |
|---|---|
| `foodrec` | open the app in the browser; start it if it is not running |
| `foodrec config open` | open the Config page (signed in as administrator); start the app if needed |
| `foodrec serve` | run only the server, without a browser |
| `foodrec status` / `foodrec stop` | is it running? stop it, from any terminal |
| `foodrec admin password` | set or change the administrator password |
| `foodrec study generate --size N --role R=P … [--write]` | create participant accounts |
| `foodrec study list` | participants with role and password |
| `foodrec users` | users in the database and their roles |
| `foodrec export --out study.zip [--users p001,p002]` | the standard export (JSON or ZIP) |
| `foodrec scenario run FILE… [--set k=v] [--matrix k=v1,v2]` | run scripted scenarios |
| `foodrec config show` / `check` / `init` | show where every setting comes from; validate; write a fresh file |
| `foodrec --config other.json …` | use another config file |

`open`, `serve`, `status`, `stop` and `config open` accept `--host` and `--port`. Only one FoodRec runs per port: if it already runs,
`foodrec` and `foodrec config open` just open the browser.

---

## Using your own dataset

1. Write your recipes in the simple JSON format (template: `data/examples/my_recipes.json`, or download it
   from Config → Settings → Dataset). Required per recipe: a title, ingredients, and energy per 100 g.
   Numbers in `"extra"` (price, preparation time …) can become criteria.
2. Build the catalogue:
   ```bash
   python -m foodrec.ingest --recipes my_recipes.json --name my_study --out data/my_study/catalogue.json.gz
   ```
3. On the Config page (Settings → Dataset), set the catalogue file to that path and the data folder to a
   new folder such as `data/my_study/runtime`, and save. The app restarts with your recipes.

Food groups, CO₂e footprints, allergens, diets, cuisine and meal type are derived automatically. Step by
step: the Dataset guide on the Config page, or [docs/configuration.md](docs/configuration.md#your-own-private-dataset).

---

## Demo and server deployment

The same code runs a public showcase: `server.mode = "demo"` with `deploy/demo/demo.config.json`. Visitors
need no account; the account menu switches between **User view** and **Admin view**, the Config page is
read-only, visitors cannot see each other's data, and the data is reset every night.

Try it on your computer next to your normal app:

```bash
foodrec --config deploy/demo/demo.config.json open --port 8001
foodrec --config deploy/demo/demo.config.json stop --port 8001     # when done
```

[docs/deployment.md](docs/deployment.md) describes the server setup step by step (an Ubuntu VM, for
example on CSC cPouta): systemd service, nightly reset, HTTPS with Caddy, and updates with release tags.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `FoodRec needs an administrator password before it starts` | Run `foodrec admin password` (see step 3). |
| Forgot the administrator password | Run `foodrec admin password` again. |
| `Port 8000 … is used by another program` | Stop that program, or use another port: `foodrec open --port 8001`. |
| The page loads but parts are empty after an update | An old server is still running: `foodrec stop`, then `foodrec`. |
| `foodrec: command not found` | Activate the environment: `source .venv/bin/activate`. |
| The status pill says `rule-based` | Ollama is not running or the model is not pulled (`ollama pull qwen3.5:2b-q4_K_M`). |
| Start over with empty data | `foodrec stop`, then delete the data folder (default `data/runtime`). Users and history are lost; the config is kept. |
| A setting has no effect | `foodrec config show` lists every value and whether it comes from the file, the environment or the default. |

---

## How it works

```
User interaction ─▶ Event log (SQLite, append-only) ─▶ User model (taste + revealed weights)
                                   │                          │
                                   ├─▶ Knowledge graph        ▼
                                   │   (projection)     Supervisor policy ─▶ ask / propose / stay quiet
                                   │                          │
User message ─▶ Supervisor agent: perceive → understand → plan → act (skills) → compose → validate
                                   │                          │
                                   ▼                          ▼
                     Recommendation engine          Hybrid retrieval (BM25 + vectors)
           rules → candidates → scores → utility → diversity → evidence records
```

| Layer | File(s) | Responsibility |
|---|---|---|
| Engine | `engine.py`, `constraints.py`, `features.py`, `ranker.py`, `diversity.py`, `preference.py`, `explain.py`, `compare.py` | deterministic, auditable ranking and evidence |
| User model | `learner.py` | taste counts and stated-vs-revealed weights, rebuilt by replaying events |
| Store | `store.py` | events, versioned profiles and goals, immutable list snapshots, decisions, messages |
| Service | `service.py` | the only writer: event log → user model → knowledge graph |
| Agent | `agent/` | turns, monitoring policy, skills, message parsing, trace |
| Knowledge | `knowledge/` | graph (Neo4j or in-process), documents, hybrid retrieval |
| Models | `llm.py`, `llm_openai.py`, `llm_anthropic.py` | Ollama, OpenAI-compatible and Claude clients |
| Config and access | `appconfig.py`, `access.py`, `model_spec.py`, `study.py`, `reload.py` | config file, roles, criteria, accounts, reloading |
| API and UI | `api.py`, `cli.py`, `web/` | FastAPI with streamed traces; a no-build ES-module front end |

### In short

* **Rules first.** Allergies, diets and "never include" ingredients filter recipes before anything is
  scored; missing allergen data is never treated as safe, and rules are never relaxed automatically.
* **Scores you can open.** Each criterion is a fixed formula from 0 to 1 (healthiness from nine nutrient
  factors weighted by the health goal, sustainability from estimated CO₂e, convenience from the number
  of ingredients, plus any criteria you configure).
* **Utility.** `utility = taste_share · taste + (1 − taste_share) · Σ weight_k · score_k`; diversity,
  freshness and the missing-data policy are applied and disclosed per recipe.
* **Learning.** Taste is counted from likes and dislikes; the weights a user's choices reveal are tracked
  next to the saved goal, which only the user changes.
* **The supervisor** asks a question when the gap between the two reaches 0.10 after 3 events and proposes
  a change at 0.15 after 6 events, one topic at a time and always with the evidence.
* **The model** classifies messages and phrases answers; it never ranks, and its wording is checked
  against the evidence.

Every formula, threshold and parameter, with the reason for each value, is in the
[complete guide](#the-complete-guide). Specifications: [docs/supervisor.md](docs/supervisor.md),
[docs/user-model.md](docs/user-model.md), [docs/features.md](docs/features.md),
[docs/architecture.md](docs/architecture.md).

---

## API

Interactive documentation at `http://127.0.0.1:8000/docs`. Main endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/system` | component status, options, criteria, mode |
| POST | `/api/login` | sign in (username, password) → session token |
| GET | `/api/recipes?q=&limit=&ids=&meal=` | search the catalogue |
| POST | `/api/users/{id}/onboard` | create profile v1 and goal v1 |
| GET / PUT | `/api/users/{id}/state`, `/profile` | current state; save a profile version |
| POST | `/api/users/{id}/goal`, `/goal/revert` | save or restore a goal version |
| POST | `/api/users/{id}/recommend` | a ranked list with evidence (stored snapshot) |
| POST | `/api/users/{id}/feedback` | record a reaction; returns the supervisor's review |
| GET / POST | `/api/users/{id}/dishes`, `/dishes/remove` | My dishes |
| GET / POST | `/api/users/{id}/decisions[/{did}]` | supervisor questions and proposals; answer them |
| POST | `/api/users/{id}/chat` | a chat turn, streamed as trace steps (NDJSON) |
| GET | `/api/users/{id}/insight`, `/events`, `/messages`, `/export` | beliefs, event log, conversation, own export |
| GET / PUT | `/api/config` | the configuration (administrator) |
| GET | `/api/export?users=&format=json\|zip` | the study export (administrator) |

---

## Testing

```bash
python -m pytest          # 129 tests, no services needed
```

The tests run on a 148-recipe sample (`data/fixture/catalogue.json.gz`) and cover the reference
calculations, the agent (with a scripted model), learning and policy regressions, configuration and
access roles, criteria and providers, sign-in and modes, fresh lists and My dishes. The Neo4j backend
needs a running Neo4j and is not part of the automatic tests.

---

## Project layout

```
foodrec/                    the application
  api.py cli.py             web API and command line
  service.py store.py       state changes and persistence (SQLite)
  engine.py features.py ranker.py diversity.py constraints.py explain.py   recommendation engine
  learner.py                user model
  agent/                    supervisor, policy, skills, message parsing, trace
  knowledge/                knowledge graph and retrieval
  llm*.py                   language-model clients
  appconfig.py access.py model_spec.py study.py reload.py   configuration, roles, accounts, reloading
  export.py scenario.py     study export and scenario runner
  ingest/                   build a catalogue from HUMMUS or your own recipes
  web/                      the web interface; web/guides/ holds the Config page guides
foodrec.config.json         the configuration (Config page)
scenarios/                  scripted scenarios
deploy/demo/                public demo: config, systemd units, Caddyfile
data/
  hummus/                   the shipped catalogue (catalogue.json.gz) and label cache
  fixture/                  the test sample
  knowledge/                method notes the assistant cites
  examples/                 template for your own recipes
docs/                       configuration, deployment, architecture, supervisor, user model, features
  guide/index.html          the complete guide (concepts and every equation)
tests/
```

---

## The catalogue (HUMMUS)

The repository ships the built catalogue, `data/hummus/catalogue.json.gz`. On the first start the app
imports it into its database (once per catalogue version), so nothing else is needed. To rebuild it from
the HUMMUS spreadsheet (`data/hummus/hummus_recipes.xlsx`, not in the repository) install
`pip install -e ".[ingest]"` and run:

```bash
python -m foodrec.ingest                      # labels with your local model via Ollama
python -m foodrec.ingest --no-llm             # keyword rules only (about 30 s)
```

The build parses ingredient amounts into grams, labels food groups, cuisine and meal type (model answers
are validated; invalid ones fall back to rules), computes footprints, allergens and diets (failing closed),
and finds similar recipes for the graph. Labels are cached in `data/hummus/labels.json`, so a run can be
resumed and copied to another computer.

**Data sources.** Nutrients and the FSA score come from HUMMUS. Footprints are estimates from ingredient
amounts and food-group factors. Allergens and diets are detected from ingredient names: verify them
before relying on them.

---

## Limitations

* Footprints are estimates from food-group averages, not a life-cycle assessment. Allergens and diets are
  detected from ingredient names and can be wrong.
* Healthiness is an operational nutrient measure for ranking, not medical or dietetic advice.
* Taste match and revealed weights are model estimates; learning rates and policy thresholds are design
  choices that still need calibration with users.
* The model's wording is checked for numbers, recipe names and medical claims, not for every possible
  misleading statement; the template answer is always available.
* One server process with SQLite: suited to research studies and demos, not to large production use.
