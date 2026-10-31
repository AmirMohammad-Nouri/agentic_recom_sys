"""Command line: foodrec [serve | scenario | export | config | users].

    foodrec                                   start the web app (same as: foodrec serve)
    foodrec --config study.json serve         use another config file
    foodrec scenario run scenarios/*.json     run scripted scenarios, write reports
    foodrec scenario check scenarios/*.json   validate scenario files without running them
    foodrec export --out study.zip            export every user (JSON or a ZIP of CSV tables)
    foodrec config open                       open the Config page (starts the app if needed)
    foodrec config show | check | init | path
    foodrec study generate [--write]          create the study population from study.population
    foodrec study list                        participants with their role and password
    foodrec users                             list users with their role
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path


def _serve(a, open_page=None):
    from .api import main
    main(host=getattr(a, "host", None), port=getattr(a, "port", None), open_page=open_page)


def _open(a):
    """Open the Config page. If the app is already running, only open the browser; otherwise
    start it here (and keep it running). The link carries this installation's admin token, so
    the page opens as administrator without choosing a user."""
    import httpx

    from .appconfig import AppConfig
    from .config import load_settings
    from .study import admin_link_token
    s = load_settings(config=AppConfig.load())
    host = a.host or s.host
    port = a.port or s.port
    bhost = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    page = f"/config#token={admin_link_token(s.data_dir)}"
    try:
        running = httpx.get(f"http://{bhost}:{port}/api/system", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        running = False
    url = f"http://{bhost}:{port}{page}"
    if running:
        print(f"FoodRec is running. Config page: {url}")
        if not a.no_browser:
            import webbrowser
            webbrowser.open(url)
        return 0
    print(f"Starting FoodRec on {bhost}:{port}; the Config page opens when it is ready.\n  {url}")
    a.host, a.port = host, port
    # a restart after a config change must not open the browser again: restart as a plain server
    from . import reload
    reload.LAUNCH_ARGV = ["serve", "--host", str(host), "--port", str(port)]
    return _serve(a, open_page=None if a.no_browser else page)


def _study(a):
    from .appconfig import AppConfig
    from .study import generate
    cfg = AppConfig.load()
    st = cfg.study
    if a.cmd == "list":
        parts = st.get("participants") or []
        print(f"{len(parts)} participant(s); login required: {bool(st.get('login_required'))}")
        for p in parts:
            print(f"  {p['username']:<16} {p['role']:<18} {p['password']}")
        return 0
    pop = dict(st.get("population") or {})
    if a.size is not None:
        pop["size"] = a.size
    if a.role:
        pop["roles"] = {r: float(v) for r, v in (x.split("=", 1) for x in a.role)}
    if a.prefix:
        pop["prefix"] = a.prefix
    parts, counts = generate(pop, None if a.new_passwords else st.get("participants"))
    print(f"{len(parts)} participant(s): " + ", ".join(f"{r} {n}" for r, n in counts.items()))
    for p in parts[:10]:
        print(f"  {p['username']:<16} {p['role']:<18} {p['password']}")
    if len(parts) > 10:
        print(f"  ... and {len(parts) - 10} more")
    if not a.write:
        print("Not saved. Add --write to store them in the config file.")
        return 0
    data = json.loads(json.dumps(cfg.raw))
    data.setdefault("study", {})["population"] = pop
    data["study"]["participants"] = parts
    cfg.save(data)
    print(f"Saved to {cfg.path}" + (" (a running app reloads it automatically)" if cfg.path else ""))
    return 0


def _core():
    """The app on the configured data, without the web server (for export and users)."""
    from .config import load_settings
    from .service import App
    logging.getLogger("foodrec").setLevel(logging.WARNING)
    return App(load_settings())


def _scenario(a):
    from .scenario import ScenarioError, load, run_file
    files = [Path(f) for f in a.files]
    if a.cmd == "check":
        bad = 0
        for f in files:
            try:
                sc = load(f)
                print(f"ok    {f}  ({sc['name']}, {len(sc.get('steps') or [])} steps)")
            except ScenarioError as e:
                bad += 1
                print(f"error {e}")
        return 1 if bad else 0
    overrides = dict(kv.split("=", 1) for kv in a.set or [])
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    reports = []
    for f in files:
        try:
            reports += run_file(f, None if a.no_output else a.out, overrides, a.matrix, a.data_dir, a.keep_services,
                                log=lambda *x: print(*x, flush=True))
        except ScenarioError as e:
            print(f"error {e}")
            reports.append({"scenario": str(f), "passed": False})
    if len(reports) > 1:
        print(f"\n{sum(r['passed'] for r in reports)}/{len(reports)} scenario run(s) passed")
    return 0 if all(r["passed"] for r in reports) else 1


def _export(a):
    from .export import export, to_json, to_zip
    app = _core()
    users = a.users.split(",") if a.users else None
    doc = export(app, users, include_raw=not a.no_raw)
    fmt = a.format or ("zip" if a.out and a.out.endswith(".zip") else "json")
    data = to_zip(doc) if fmt == "zip" else to_json(doc).encode()
    if a.out:
        Path(a.out).write_bytes(data)
        print(f"wrote {a.out}: {len(doc['users'])} user(s), {len(data) / 1e3:.0f} kB")
    else:
        if fmt == "zip":
            print("a ZIP needs --out FILE", file=sys.stderr)
            return 2
        sys.stdout.write(data.decode())
    return 0


def _config(a):
    from .appconfig import DEFAULT_PATH, AppConfig, config_path, defaults
    if a.cmd == "path":
        print(config_path() or "(no config file: FOODREC_CONFIG=none)")
        return 0
    if a.cmd == "init":
        path = Path(a.file) if a.file else (config_path() or DEFAULT_PATH)
        if path.exists() and not a.force:
            print(f"{path} exists; use --force to overwrite it with the defaults", file=sys.stderr)
            return 1
        path.write_text(json.dumps(defaults(), indent=2) + "\n")
        print(f"wrote {path}")
        return 0
    cfg = AppConfig.load(Path(a.file) if a.file else None)
    if a.cmd == "check":
        print(f"{cfg.path}: " + ("ok" if not cfg.errors else "\n  " + "\n  ".join(cfg.errors)))
        return 1 if cfg.errors else 0
    from .config import load_settings
    s = load_settings(config=cfg)
    from .appconfig import FIELDS
    print(f"config file: {cfg.path or '(none)'}  version {cfg.version}")
    for f in FIELDS:
        print(f"  {f['section'] + '.' + f['key']:<32} {str(getattr(s, f['attr'])):<44} [{s.sources.get(f['attr'])}]")
    acc = cfg.data["access"]
    print(f"roles: {', '.join(cfg.roles)}; default {acc.get('default_role')}; "
          f"{len(acc.get('users') or {})} user(s) with a fixed role")
    st = cfg.study
    print(f"study: {st.get('name') or '(unnamed)'}; registration {st.get('registration')}; "
          f"max users {st.get('max_users') or 'unlimited'}; assignment {st.get('assignment')}")
    return 0


def _users(a):
    app = _core()
    rows = app.store.user_rows()
    print(f"{'user':<20} {'role':<18} {'source':<9} {'onboarded':<10} events")
    for u in rows:
        acc = app.access(u["user_id"])
        print(f"{u['user_id']:<20} {acc['role']:<18} {acc['source']:<9} {str(u['onboarded']):<10} {u['events']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="foodrec", description="FoodRec: run the app, scenarios, exports and config.")
    ap.add_argument("--config", help="config file (default foodrec.config.json; 'none' for built-in defaults)")
    sub = ap.add_subparsers(dest="command")

    sp = sub.add_parser("serve", help="start the web app (default)")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)

    sc = sub.add_parser("scenario", help="run or check scenario files")
    sc.add_argument("cmd", choices=["run", "check"])
    sc.add_argument("files", nargs="+")
    sc.add_argument("--out", default="out/scenarios", help="report folder (default out/scenarios)")
    sc.add_argument("--no-output", action="store_true", help="print only, write no files")
    sc.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="config override, e.g. llm.model=qwen3:4b or llm.enabled=false (repeatable)")
    sc.add_argument("--matrix", action="append", metavar="KEY=V1,V2",
                    help="run once per value, e.g. llm.model=qwen3.5:2b-q4_K_M,qwen3:4b (repeatable)")
    sc.add_argument("--data-dir", help="use this data folder instead of a fresh temporary one")
    sc.add_argument("--keep-services", action="store_true",
                    help="use the configured Neo4j/Qdrant instead of in-process stores")

    ex = sub.add_parser("export", help="export users (the standard study output)")
    ex.add_argument("--out", help="file to write (.json or .zip); default: JSON to stdout")
    ex.add_argument("--format", choices=["json", "zip"])
    ex.add_argument("--users", help="comma-separated user ids (default: all)")
    ex.add_argument("--no-raw", action="store_true", help="leave out the raw event log")

    cf = sub.add_parser("config", help="open the Config page; show, check or create the config file")
    cf.add_argument("cmd", choices=["open", "show", "check", "init", "path"])
    cf.add_argument("file", nargs="?")
    cf.add_argument("--force", action="store_true")
    cf.add_argument("--host")
    cf.add_argument("--port", type=int)
    cf.add_argument("--no-browser", action="store_true", help="print the link only")

    st = sub.add_parser("study", help="study population: generate or list participants")
    st.add_argument("cmd", choices=["generate", "list"])
    st.add_argument("--size", type=int, help="number of participants (default: study.population.size)")
    st.add_argument("--role", action="append", metavar="ROLE=PERCENT", help="e.g. --role full=50 --role baseline=50")
    st.add_argument("--prefix", help="username prefix (default: study.population.prefix)")
    st.add_argument("--new-passwords", action="store_true", help="new passwords for existing usernames too")
    st.add_argument("--write", action="store_true", help="save to the config file")

    sub.add_parser("users", help="list users and their roles")

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(argv)
    if a.config and os.environ.get("FOODREC_CONFIG") != a.config:
        # criteria and interactions are read when the package is imported, i.e. before these
        # arguments were parsed: start again with the config file in the environment
        os.environ["FOODREC_CONFIG"] = a.config
        os.execv(sys.executable, [sys.executable, "-m", "foodrec", *raw_argv])
    from . import reload
    reload.LAUNCH_ARGV = raw_argv
    cmd = a.command or "serve"
    if cmd == "serve":
        if not a.command:
            a.host = a.port = None
        return _serve(a)
    if cmd == "config" and a.cmd == "open":
        return _open(a)
    return {"scenario": _scenario, "export": _export, "config": _config, "users": _users, "study": _study}[cmd](a)


if __name__ == "__main__":
    sys.exit(main())
