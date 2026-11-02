# The user model, in plain language

This page explains what FoodRec knows about a user, where each number comes from, and how it
changes. It is written so the whole flow can be explained on one whiteboard. Every number in it
can be recomputed by replaying the user's event log (`learner.py`), so nothing is hidden state.

## 1. What onboarding asks, and what each answer does

| Step | Question | Stored as | Effect |
| --- | --- | --- | --- |
| 1 | Allergies and diets | profile (rule) | **Hard filter.** A recipe with that allergen, or with unknown allergen data, is never shown. |
| 2 | Cuisines and ingredients you like / dislike, "never include" | profile + taste model | "Never include" is a hard filter. Likes and dislikes start the taste model at 2 likes or 2 dislikes for that concept. |
| 3 | Health goal (eat healthier, lose weight, gain weight, build muscle, heart-friendly, less sugar) and an optional ingredient limit | profile | The goal sets the **weights of the nine health factors** (for example, *build muscle* weights protein ×4). The ingredient limit is strict (filter) or a preference (shown as a soft target). |
| 4 | A few dishes you like (searchable, with photos) | taste model | Each counts as one like for every cuisine, ingredient and food group of that dish. |
| 5 | How much healthiness, sustainability and convenience should count | goal (version 1) | The **stated weights** w = (w_h, w_s, w_t), summing to 1. |

Rules (step 1, "never include", a strict limit) are never relaxed by the system. Everything else
is a preference that ranking trades off.

## 2. How a recipe is ranked

Each recipe has four scores between 0 and 1:

* **Healthiness**: nine nutrient factors per 100 g, each on a straight line between a good and
  a poor anchor, averaged with the goal's weights.
* **Sustainability**: estimated kg CO₂e per kg of dish, on a log scale (0.5 → 1, 30 → 0).
* **Convenience**: number of ingredients (3 → 1, 15 → 0).
* **Taste match**: from the taste model below.

```
utility = 0.25 × taste  +  0.75 × (w_h × health + w_s × sustainability + w_t × convenience)
```

Taste has a fixed 25 % share so the three sliders mean exactly what they say, and a dish the
user would never eat cannot win on numbers alone. The top five are picked with a diversity step
that avoids five near-identical dishes.

## 3. The two learned parts

### Taste model: "do they like this kind of food?"

For every concept (a cuisine, an ingredient, a food group) FoodRec counts likes and dislikes:

```
taste(concept) = (1 + likes) / (2 + likes + dislikes)        0.5 = no opinion yet
```

| Action | Counts as |
| --- | --- |
| Like, or reject with "not my taste" | 1 |
| *I'll cook this* | 0.8 (1.0 with the reason "it sounds tasty") |
| Save | 0.6 |
| Other rejections without a value reason ("other") | 0.5 dislike |
| "Had it recently" | nothing: it is a recency signal, not a dislike (see below) |

Repeated clicks count once: a like or save once per recipe, *I'll cook this* once per recipe
per list, a rejection once per recipe, list and reason.

A recipe's taste match is the weighted mean over its concepts (cuisine 35 %, ingredients 45 %
by mass, food groups 20 %). The user can correct any concept on the Insights page; the
correction replaces the counts and is labelled "your correction".

### Value model: "what do they actually prioritise?"

The **observed weights** o = (o_h, o_s, o_t) estimate how the user's behaviour weighs the three
values. They start equal to the stated weights and move only on informative events:

| Event | Update |
| --- | --- |
| Chose a dish from the five shown | for each value k: d_k = score_k(chosen) − average score_k of the five; o_k ← o_k × e^(d_k) |
| ... with a reason ("it is healthy", "it is climate-friendly", "it is easy") | that value also gets d_k + 0.2 |
| Rejected with a reason ("not healthy enough", "not sustainable", "too complicated") | d_k = average score_k of the five − score_k(rejected), same update |
| Answered a trade-off question | winner × e^0.2, loser × e^−0.2 |
| Saved a new goal | o moves halfway to the new goal; the event count restarts |

Before each update every o_k is lifted to at least 0.02 (the floor), so a value the user set to
0 % can still grow when their choices favour it; without the floor 0 × e^(d) stays 0 forever.
After every update o is rescaled to sum to 1 and mixed with 5 % of the stated goal, so a run
of unusual choices moves the estimate but cannot wipe a priority out. The observed weights
never change the saved goal; only a goal the user saves or a proposal they accept does.

### Temporary circumstances vs lasting preferences

*I'll cook this* with a reason and every *Not for me* reason except "had it recently" ask one
more thing: **Update my preference** (lasting, the updates above) or **Only today**. An
"only today" signal changes neither model. It adjusts today's lists only: the named value gets
+10 points of weight for the rest of the day and a rejected dish is hidden until tomorrow.

Supervisor questions and proposals offer the same choice:

