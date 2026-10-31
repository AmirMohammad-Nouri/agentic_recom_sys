# Supervisor

- **max_agent_steps**: how many skills one chat turn may run (planned skill plus model-chosen follow-ups).

The supervisor's decision thresholds (when to ask a question or propose a goal change) are documented in `docs/supervisor.md`. Whether a user sees its questions at all is a role feature (**supervisor**, Access roles).
