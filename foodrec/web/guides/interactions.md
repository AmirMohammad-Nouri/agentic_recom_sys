# Interactions: feedback options and how much they count

Users react to a recipe with **Like**, **Save**, **I'll cook this** (with a reason) and **Not for me** (with a reason). These options and their weights are configurable.

## Feedback strength (taste model)

How strongly each type counts as liking the dish's ingredients, cuisine and food groups:

| Type | Default |
|---|---|
| like | 1.0 |
| save | 0.6 |
| choose | 0.8 |

Taste of a concept = (1 + likes) / (2 + likes + dislikes), so higher strengths move taste faster.

## Reasons

Each reason is about a **criterion** or about **taste**.

**“I'll cook this” reasons** (choose):

- about a criterion (e.g. *It is healthy* → `h`): the choice moves the revealed weights toward the criteria on which the chosen dish beats the others shown, **plus** a nudge toward the named criterion of size `learning.nudge × weight`. If the dish is not actually better on that criterion, the reason is flagged as inconsistent and gives no nudge.
- about taste (*It sounds tasty*): adds `taste_bonus` to the like strength.

**“Not for me” reasons** (reject):

- about a criterion (e.g. *Not healthy enough*): moves the revealed weight of that criterion up by how much worse the dish is than the others shown, × `weight`. It is **not** a taste dislike.
- about taste (*Not my taste*): a dislike of the dish's concepts with `taste_strength` (1.0 = as strong as a like).
- **effect: recency** (*Had it recently*): no learning at all, only a fading repetition penalty for that dish.

Other fields:

- **Shown**: hidden reasons stay valid for the API and scenarios but are not offered on the card.
- **Ask: lasting or today**: after picking the reason, the user says whether it is a lasting preference or only today (then nothing lasting is learned).

Every enabled criterion needs at least one choose and one reject reason, because the supervisor asks about criteria through them. Missing ones are added automatically (*Good …* / *Poor …*); add your own to word them better.

## Learning parameters

| Parameter | Default | Effect |
|---|---|---|
| `eta` | 1.0 | learning rate: how far one choice moves the revealed weights |
| `nudge` | 0.2 | size of an explicit reason or an answered question |
| `anchor` | 0.05 | share of the saved goal mixed back in after every update (higher = slower drift) |
| `floor` | 0.02 | minimum weight before an update, so a weight set to 0 can still grow |
| `today_nudge` | 0.10 | extra weight for the rest of the day from an "only today" reason |
| `conf_k` | 5 | confidence = n / (n + conf_k); the supervisor needs confidence to ask or propose |

## How this affects results

- Interactions never change a **saved goal** directly. They change the **revealed** weights; the supervisor compares saved and revealed weights and asks or proposes when they drift apart. Stronger weights or a larger `eta` → the supervisor speaks up after fewer clicks.
- Taste strengths change the taste-match score, which keeps its fixed share of every utility.
- Changing these options restarts the app. Past events keep the reason ids they were recorded with; renaming a reason id means old events with the old id no longer count for it when the models are replayed.
