"""Watch the config file and reload the app when it changes (server.auto_reload).

    content unchanged (e.g. saved again, or only whitespace) -> nothing happens
    invalid JSON or invalid settings                         -> the app keeps its current config;
                                                                the problem is shown on the Config page
    only "live" settings, roles, study, users changed       -> applied in place, no interruption
    a "restart" setting, criteria or interactions changed   -> the process restarts itself with the
                                                                same command line (a few seconds)

The same path is taken whether the file was edited by hand or saved from the Config page.

The watcher also restarts the app when its own Python code changes (e.g. after `git pull` or
an edit), so a running server never serves an older backend to a newer web page. Web files
(JS, CSS) need no restart: the browser loads them fresh.
"""
import logging
import os
import sys
import threading
import time
from pathlib import Path

from .appconfig import AppConfig

log = logging.getLogger("foodrec.reload")
LAUNCH_ARGV = None  # set by cli.main: the arguments to restart with


class ConfigWatcher(threading.Thread):
    def __init__(self, core, path, restart=None, interval=1.0):
        super().__init__(daemon=True, name="config-watcher")
        self.core, self.path, self.interval = core, Path(path), interval
        self.restart = restart or restart_process
        self._stamp = self._stat()
        self._code = self._code_stamp()
        self._stop = threading.Event()

    def _stat(self):
        try:
            st = self.path.stat()
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None

    @staticmethod
    def _code_stamp():
        pkg = Path(__file__).resolve().parent
        try:
            return max(p.stat().st_mtime_ns for p in pkg.rglob("*.py"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _compile_errors(since):
        """Syntax errors in the files changed after `since` (a restart into them would fail)."""
        import py_compile
        pkg = Path(__file__).resolve().parent
        for p in pkg.rglob("*.py"):
            try:
                if since is None or p.stat().st_mtime_ns > since:
                    py_compile.compile(str(p), doraise=True, cfile=os.devnull)
            except py_compile.PyCompileError as e:
                return f"{p.name}: {e.msg.strip().splitlines()[-1]}"
            except OSError:
                continue
        return None

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.wait(self.interval):
            code = self._code_stamp()
            if code != self._code and not getattr(self.core, "restarting", False):
                old, self._code = self._code, code
                broken = self._compile_errors(old)
                if broken:
                    log.error("the app's code changed but does not compile (%s); keeping the running version",
                              broken)
                    continue
                log.warning("the app's Python code changed: restarting to use it")
                self.core.restarting = True
                threading.Timer(0.8, self.restart).start()
                continue
            stamp = self._stat()
            if stamp != self._stamp:
                self._stamp = stamp
                try:
                    self.check()
                except Exception:  # noqa: BLE001 - a watcher must never die
                    log.exception("config reload failed")

    def check(self):
        """Compare the file with the running config and act. Returns what happened."""
        core = self.core
        try:
            new = AppConfig.load(self.path)
        except (ValueError, OSError) as e:
            core.config_problem = str(e)
            log.warning("config file not applied: %s", e)
            return "invalid"
        if new.errors:
            core.config_problem = "; ".join(new.errors)
            log.warning("config file not applied: %s", core.config_problem)
            return "invalid"
        core.config_problem = None
        if new.version == core.cfg.version and not core.restart_pending:
            return "unchanged"
        pending = core.apply_config(new)
        if not pending:
            log.info("config reloaded (live settings applied, no restart needed)")
            return "applied"
        if not core.s.auto_reload:
            log.info("config changed; %d setting(s) wait for a manual restart", len(pending))
            return "pending"
        log.warning("config changed (%s): restarting", ", ".join(p["setting"] for p in pending))
        core.restarting = True
        threading.Timer(0.8, self.restart).start()  # let the current response reach the browser
        return "restart"


def restart_process():
    """Replace this process with a fresh one started the same way (`python -m foodrec ...`).
    The listening socket closes with the old process and the new one binds the same port."""
    argv = LAUNCH_ARGV if LAUNCH_ARGV is not None else sys.argv[1:]
    for h in logging.getLogger().handlers:
        h.flush()
    sys.stdout.flush()
    sys.stderr.flush()
    time.sleep(0.1)
    os.execv(sys.executable, [sys.executable, "-m", "foodrec", *argv])
