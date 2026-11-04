# Web server and reloading

- **mode**: `local` (default): everyone signs in, the administrator account sees Config. `demo`: a public showcase where visitors choose a User or an Admin view, with sample data in Monitor and a read-only Config page. `open`: no sign-in (development and tests only).
- **host**: `127.0.0.1` keeps the app on this computer. `0.0.0.0` makes it reachable on the network; for a public server put HTTPS in front (see `docs/deployment.md`).
- **port**: default 8000.
- **auto_reload**: the app watches this config file. When it changes (saved here or edited by hand):
  - unchanged content → nothing happens;
  - invalid JSON or settings → the app keeps running with the last valid config and shows the problem here;
  - only "live" settings, roles, study and accounts → applied in place, no interruption;
  - a "restart" setting, criteria or interactions → the app restarts itself (a few seconds); open pages reconnect.

Open this page from a terminal with `foodrec config open`. It starts the app if needed and opens this page; its link signs you in as administrator on this computer. An administrator who signs in at the app comes here too, with **Monitor** and **Config** in the top bar. Participants never see either.

`foodrec` opens the app the same way: it starts it only if it is not running yet. `foodrec status` tells whether it runs, `foodrec stop` stops it from any terminal, and `foodrec serve` runs only the server (no browser).
