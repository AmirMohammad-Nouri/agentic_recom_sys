# Ranking

- **top_k**: recipes per list (default 5).
- **show_similarity_score**: the number on each recipe card, called the similarity score (the formulas call it utility). Off hides the number for everyone; the bar and the reason stay.
- **default_diversity** / **default_mode**: the starting goal of new users. Diversity 0 = pure utility order; higher values promote dishes that differ from earlier picks (utility rank and final rank are both recorded).
- **max_missing_share**: a recipe whose unscored criteria carry more than this share of the user's weight is not ranked; otherwise the other value weights are scaled up for that recipe and the card says so.
- **min_nutrient_coverage**: below this share of nutrient factors, healthiness counts as missing instead of being computed from a few nutrients.

Every list is stored as an immutable snapshot with the exact settings, so changing these values never changes past results in the export.

## Fresh lists

The ranking is the same calculation every time, so without help the same dishes stay on top. Measured on HUMMUS for a user who opens Discover twice a day for two weeks and cooks one dish a day (30 lists): **27 different dishes and 5 cuisines without freshness, 55 dishes and 7 cuisines with the defaults, 61 dishes and 10 cuisines with one "New for you" place** (`scenarios/fresh_lists.json`).

| Setting | Default | Effect |
|---|---|---|
| `hide_saved` | on | Saved dishes wait in **My dishes** instead of taking a Discover place. Removing one from Saved brings it back. |
| `fatigue_per_view` | 0.03 | Each past session in which a dish was shown and the user neither chose nor rejected it lowers its utility by this much. |
| `fatigue_max` | 0.12 | Cap per dish. |
| `fatigue_half_life_days` | 2 | Fatigue fades: a showing counts half after this many days. |
| `cuisine_fatigue_per_slot` | 0.01 | Each list place a cuisine filled recently lowers every dish of that cuisine by this much ("Other" never). |
| `cuisine_fatigue_max` | 0.06 | Cap per cuisine. |
| `history_lists` | 3 | The variety step also keeps a new list apart from this many recent lists (counted at half strength, and scaled by the user's diversity setting; diversity 0 turns it off). |
| `discovery_slots` | 0 | List places given to the best dish of a cuisine not shown within `discovery_days`, marked **New for you**. |
| `freshness_window_days` | 14 | How far back showings count. |

**Sessions.** Lists of the last hour do not count yet, so the list stays the same while the user works with it (moving sliders, coming back to the page). Older lists count once per hour.

**What it changes.** Utility = the usual weighted sum − repetition penalty − dish fatigue − cuisine fatigue. Every penalty is shown on the card ("Shown 3× recently", "New for you: Thai") and in the ranking notes, and stored with the list, so the export shows why a dish moved. Fatigue never changes what is learned about the user, the saved goal or the supervisor. Set the fatigue values and `history_lists` to 0 for the old fixed ranking (for example in a study condition that should not rotate).
