# Supervisor

- **max_agent_steps**: how many skills one chat turn may run (planned skill plus model-chosen follow-ups).

## When the supervisor speaks up

After each piece of feedback the supervisor compares the weights the user saved with the weights their choices behave like. The difference is the **gap**: 0.10 means 10 points.

| Setting | Default | Meaning |
| --- | --- | --- |
| `ask_gap` | 0.10 | Ask a question at this gap ... |
| `ask_min_events` | 3 | ... after this many informative feedback events. |
| `propose_gap` | 0.15 | Propose changing the goal at this gap ... |
| `propose_min_events` | 6 | ... after this many events. |
| `taste_conflict_mean` | 0.40 | Ask whether a declared liking still holds when its learned belief falls below this ... |
| `taste_min_evidence` | 2.0 | ... with at least this much observed evidence. |
| `cooldown_events` | 6 | After an answer or dismissal, wait this many feedback events before raising the topic again. |

Lower values make the supervisor speak up sooner and more often. A proposal may not need less than a question: `propose_gap` must be at least `ask_gap`, and `propose_min_events` at least `ask_min_events`.

Changes apply at once, to everyone, from their next feedback. For a study, set them **before** participants start, because the setting changes what people are asked.

In the demo, a visitor in the admin view can try these under **Try these yourself**. That only affects that visitor and the people using their links.

More detail: `docs/supervisor.md`. Whether a user sees the supervisor's questions at all is a role feature (**supervisor**, Access roles).
