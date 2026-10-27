# Architecture

## Principle

> The fundamental idea is not "an LLM recommends food". An agent continuously manages the
> learning process between the user and the recommender.
> — `docs/background/agentic_ideas.txt`

Every component has one job, and the boundaries are enforced in code:

| Component | Owns | Never does |
| --- | --- | --- |
| **Recommendation engine** (`engine.py`, `ranker.py`, `diversity.py`, `constraints.py`) | Eligibility, scoring, weighted utility, diversity, evidence records | Call a language model |
| **Preference model** (`learner.py`) | Taste counts and observed value weights, rebuilt from the event log, with provenance | Change the saved goal |
| **Supervisor policy** (`agent/policy.py`) | When to stay quiet, ask, or propose; counterfactuals | Apply a proposal |
| **Supervisor agent** (`agent/supervisor.py`, `agent/skills.py`) | Understand a message, plan skills, compose a validated answer | Call a skill with model-invented arguments; write graph queries |
| **Language model** (`llm.py`, via Ollama) | Choose among fixed options; fill a JSON schema; phrase facts | Rank, decide or edit anything |
| **Knowledge graph** (`knowledge/graph.py`, `knowledge/neo4j_graph.py`) | Food-domain relations and the user's interaction history as a queryable graph | Act as the source of truth (it is a projection) |
| **Hybrid retrieval** (`knowledge/retrieval.py`) | Passages from recipes, ingredients, method notes, user documents | Generate content |
| **Store** (`store.py`) | Append-only event log, versioned profile and goals, request snapshots, decisions, messages | — |
| **User** | Saving goals, accepting proposals, confirming rule changes | — |

## Data flow

```
                 ┌──────────────── Web UI (foodrec/web) ─────────────────┐
                 │ Discover · Assistant · Insights · Knowledge · Activity │
                 └───────────────▲───────────────────────┬───────────────┘
                     NDJSON trace│ stream                │ REST
                 ┌───────────────┴───────────────────────▼───────────────┐
                 │                    api.py (FastAPI)                   │
                 └───────┬──────────────────────┬────────────────────────┘
                         │                      │
              ┌──────────▼─────────┐   ┌────────▼──────────┐
              │ Supervisor agent   │   │ App service       │  the only writer
              │ perceive→understand│──▶│ service.py        │
              │ →plan→act→compose  │   └──┬─────┬────┬─────┘
              └──┬─────┬───────────┘      │     │    │
         Ollama  │     │ skills           │     │    │ events
   (intent/phrase)     ▼                  ▼     │    ▼
              ┌────────────┐ ┌──────────────┐   │  ┌───────────────┐
              │ Policy     │ │ Engine       │   │  │ SQLite store  │ ◀── source of truth
              │ (review)   │ │ (ranking)    │   │  │ events, goals,│
              └────────────┘ └──────────────┘   │  │ snapshots...  │
                                                │  └──────┬────────┘
                       ┌────────────────────────┤         │ replay / project
                       ▼                        ▼         ▼
                ┌──────────────┐        ┌──────────────┐ ┌──────────────┐
                │ Learner      │        │ Hybrid       │ │ Knowledge    │
                │ (per user)   │        │ retrieval    │ │ graph        │
                └──────────────┘        │ BM25+Qdrant  │ │ Neo4j/memory │
                                        └──────────────┘ └──────────────┘
```

Every state change goes through `App._emit`. It (1) appends to the event log, (2) applies
the event to the user's learner and (3) projects it into the graph. On start-up the learners
are rebuilt by replaying the log and the graph is re-projected (all writes are idempotent
`MERGE`s), so deleting the graph or the vector index loses nothing.

## Knowledge graph schema

Nodes carry a unique `uid` (`recipe:r001`, `ingredient:chickpeas`, `user:demo`,
`interaction:<event_id>` …) plus their label.

```
(Recipe)-[:CONTAINS {grams}]->(Ingredient)-[:IN_CATEGORY]->(Category)
(Recipe)-[:OF_CUISINE]->(Cuisine)          (Ingredient)-[:HAS_ALLERGEN]->(Allergen)
(Recipe)-[:HAS_ALLERGEN]->(Allergen)       (Recipe)-[:SUITS]->(Diet)
(Recipe)-[:SIMILAR_TO {score}]->(Recipe)   top-3 cosine neighbours, score >= 0.3
(User)-[:PERFORMED]->(Interaction {type, reason, ts, meal, place})-[:ON]->(Recipe)
(User)-[:DECLARED {kind, strict}]->(Allergen|Diet|Ingredient|Cuisine)   current profile
(User)-[:SET_GOAL]->(Goal {version, w_p … w_t})
(User)-[:RECEIVED]->(Request)-[:RECOMMENDED {rank, utility}]->(Recipe)
(User)-[:SUBJECT_OF]->(Decision {kind, status})-[:BASED_ON]->(Interaction)
(Decision)-[:ABOUT]->(Recipe)
```

Query templates (identical signatures in both backends): `timeline` (filter by time window,
interaction type, recipe, ingredient, cuisine, meal, place), `ingredient_profile`,
`recipe_context`, `alternatives` (similar recipe that scores better on one criterion),
`concept_evidence`, `decisions`, `recommended`, `search_nodes`, `neighborhood`, `stats`.

The model never writes Cypher. With a 2–4 B model, generated queries would be unreliable
and a safety risk; a fixed template whose arguments are validated is both auditable and
cheap.

## Retrieval

The corpus is built from the catalogue (one passage per recipe and per ingredient, every number
copied from catalogue fields), the method notes in `data/knowledge/*.md` and documents users
add. Search runs BM25 and dense cosine (Qdrant embedded on disk by default) and fuses them with
Reciprocal Rank Fusion (k = 60). Each hit reports `bm25_rank`, `dense_rank` and `rrf` so the
trace shows why it was retrieved. Without Ollama the dense side uses a feature-hashing embedder.
It is labelled *lexical, not semantic* everywhere it appears.

## Degradation

| Missing | Fallback | Visible in |
| --- | --- | --- |
| Ollama / model | Rule-based intent parser, template answers, no follow-up step | status pill, every trace (`source: rules`) |
| Embedding model | Hashing embedder | Knowledge → Library, `/api/system` |
| Qdrant | numpy index | `/api/system` |
| Neo4j | In-process graph with the same templates | status pill, Knowledge page |
