# Meal cycles

With cycles on, Discover walks the user through meals, day by day, instead of showing a free list.

1. The user sees **r** dishes for the first meal of day 1.
2. The user reacts to **every** dish: Like, Save, I'll cook this or Not for me.
3. **Continue** opens the next meal. After the last meal of the day, the next day starts.

In total the user gives **meals × r × n** reactions: 3 meals × 5 dishes × 3 days = 45.

| Setting | Meaning |
| --- | --- |
| `enabled` | For accounts without their own path. Off by default in your own copy. In the demo: always on in the user view, off in the admin view, which browses freely. Each participant's **Path** (Users & study) overrides it, and people with a link are guided unless you choose **Free**. |
| `meals` | The meals, in order, comma-separated: `breakfast`, `lunch`, `dinner`, `snack`, `dessert`, `drink`. The demo always uses breakfast, lunch and dinner. |
| `recommendations` | **r**, the dishes per meal. |
| `days` | **n**, the days in a cycle. |

**Good to know**

- A "day" is a step in the cycle, not a calendar day: the next day starts as soon as the last meal is done.
- The list of a meal is made once and kept, so reloading the page does not change it.
- If a user's rules leave too few dishes for a meal, that meal shows what there is, and the user can continue.
- A role without the **Feedback buttons** feature can't finish a meal, so it gets the normal list instead.
- At the end the user can start again. Earlier reactions stay recorded.
- The export lists every meal step and reaction under `meal_cycle`.