| Answer | Effect |
| --- | --- |
| Name a winner / *Update my goal* | lasting: recorded as evidence, or the proposal is saved as a new goal version |
| *Only today* | today's lists use the proposed weights (or a +10 point nudge); the value model is rolled back to how it was at the start of the day |
| *Keep my goal* | the saved goal stays and the observed weights are **kept** (Insights still show what your choices reveal). The current gap is recorded as acknowledged: the supervisor only asks again when your choices drift **further from the goal** than that, counted in events since then. Moving back toward the goal never counts; passing the goal in the other direction counts in full |

### Recency ("had it recently")

Choosing a dish, or rejecting it as "had it recently", stores when it was eaten. For 14 days the
dish loses `0.15 × 0.5^(days since / 3)` utility (0.15 today, 0.075 after three days), shown on
its card as "Had it ...". Its ingredients and cuisine get no dislikes.

**Worked example.** Stated (h, s, t) = (0.40, 0.40, 0.20). The five dishes shown average
convenience 0.50; the user picks one with convenience 0.90 and average health and footprint.
Then d_t = 0.40, so o_t ← 0.20 × e^0.40 = 0.30, the others stay ≈ 0.40. After rescaling and
the 5 % anchor: o ≈ (0.37, 0.37, 0.27). Convenience has gone up 7 points.

### What the Insights page shows

* For each value: **You chose** (w_k) next to **You do** (o_k), and the gap o_k − w_k.
* **Preference match** = 1 − ½ (|w_h − o_h| + |w_s − o_s| + |w_t − o_t|). 100 % means the
  behaviour follows the goal exactly; the example above gives 93 %.
* **Confidence** = n / (n + 5), where n is the number of informative events since the goal was
  saved (5 events → 50 %).
* Every event that moved the weights, with how many points it moved each one.

## 4. The AI agent's role

The agent (the supervisor) watches the gap between stated and observed weights. It **never
changes the saved goal or a rule by itself**; it can only ask or propose.

| Situation | What the agent does |
| --- | --- |
| Every gap below 10 points | Nothing. Keeps recommending. |
| A gap of 10 points or more, after at least 3 events | Asks one trade-off question: "When convenience and healthiness pull in different directions, which usually wins for you?" The answer is evidence for the value model. |
| A gap of 15 points or more, after at least 6 events | Proposes moving that weight halfway to the observed value, with a preview of which recipes would enter or leave the top five. The user accepts or keeps the goal. |
| A declared liking with taste below 0.40 after 2+ rejections | Asks "Do you still want Italian as a preference?" |

Restraint: only one question is open at a time, and a topic the user answered or dismissed is
left alone for 6 feedback events.

In the chat, the language model (local Qwen) does two things only: it turns a message into a
structured request (validated against a fixed schema, with keyword rules as fallback), and it
words answers from facts the tools computed. Every number comes from the ranking engine, the
user model or the knowledge graph, and the timeline above each answer shows which.

## 5. The whole flow

```
onboarding ──▶ rules + stated weights w + health goal + taste counts
                     │
                     ▼
            rank: filter by rules ─▶ score h, s, t, taste ─▶ utility ─▶ diversity ─▶ top 5
                     │
                     ▼
     user likes / saves / chooses / rejects (with optional reason)  ──▶  event log
                     │
                     ▼
            replay: taste counts update, observed weights o update
                     │
                     ▼
            supervisor compares w and o ──▶ stay quiet / ask a question / propose a change
                     │                                         │
                     ▼                                         ▼
            next ranking uses w (the goal)            the user decides; only an accepted
            and the updated taste counts              proposal or a saved goal changes w
```

## Parameters

| Name | Value | Where |
| --- | --- | --- |
| Taste share | 0.25 | `foodrec/__init__.py: TASTE_SHARE` |
| Learning rate η | 1.0 | `learner.py: PARAMS["eta"]` |
| Reason / answer nudge | 0.2 | `PARAMS["nudge"]` |
| Goal anchor | 0.05 | `PARAMS["anchor"]` |
| Observed-weight floor | 0.02 | `PARAMS["floor"]` |
| "Only today" nudge | 0.10 | `PARAMS["today_nudge"]` |
| Repetition penalty / half-life / window | 0.15 / 3 days / 14 days | `engine.py: DEFAULT_CONFIG` |
| Minimum nutrient coverage | 0.6 | `FOODREC_MIN_NUTRIENT_COVERAGE` |
| Maximum missing value weight | 0.5 | `FOODREC_MAX_MISSING_SHARE` |
| Confidence constant | 5 | `PARAMS["conf_k"]` |
| Declared like / dislike | 2 counts | `PARAMS["declared_count"]` |
| Ask / propose gap | 0.10 / 0.15 | Config: `supervisor.ask_gap`, `propose_gap` |
| Ask / propose minimum events | 3 / 6 | Config: `supervisor.ask_min_events`, `propose_min_events` |
| Cooldown | 6 events | Config: `supervisor.cooldown_events` |

These are design choices, not fitted values. They are exposed at `GET /api/system` and should
be calibrated with simulated or real users before an evaluation.
