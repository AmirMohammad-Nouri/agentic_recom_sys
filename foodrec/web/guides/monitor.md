# Monitor

Monitor is where the administrator follows a study. It shows the populations, the people in them, and what each person did.

**At the top**: the totals for the whole study, then **Compare populations**, with one row per population.

| Column | Meaning |
| --- | --- |
| Started | People who have opened the app at least once |
| Finished | People who completed their meal cycle |
| Per person | Reactions per person who started |
| Rejected | Share of reactions that were *Not for me* |
| Open questions | Supervisor questions waiting for an answer |

**Below that**: one table per population. A person's status is one of these:

- **Invited**: has an account or link, but has not come yet.
- **Started**: has come, but has not reacted to a dish or used the assistant yet.
- **In progress**: has reacted to a dish or used the assistant.
- **Finished**: completed the meal cycle.
- **Deactivated**: you ended their access.

Click a person to read their record:

- **Interactions**: every reaction, with the dish, its position in the list and the reason.
- **Log**: the full timeline of lists shown, actions, messages and supervisor decisions.
- **Conversation**: what they asked the assistant and what it answered.
- **Supervisor**: what it asked or proposed, why, and how they responded.
- **Values**: the weights they saved, next to the weights their choices show.

**Populations.** A population is a named group of participants. Set the name under **Config → Users & study** when you generate it, or type it in the accounts table. Participants with no name are in *Main population*. Anyone who used the app without being a participant appears under *Other users*. Administrators are not listed.

**Downloading the data.** Each population and person has **Export as JSON**. For everything at once, or for CSV tables, use **Config → Export**.

**In the demo**, the populations marked **Sample** are made up: nobody real is behind them, and they are not stored. Links you create under **Config → Users & study** appear under *Your links*, with what those people really do. Only you can see them.
