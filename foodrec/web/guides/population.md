# Creating the study population

Instead of adding participants one by one, describe the population and generate the accounts.

1. **Number of users**, e.g. 40.
2. **Percent per role**, e.g. full 50 %, baseline 50 %. The percentages must add up to 100. The counts follow the largest-remainder rule, so they always add up to the number of users (40 × 50 % = 20 + 20; 25 with 33/33/34 % = 8 + 8 + 9).
3. **Username prefix and digits**: `p` + 3 digits gives p001 … p040.
4. **Mix roles over usernames** (recommended): roles are assigned in a random order fixed by the **seed**, so p001-p020 are not all one condition. Off = blocks in role order.
5. **Words per password**: readable passwords such as `maple-river-42`.

**Generate participants** fills the accounts table. Review it: every username, password and role can be edited, accounts can be added or removed. Then **Save to file**. Generating again keeps the password of every username that stays.

**Download usernames and passwords (CSV)** gives a sheet to hand out.

Command line, same rules:

```
foodrec study generate --size 40 --role full=50 --role baseline=50 --write
foodrec study list
```

In the JSON file:

```json
"study": {
  "login_required": true,
  "population": {"size": 40, "prefix": "p", "digits": 3,
                 "roles": {"full": 50, "baseline": 50},
                 "shuffle": true, "seed": 7, "password_words": 2},
  "participants": [{"username": "p001", "password": "maple-river-42", "role": "baseline"}]
}
```

**Security**: passwords are stored as written in the config file because you hand them out. They are access codes for a local study server, not protection for sensitive data. Keep the file private while it holds real credentials (don't commit it), and turn on sign-in plus `FOODREC_ADMIN_TOKEN` when the server is reachable from other computers.
