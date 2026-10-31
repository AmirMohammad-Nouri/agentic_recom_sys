# Study settings

- **Name / description**: shown in the browser tab, on the sign-in page and on registration messages.
- **Participants sign in**: with this on, every page asks for a username and password (accounts below), and each participant can only use their own account. Administrators open this page with `foodrec config open`, `FOODREC_ADMIN_TOKEN`, or an account with an admin role.
- **Who may start**: `open` (any new id), `listed_only` (only fixed ids and participant accounts), `closed` (nobody new; existing users continue).
- **Maximum number of users**: counts onboarded users; administrators, fixed ids and participant accounts are not limited.
- **Role for new users without an account**: the default role, balanced rotation, or seeded random assignment among chosen roles. The assignment happens once, at onboarding, and is stored.
- **Users and fixed roles**: give one id a role that wins over everything (e.g. `admin`).

All of it is also in the JSON file under `study` and `access`.
