# Creating the study population

Instead of adding participants one by one, describe the population and generate the accounts.

1. **Number of users**, e.g. 40.
2. **Percent per role**, e.g. full 50 %, baseline 50 %. The percentages must add up to 100. The counts follow the largest-remainder rule, so they always add up to the number of users (40 × 50 % = 20 + 20; 25 with 33/33/34 % = 8 + 8 + 9).
3. **Username prefix and digits**: `p` + 3 digits gives p001 … p040.
4. **Mix roles over usernames** (recommended): roles are assigned in a random order fixed by the **seed**, so p001-p020 are not all one condition. Off = blocks in role order.
5. **How people get in**:
   - **Username and password**: readable passwords such as `maple-river-42` (**Words per password** sets their length).
   - **Personal link only**: each person gets a link such as `https://your-server/join/<code>`, with nothing to type. Roles that can configure the app can't have links.

6. **Path users must follow**:
   - **Guided**: meal cycles with a reaction to every dish before moving on, the same as the demo. It uses the settings under **Settings → Meal cycles**.
   - **Free**: the normal Discover list.

**Generate participants** (or **Create links**) fills the accounts table. Review it: usernames, passwords and roles can be edited, accounts can be added or removed. Then **Save to file**. Generating again keeps the password or link of every username that stays.

The **CSV download** gives a sheet to hand out.

**In the demo** there are only links. **Create links** makes them at once for you alone, up to 50 at a time, and they work without saving. Creating new ones replaces your earlier links, and the nightly reset removes them.

Command line, same rules:

```
foodrec study generate --size 40 --role full=50 --role baseline=50 --write
foodrec study list
```

In the JSON file:

```json
"study": {
  "population": {"size": 40, "prefix": "p", "digits": 3,
                 "roles": {"full": 50, "baseline": 50},
                 "shuffle": true, "seed": 7, "password_words": 2},
  "participants": [{"username": "p001", "password": "maple-river-42", "role": "baseline"},
                   {"username": "p002", "link": "<code>", "role": "full", "flow": "guided"},
                   {"username": "p003", "link": "<code>", "role": "full", "active": false}]
}
```

**Security**: passwords and links are stored as written in the config file because you hand them out. They are access codes for a study server, not protection for sensitive data. Whoever has a link is that participant, so send each link only to its person, and keep the file private while it holds real ones (don't commit it). A link has no end date. To end it, clear **Active** for that account or remove it, then **Save to file**: it applies at once and the person is signed out. Clearing **Active** also blocks an account with a password. Passwords and links never appear in exports.
