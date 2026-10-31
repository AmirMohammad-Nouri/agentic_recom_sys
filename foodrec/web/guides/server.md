# Web server and reloading

- **host**: `127.0.0.1` keeps the app on this computer. `0.0.0.0` makes it reachable on the network; then also set `FOODREC_ADMIN_TOKEN` in the environment and turn on **Participants sign in** (Users & study), so nobody can pick an admin id.
- **port**: default 8000.
- **auto_reload**: the app watches this config file. When it changes (saved here or edited by hand):
  - unchanged content → nothing happens;
  - invalid JSON or settings → the app keeps running with the last valid config and shows the problem here;
  - only "live" settings, roles, study and accounts → applied in place, no interruption;
  - a "restart" setting, criteria or interactions → the app restarts itself (a few seconds); open pages reconnect.

Open this page from a terminal with `foodrec config open`. It starts the app if needed and opens only the Config page; its link signs you in as administrator on this computer. **Open the app** (top right) replaces the Config page with the app in the same tab. The app has no link back to the Config page: run `foodrec config open` again (it reuses the running app).
