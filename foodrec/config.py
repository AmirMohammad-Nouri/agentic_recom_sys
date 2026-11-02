"""Runtime settings: built-in defaults < environment variables (.env) < the config file
(foodrec.config.json, see appconfig.py and docs/configuration.md).

Every optional service degrades to a documented local fallback so the demo always runs:
  Ollama missing   -> rule-based intent parsing, template answers, hashing embeddings
  Neo4j missing    -> in-process graph store with the same query templates
  Qdrant missing   -> in-process numpy vector index
The active backend of each component is reported by GET /api/system and in every trace.
"""
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("foodrec.config")


DOTENV = ROOT / ".env"
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


def _load_dotenv(path=None):
    path = path or DOTENV
    """Minimal .env reader: KEY=VALUE lines; real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def set_dotenv(name, value, path=None):
    """Add, update (value) or remove (value None) one KEY=VALUE line in .env, keeping every other
    line and comment, and apply it to this process at once. .env is git-ignored and made
    readable by its owner only. Used for API keys entered on the Config page."""
    if not ENV_NAME_RE.match(name or ""):
        raise ValueError("the variable name must be capital letters, digits and _ (e.g. OPENAI_API_KEY)")
    if value is not None and ("\n" in value or "\r" in value or not value.strip()):
        raise ValueError("the value must be one non-empty line")
    path = Path(path or DOTENV)
    lines = path.read_text().splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key == name:
            if value is not None and not done:
                out.append(f"{name}={value.strip()}")
            done = True
            continue
        out.append(line)
    if value is not None and not done:
        out.append(f"{name}={value.strip()}")
    path.write_text("\n".join(out) + "\n")
    path.chmod(0o600)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value.strip()
    return {"name": name, "set": value is not None, "file": str(path)}


def _env(name, default):
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def _flag(name, default):
    return str(_env(name, str(default))).lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("FOODREC_DATA_DIR", ROOT / "data" / "runtime")))
    # Portable catalogue built by `python -m foodrec.ingest`; imported into the database on start-up.
    catalogue_path: Path = field(default_factory=lambda: Path(_env("FOODREC_CATALOGUE", ROOT / "data" / "hummus" / "catalogue.json.gz")))
    knowledge_dir: Path = field(default_factory=lambda: Path(_env("FOODREC_KNOWLEDGE_DIR", ROOT / "data" / "knowledge")))

    # Supervisor language model (Ollama). Default sized for a 4 GB GPU, see README.
    llm_enabled: bool = field(default_factory=lambda: _flag("FOODREC_LLM", True))
    # ollama | openai (any OpenAI-compatible API) | anthropic
    llm_provider: str = field(default_factory=lambda: _env("FOODREC_LLM_PROVIDER", "ollama"))
    llm_base_url: str = field(default_factory=lambda: _env("FOODREC_LLM_BASE_URL", ""))
    llm_api_key_env: str = ""          # NAME of the variable holding the key; never the key itself
    llm_effort: str = "low"            # Anthropic effort
    llm_max_tokens: int = 4096         # minimum output budget for hosted providers
    llm_fallbacks: bool = True         # Anthropic server-side refusal fallbacks
    embed_provider: str = field(default_factory=lambda: _env("FOODREC_EMBED_PROVIDER", "ollama"))
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://127.0.0.1:11434"))
    llm_model: str = field(default_factory=lambda: _env("FOODREC_LLM_MODEL", "qwen3.5:2b-q4_K_M"))
    llm_ctx: int = field(default_factory=lambda: int(_env("FOODREC_LLM_CTX", 8192)))
    llm_think: bool = field(default_factory=lambda: _flag("FOODREC_LLM_THINK", False))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("FOODREC_LLM_TIMEOUT", 60)))
    llm_seed: int = field(default_factory=lambda: int(_env("FOODREC_LLM_SEED", 7)))
    # Embeddings run on the CPU by default so the supervisor keeps the whole GPU.
    embed_model: str = field(default_factory=lambda: _env("FOODREC_EMBED_MODEL", "nomic-embed-text"))
    embed_on_cpu: bool = field(default_factory=lambda: _flag("FOODREC_EMBED_CPU", True))

    # Knowledge graph (Neo4j). Empty URI -> in-process graph.
    neo4j_uri: str = field(default_factory=lambda: _env("NEO4J_URI", ""))
    neo4j_user: str = field(default_factory=lambda: _env("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _env("NEO4J_PASSWORD", ""))
    neo4j_database: str = field(default_factory=lambda: _env("NEO4J_DATABASE", "neo4j"))

    # Vector store (Qdrant). "local" = embedded on-disk mode, a URL = Qdrant server,
    # "memory" = numpy index without Qdrant.
    qdrant: str = field(default_factory=lambda: _env("FOODREC_QDRANT", "local"))

    # Missing data. Healthiness needs at least this share of its (goal-weighted) nutrient factors;
    # below it the score is treated as missing. A recipe whose missing criteria carry more than
    # max_missing_share of the user's value weight is not ranked (see ranker.py).
    min_nutrient_coverage: float = field(default_factory=lambda: float(_env("FOODREC_MIN_NUTRIENT_COVERAGE", 0.6)))
    max_missing_share: float = field(default_factory=lambda: float(_env("FOODREC_MAX_MISSING_SHARE", 0.5)))

    # Supervisor policy limits.
    max_agent_steps: int = field(default_factory=lambda: int(_env("FOODREC_MAX_STEPS", 3)))
    # When the supervisor asks or proposes (agent/policy.py)
    ask_gap: float = 0.10
    ask_min_events: int = 3
    propose_gap: float = 0.15
    propose_min_events: int = 6
    taste_conflict_mean: float = 0.40
    taste_min_evidence: float = 2.0
    cooldown_events: int = 6
    # What recipe cards show
    show_similarity_score: bool = True
    # Meal cycles (service.py "Meal cycles"): meals x recommendations x days reactions
    cycle_enabled: bool = False
    cycle_meals: str = "breakfast,lunch,dinner"
    cycle_recommendations: int = 5
    cycle_days: int = 3

    # List size and the starting goal of new users.
    top_k: int = field(default_factory=lambda: int(_env("FOODREC_TOP_K", 5)))
    # Fresh lists (engine.py "Freshness"): saved dishes, fatigue, variety across lists, discovery.
    hide_saved: bool = True
    fatigue_per_view: float = 0.03
    fatigue_max: float = 0.12
    fatigue_half_life_days: float = 2.0
    cuisine_fatigue_per_slot: float = 0.01
    cuisine_fatigue_max: float = 0.06
    history_lists: int = 3
    discovery_slots: int = 0
    discovery_days: float = 7.0
    freshness_window_days: float = 14.0
    default_diversity: float = 0.3
    default_mode: str = "lightweight"

    # Web server and config-file watching.
    host: str = field(default_factory=lambda: _env("FOODREC_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("FOODREC_PORT", 8000)))
    auto_reload: bool = field(default_factory=lambda: _flag("FOODREC_AUTO_RELOAD", True))
    # local (everyone signs in) | demo (public showcase, read-only config) | open (no sign-in; dev/tests)
    server_mode: str = field(default_factory=lambda: _env("FOODREC_MODE", "local"))

    # Where each setting came from: "default", "env", "config" or "override".
    sources: dict = field(default_factory=dict)
    # The AppConfig the settings were read from (access roles and study settings live there).
    config: object = None

    @property
    def db_path(self):
        # v2: three criteria and the HUMMUS catalogue. The v1 database (foodrec.db) is left untouched.
        return self.data_dir / "foodrec_v2.db"

    @property
    def qdrant_path(self):
        return self.data_dir / "qdrant"


def apply_config(s, cfg):
    """Copy the values the config file sets onto `s` (paths resolved against the repo root).
    Returns {attr: (old, new)} for the values that changed."""
    from .appconfig import FIELDS, coerce, resolve_path
    changed = {}
    for f in FIELDS:
        v = coerce(f, cfg.value(f["section"], f["key"]))
        if v is None:
            continue
        if f["type"] == "path":
            v = resolve_path(v)
        old = getattr(s, f["attr"])
        if old != v:
            env = f.get("env") and os.environ.get(f["env"])
            if env and s.sources.get(f["attr"]) == "env":
                log.info("%s.%s from the config file overrides %s", f["section"], f["key"], f["env"])
            changed[f["attr"]] = (old, v)
            setattr(s, f["attr"], v)
        s.sources[f["attr"]] = "config"
    return changed


def load_settings(config=None, **overrides):
    """`config`: an AppConfig, or None to read the file named by FOODREC_CONFIG (default
    foodrec.config.json; FOODREC_CONFIG=none disables it). `overrides` win over everything."""
    from .appconfig import FIELDS, AppConfig
    s = Settings()
    s.sources = {f["attr"]: ("env" if f.get("env") and os.environ.get(f["env"]) else "default") for f in FIELDS}
    s.config = config if config is not None else AppConfig.load()
    apply_config(s, s.config)
    for k, v in overrides.items():
        if k in ("data_dir", "catalogue_path", "knowledge_dir") and isinstance(v, str):
            v = Path(v)
        setattr(s, k, v)
        s.sources[k] = "override"
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
