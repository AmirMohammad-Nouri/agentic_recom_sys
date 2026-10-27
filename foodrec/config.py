"""Runtime settings, read once from environment variables (see .env.example).

Every optional service degrades to a documented local fallback so the demo always runs:
  Ollama missing   -> rule-based intent parsing, template answers, hashing embeddings
  Neo4j missing    -> in-process graph store with the same query templates
  Qdrant missing   -> in-process numpy vector index
The active backend of each component is reported by GET /api/system and in every trace.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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

    # Supervisor policy limits.
    max_agent_steps: int = field(default_factory=lambda: int(_env("FOODREC_MAX_STEPS", 3)))

    @property
    def db_path(self):
        # v2: three criteria and the HUMMUS catalogue. The v1 database (foodrec.db) is left untouched.
        return self.data_dir / "foodrec_v2.db"

    @property
    def qdrant_path(self):
        return self.data_dir / "qdrant"


def load_settings(**overrides):
    s = Settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
