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
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("foodrec.config")


def _load_dotenv(path=ROOT / ".env"):
    """Minimal .env reader: KEY=VALUE lines; real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


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

    # List size and the starting goal of new users.
    top_k: int = field(default_factory=lambda: int(_env("FOODREC_TOP_K", 5)))
    default_diversity: float = 0.3
    default_mode: str = "lightweight"

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
        setattr(s, k, v)
        s.sources[k] = "override"
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
