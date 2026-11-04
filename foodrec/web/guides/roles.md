# Access roles

A role decides what a user sees and can do. Each user has one role:

1. a **fixed role** for that id (`access.users`, e.g. administrators),
2. else the role of their **participant account** (Users & study),
3. else the role **assigned** at onboarding (round robin or random, Users & study),
4. else the **default role**.

## Features

Every switch is **enforced by the server**: hidden fields are removed from the API responses, endpoints of disabled features answer "forbidden", and the assistant skips skills the role does not allow (for example, without *AI explanations* it refuses "why is #1 recommended?").

| Feature | Off means |
|---|---|
| Assistant chat | no chat dock |
| Agent trace | answers without the step-by-step trace |
| AI explanations | no why-reasons, fit or compromise lines; explaining chat skills refused |
| Score breakdown | no factor tables and formulas |
| Similarity score | no similarity score or contribution bar. To hide only the number for everyone, use `ranking.show_similarity_score`. |
| Food details | no nutrition, kcal, CO₂e, grade, ingredient list |
| Recipe images | no thumbnails |
| Feedback buttons | no like / save / cook / reject |
| Supervisor questions | the supervisor never asks or proposes |
| Priority controls | no sliders, goal saving or what-if previews: the saved goal is always used |
| My dishes (account menu) / Insights / Activity | that page is hidden |
| Knowledge / Profile | that part of the Profile page is hidden (Knowledge, and Preferences with History) |
| Download own data | no personal export |
| Switch user | the user id cannot be changed in the top bar |
| Configuration | this page, all exports, study administration |

**Show score** rows decide which criterion scores the role sees. **Show every criterion** includes criteria added later.

## Typical study conditions

- **full**: everything (transparent condition).
- **no_explanations**: scores and the assistant, but no reasons, traces or breakdowns.
- **baseline**: a plain list with food details and feedback buttons only.

The export records each user's role and, for every list, exactly which scores were visible.

**Administrators** are anyone whose role has the *Config page* feature. They see only Monitor and Config, whatever else the role allows: no onboarding, preferences or recommendations. To see the participant side yourself, create a personal link and open it in a private window.
