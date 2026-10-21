"""Per-stage processing measurements.

Measured:  wall-clock latency (perf_counter) and process CPU time per stage.
Energy:    read from the Linux RAPL package counter ONLY when /sys/class/powercap is
           readable. It covers the whole CPU package (not just this process) and is
           absent on most cloud VMs; then energy is recorded as null with a note.
           Remote API calls: only calls and tokens are recorded; these are NOT energy.
Food footprint and computing energy are never combined.
"""
import glob
import os
import platform
import time
from contextlib import contextmanager


def _rapl_path():
    for p in sorted(glob.glob("/sys/class/powercap/intel-rapl:*/energy_uj")):
        try:
            with open(p) as f:
                f.read()
            return p
        except OSError:
            continue
    return None


_RAPL = _rapl_path()


def _energy_uj():
    if _RAPL is None:
        return None
    try:
        with open(_RAPL) as f:
            return int(f.read())
    except (OSError, ValueError):
        return None


def environment():
    return {"python": platform.python_version(), "platform": platform.platform(),
            "cpu_count": os.cpu_count(), "energy_source": "rapl_package" if _RAPL else "unavailable",
            "energy_note": ("RAPL package counter covers the whole CPU package, not this process"
                            if _RAPL else "no readable energy counter on this machine")}


class Telemetry:
    def __init__(self):
        self.stages = {}
        self.llm_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cache = "not_used"

    @contextmanager
    def stage(self, name):
        t0, c0, e0 = time.perf_counter(), time.process_time(), _energy_uj()
        try:
            yield
        finally:
            t1, c1, e1 = time.perf_counter(), time.process_time(), _energy_uj()
            row = self.stages.setdefault(name, {"wall_ms": 0.0, "cpu_ms": 0.0, "energy_j": None})
            row["wall_ms"] += (t1 - t0) * 1000.0
            row["cpu_ms"] += (c1 - c0) * 1000.0
            if e0 is not None and e1 is not None and e1 >= e0:
                row["energy_j"] = (row["energy_j"] or 0.0) + (e1 - e0) / 1e6

    def record_llm(self, tokens_in=0, tokens_out=0):
        self.llm_calls += 1
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out

    def summary(self):
        groups = {"recommendation": ("filter", "candidates", "scoring", "rerank"),
                  "explanation": ("evidence", "narrative")}
        out = {"stages": {k: {kk: (round(vv, 3) if isinstance(vv, float) else vv)
                              for kk, vv in v.items()} for k, v in self.stages.items()},
               "llm_calls": self.llm_calls, "tokens_in": self.tokens_in,
               "tokens_out": self.tokens_out, "cache": self.cache}
        for g, names in groups.items():
            out[f"{g}_wall_ms"] = round(sum(self.stages.get(n, {}).get("wall_ms", 0.0) for n in names), 3)
        out["total_wall_ms"] = round(sum(v["wall_ms"] for v in self.stages.values()), 3)
        energies = [v["energy_j"] for v in self.stages.values() if v["energy_j"] is not None]
        out["total_energy_j"] = round(sum(energies), 6) if energies else None
        out["energy_boundary"] = environment()["energy_note"]
        return out
