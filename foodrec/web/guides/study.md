# Study settings

- **Name / description**: shown in the browser tab, on the sign-in page and on registration messages.
- **Sign-in** (server mode `local`, the default): every page asks for a username and password. Participants use the accounts below and can only use their own account; they never see Config. The **administrator account** (its own card) is the only one that sees Config. The app does not start until the administrator has a password (`foodrec admin password`, or this page). Changing a password or removing an account signs that person out at once.
- In `local` mode only these accounts can sign in, so **Who may start**, **Maximum number of users** and **Role for new users** only matter in the `open` development mode.
- **Who may start**: `open` (any new id), `listed_only` (only fixed ids and participant accounts), `closed` (nobody new; existing users continue).
- **Maximum number of users**: counts onboarded users; administrators, fixed ids and participant accounts are not limited.
- **Role for new users without an account**: the default role, balanced rotation, or seeded random assignment among chosen roles. The assignment happens once, at onboarding, and is stored.
- **Users and fixed roles**: give one id a role that wins over everything (e.g. `admin`).

All of it is also in the JSON file under `study` and `access`.
