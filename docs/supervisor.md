# The supervisor's decision system

The supervisor makes two kinds of decisions. **Monitoring decisions** (is the preference model
consistent? ask or propose?) are fully deterministic. **Conversational decisions** (what does
this message want? is one more lookup needed?) are bounded choices that the language model
makes from fixed options, with a rule-based fallback.

## 1. Monitoring loop (`agent/policy.py`)

Runs after every piece of feedback, on "Review now", and inside chat recommendations.

```
evidence ──▶ learner ──▶ compare saved goal w with revealed weights θ
                          │
          P(|θ_k − w_k| > δ) < 0.70 for all k ──▶ "aligned" / "uncertain" (stay quiet)
                          │
          conflict on criterion k (largest P·|diff|)
                          │
          another priority question already open? ──▶ wait (one at a time)
          topic answered/dismissed < 6 feedback events ago? ──▶ respect it
                          │
          search history in the graph (timeline, reasons naming k)
                          │
          P ≥ 0.90 and ≥ 4 informative events since the last explicit statement?
               │ yes                                    │ no
               ▼                                        ▼
        GOAL PROPOSAL                            TRADE-OFF QUESTION
        w_k ← (w_k + θ_k)/2, renormalise         "When k and j pull in different
        counterfactual: which recipes would       directions, which usually wins?"
        enter/leave the top five                  answer = Bradley–Terry evidence
        waits for Accept / Keep my goal           (never edits the goal)
```

Also checked every time: **taste conflicts**. A cuisine or ingredient the user *declared* as a
liking whose belief fell below 0.40, with at least 2 units of evidence, produces the question
"Do you still want X as a preference?". Removal only happens if the user picks that option.

| Threshold | Value | Meaning |
| --- | --- | --- |
| `conflict_prob` | 0.70 | Posterior probability that a weight differs from the goal by more than δ = 0.08 |
| `strong_prob` | 0.90 | Needed to propose rather than ask |
| `min_evidence` | 4 | Informative events (choices, reasoned rejections, answers) since the last statement |
| `low_confidence` | 0.25 | Below this, results are described as resting on the saved goal |
| `taste_conflict_mean` | 0.40 | Declared liking contradicted when its belief falls below this |
| `taste_min_evidence` | 2.0 | ... with at least this much observed evidence |
| `cooldown_events` | 6 | Feedback events before an answered or dismissed topic can return |

These are design choices for the demo and are exposed at `GET /api/system`. They should be
calibrated with simulated users before any evaluation.

## 2. A chat turn (`agent/supervisor.py`)

| Stage | What happens | Who decides |
| --- | --- | --- |
| perceive | Load profile/goal versions, rules, open decisions, the latest list | store |
| understand | Classify into one of 10 intents and extract typed slots (criterion, recipe references, request filters, time window, profile change) | model with JSON schema, else rules; rules always run and their agreement is shown |
| validate | Drop unknown cuisines, ingredients, recipes and out-of-range numbers; report each drop | validator |
| plan | Map the intent to skills; every argument comes from validated slots | deterministic table |
| act | Run skills; each emits a `tool` step with arguments, summary and evidence | engine / graph / retriever / learner |
| follow up | At most `FOODREC_MAX_STEPS` (3) total skills; the model may choose one extra skill from a list the plan allows | model, choice from fixed enum |
| compose | Template answer built from the skills' facts | deterministic |
| phrase | Model rewrites the facts (≤110 words) | model |
| check | Reject if any number, recipe title or medical/certainty claim is not in the evidence; then use the template | validator |

The trace streams to the UI as NDJSON (`step`, `step_update`, `model_token`, `done`). If
"Show model reasoning" is on, the model's own thinking tokens appear in a separate step
labelled **unverified**: the verified steps are the agent's, the model's reasoning is shown
for transparency but never trusted.

## 3. Skills

| Skill | Side effect |
| --- | --- |
| `recommend` (request-only filters or a request-only weight emphasis) | snapshot |
| `explain_recipe`, `compare_recipes`, `what_if`, `explain_preference`, `history`, `search_knowledge`, `graph_lookup`, `alternatives` | none |
| `review` | proposal |
| `propose_profile_change` | proposal |

"Proposal" means a decision record that waits for the user. No skill edits a saved goal or
a mandatory rule.

## 4. Why not let the model do more?

A 2–4 B model on a 4 GB GPU is good at short classification and rewriting, but unreliable at
long tool-calling loops, arithmetic and free-form queries. The design keeps the model where it
is strong and puts every consequential decision in code you can test (`tests/test_agent.py`).
Every decision is logged as state → options → chosen → outcome, which keeps the system
auditable and leaves room to replace a component with a specialised model later.
