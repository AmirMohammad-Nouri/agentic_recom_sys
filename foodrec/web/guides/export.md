# Export

One record per user, for analysis. JSON (`export.json`) or a ZIP with the same JSON plus flat CSV tables.

| Field | Content |
|---|---|
| `user_id`, `access` | role, its features, the criteria it could see, where the role came from |
| `preferences` | declared profile (allergies, diets, likes, health goal) and goal weights, with every version |
| `criteria` | per criterion: saved weight, weight revealed by behaviour, gap, visible?, mean score shown and chosen |
| `taste_beliefs` | learned taste concepts with evidence |
| `recommendations` | every list: weights, filters, role; per recipe all `scores` and the `visible_scores` |
| `interactions` | feedback with rank in list, reason, scope, meal/place, and the scores visible at the time |
| `decisions` | supervisor questions and proposals with the response |
| `conversation` | chat turns with the agent steps behind each answer |
| `ui_events` | page views, panels opened, assistant opened/closed |
| `my_dishes` | what is in Saved for later, Liked and Cooked |
| `events`, `log` | raw event log; one merged timeline |
| `summary` | counts, acceptance rate, mean rank chosen, preference match, active minutes |

CSV tables: `users`, `recommendations` (one row per shown recipe, with `visible_*` columns), `interactions`, `decisions`, `messages`, `ui_events`, `criteria`, `log`, `events`.

Command line: `foodrec export --out study.zip`, or `--users p001,p002`. Scenario runs write the same export for their users.
