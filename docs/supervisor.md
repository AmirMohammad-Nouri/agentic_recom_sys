# The supervisor's decision system

The supervisor makes two kinds of decisions. **Monitoring decisions** (is the preference model
consistent? ask or propose?) are fully deterministic. **Conversational decisions** (what does
this message want? is one more lookup needed?) are bounded choices that the language model
makes from fixed options, with a rule-based fallback.

## 1. Monitoring loop (`agent/policy.py`)

Runs after every piece of feedback, on "Review now", and inside chat recommendations.

```
choices, reasoned rejections, answers ──▶ value model ──▶ observed weights o
                          │
          gap_k = o_k − w_k  (w = saved goal)
                          │
          all |gap| < 0.10, or < 3 events since the goal ──▶ "aligned" (stay quiet)
                          │
          criterion k with the largest |gap|
                          │
          another priority question already open? ──▶ wait (one at a time)
          topic answered/dismissed < 6 feedback events ago? ──▶ respect it
                          │
          search history in the graph (timeline, reasons naming k)
                          │
          |gap| ≥ 0.15 and ≥ 6 events?
               │ yes                                    │ no
               ▼                                        ▼
        GOAL PROPOSAL                            TRADE-OFF QUESTION
        w_k ← (w_k + o_k)/2, renormalise         "When k and j pull in different
        counterfactual: which recipes would       directions, which usually wins?"
        enter/leave the top five                  answer = evidence (±0.2 nudge),
        waits for Accept / Keep my goal           never edits the goal
```

Also checked every time: **taste conflicts**. A cuisine or ingredient the user *declared* as a
liking whose taste fell below 0.40, with at least 2 units of observed evidence, produces the
question "Do you still want X as a preference?". Removal only happens if the user picks that
option.

| Threshold | Value | Meaning |
| --- | --- | --- |
| `ask_gap` | 0.10 | Gap between observed and stated weight worth a question ... |
| `ask_min_events` | 3 | ... after this many informative events since the goal was saved |
| `propose_gap` | 0.15 | Gap worth a proposal ... |
| `propose_min_events` | 6 | ... after this many events |
| `taste_conflict_mean` | 0.40 | Declared liking contradicted when its taste falls below this |
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

**Answers control what is learned.** Questions offer the two winners (lasting evidence),
*Only today* (today's lists change, the day's drift is rolled back) and *Keep my goal* (the goal
stays, the learned weights are kept, and the gap seen at that moment counts as acknowledged: the
supervisor measures later gaps from there and only asks again when the choices move further from the
goal; moving back toward the goal never counts, while passing it in the other direction does). Proposals offer
*Update my goal*, *Only today* and *Keep my goal*.

**Several small options.** A message such as "more convenient, with only a small reduction in
healthiness" is parsed as `adjust_goal`. The `goal_options` skill builds up to four normalised
weight changes (convenience +5/+10/+15 points, healthiness down by at most 10 points, the third
value pays the rest), ranks with each, and stores them as one `goal_options` decision showing
every option's top five and how the average scores move. Nothing is saved until the user picks
*Save this goal* (or *Only today*). Also available as `POST /api/users/{uid}/goal-options`.

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
