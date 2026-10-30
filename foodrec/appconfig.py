"""The application config file (foodrec.config.json): model, dataset, ranking, access roles
and study settings in one JSON file that the Config page edits.

Precedence for runtime settings:  built-in default < environment / .env < config file.
A value of null in the file means "not set here": the environment or the default applies.
Secrets (the Neo4j password, the admin token) are read from the environment only and are
never written to the file.

Sections
  llm, dataset, services, ranking, supervisor   runtime settings (FIELDS below)
  access                                        roles: which features and criteria a user sees
  study                                         who may register, how many, how roles are assigned
Every field says whether a change applies at once ("live") or after a restart.
"""
import copy
import hashlib
import json
import logging
import os
from pathlib import Path

from . import CRITERIA, CRITERION_LABELS

log = logging.getLogger("foodrec.config")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "foodrec.config.json"
CONFIG_VERSION = 1

# ---- runtime settings ------------------------------------------------------------------------
# (section, key) -> spec. `attr` is the Settings attribute, `env` the environment variable.
FIELDS = [
    # language model
    {"section": "llm", "key": "enabled", "attr": "llm_enabled", "env": "FOODREC_LLM", "type": "bool", "apply": "live",
     "label": "Use the language model", "help": "Off: rule-based intent parsing and template answers."},
    {"section": "llm", "key": "url", "attr": "ollama_url", "env": "OLLAMA_URL", "type": "str", "apply": "live",
     "label": "Ollama URL", "machine": True},
    {"section": "llm", "key": "model", "attr": "llm_model", "env": "FOODREC_LLM_MODEL", "type": "str", "apply": "live",
     "label": "Supervisor model", "help": "Any model pulled in Ollama, e.g. qwen3.5:2b-q4_K_M, qwen3:4b, llama3.2:3b."},
    {"section": "llm", "key": "ctx", "attr": "llm_ctx", "env": "FOODREC_LLM_CTX", "type": "int", "min": 512, "apply": "live",
     "label": "Context window (tokens)"},
    {"section": "llm", "key": "think", "attr": "llm_think", "env": "FOODREC_LLM_THINK", "type": "bool", "apply": "live",
     "label": "Model reasoning by default"},
    {"section": "llm", "key": "timeout_s", "attr": "llm_timeout_s", "env": "FOODREC_LLM_TIMEOUT", "type": "float", "min": 1,
     "apply": "live", "label": "Timeout (s)"},
    {"section": "llm", "key": "seed", "attr": "llm_seed", "env": "FOODREC_LLM_SEED", "type": "int", "apply": "live",
     "label": "Sampling seed"},
    {"section": "llm", "key": "embed_model", "attr": "embed_model", "env": "FOODREC_EMBED_MODEL", "type": "str",
     "apply": "restart", "label": "Embedding model"},
    {"section": "llm", "key": "embed_on_cpu", "attr": "embed_on_cpu", "env": "FOODREC_EMBED_CPU", "type": "bool",
     "apply": "restart", "label": "Embeddings on CPU"},
    # dataset and storage
    {"section": "dataset", "key": "catalogue_path", "attr": "catalogue_path", "env": "FOODREC_CATALOGUE", "type": "path",
     "apply": "restart", "label": "Recipe catalogue file",
     "help": "A catalogue built with `python -m foodrec.ingest` (.json.gz or .json). Point it at your own dataset."},
    {"section": "dataset", "key": "knowledge_dir", "attr": "knowledge_dir", "env": "FOODREC_KNOWLEDGE_DIR", "type": "path",
     "apply": "restart", "label": "Method notes folder"},
    {"section": "dataset", "key": "data_dir", "attr": "data_dir", "env": "FOODREC_DATA_DIR", "type": "path",
     "apply": "restart", "label": "Runtime data folder",
     "help": "Database, vector index and caches. Use a separate folder per dataset or study."},
    # services
    {"section": "services", "key": "neo4j_uri", "attr": "neo4j_uri", "env": "NEO4J_URI", "type": "str", "apply": "restart",
     "machine": True, "label": "Neo4j URI", "help": "Empty: in-process graph. The password is read from NEO4J_PASSWORD only."},
    {"section": "services", "key": "neo4j_user", "attr": "neo4j_user", "env": "NEO4J_USER", "type": "str",
     "apply": "restart", "machine": True, "label": "Neo4j user"},
    {"section": "services", "key": "neo4j_database", "attr": "neo4j_database", "env": "NEO4J_DATABASE", "type": "str",
     "apply": "restart", "machine": True, "label": "Neo4j database"},
    {"section": "services", "key": "qdrant", "attr": "qdrant", "env": "FOODREC_QDRANT", "type": "str", "apply": "restart",
     "machine": True, "label": "Vector store", "help": "\"local\" (embedded Qdrant), \"memory\" (numpy) or a Qdrant server URL."},
    # ranking
    {"section": "ranking", "key": "top_k", "attr": "top_k", "env": "FOODREC_TOP_K", "type": "int", "min": 1, "max": 20,
     "apply": "live", "label": "Recipes per list"},
    {"section": "ranking", "key": "default_diversity", "attr": "default_diversity", "env": None, "type": "float",
     "min": 0, "max": 1, "apply": "live", "label": "Diversity for new users"},
    {"section": "ranking", "key": "default_mode", "attr": "default_mode", "env": None, "type": "choice",
     "options": ["lightweight", "extended"], "apply": "live", "label": "Computation mode for new users"},
    {"section": "ranking", "key": "max_missing_share", "attr": "max_missing_share", "env": "FOODREC_MAX_MISSING_SHARE",
     "type": "float", "min": 0, "max": 1, "apply": "live", "label": "Max. missing weight share",
     "help": "A recipe whose missing criteria carry more of the user's weight than this is not ranked."},
    {"section": "ranking", "key": "min_nutrient_coverage", "attr": "min_nutrient_coverage",
     "env": "FOODREC_MIN_NUTRIENT_COVERAGE", "type": "float", "min": 0, "max": 1, "apply": "restart",
     "label": "Min. nutrient coverage", "help": "Below this share of nutrient factors, healthiness counts as missing."},
    # supervisor
    {"section": "supervisor", "key": "max_agent_steps", "attr": "max_agent_steps", "env": "FOODREC_MAX_STEPS",
     "type": "int", "min": 1, "max": 8, "apply": "live", "label": "Max. skill calls per chat turn"},
]
SECTIONS = {"llm": "Language model", "dataset": "Dataset and storage", "services": "Knowledge services",
            "ranking": "Ranking", "supervisor": "Supervisor"}

# ---- access control ---------------------------------------------------------------------------
# What a role may see or do. A feature missing from a role counts as allowed, except `configure`.
FEATURES = {
    "assistant": ("Assistant chat", "The chat dock on every page."),
    "agent_trace": ("Agent trace", "The step-by-step trace under answers and in the supervisor activity feed."),
    "explanations": ("AI explanations", "Why-reasons on recipe cards and explaining answers in chat (why, compare, "
                     "what the system believes)."),
    "score_breakdown": ("Score breakdown", "\"How it's scored\": factor tables and formulas."),
    "utility": ("Utility score", "The overall utility number and the contribution bar."),
    "food_details": ("Food details", "Nutrition, kcal, CO2e, health grade and the ingredient list."),
    "images": ("Recipe images", "Thumbnails on recipe cards."),
    "feedback": ("Feedback buttons", "Like, save, I'll cook this, not for me."),
    "supervisor": ("Supervisor questions", "The supervisor's questions and goal proposals after feedback."),
    "weight_controls": ("Priority controls", "Sliders on Discover, saving goals and what-if previews."),
    "page_insights": ("Insights page", "What the system has learned about the user."),
    "page_knowledge": ("Knowledge page", "Knowledge graph, document library and retrieval tester."),
    "page_activity": ("Activity page", "The user's own event log and decisions."),
    "page_profile": ("Profile page", "Editing rules and preferences after onboarding."),
    "export_own": ("Download own data", "Export of the user's own record."),
    "switch_user": ("Switch user", "Change the user id in the top bar."),
    "configure": ("Configuration", "This Config page, all users' exports and study administration."),
}
ADMIN_ONLY = {"configure"}

# Chat skills that a disabled feature removes from the supervisor's plan.
SKILLS_BY_FEATURE = {
    "explanations": ["explain_recipe", "explain_preference", "compare_recipes", "alternatives"],
    "supervisor": ["review", "goal_options"],
    "weight_controls": ["what_if", "goal_options"],
}

REGISTRATION = ("open", "listed_only", "closed")
ASSIGNMENT = ("default", "round_robin", "random")


def _all(**off):
    return {k: off.get(k, k not in ADMIN_ONLY) for k in FEATURES}


DEFAULT_ROLES = {
    "admin": {"label": "Administrator", "description": "Everything, including this Config page.",
              "features": {k: True for k in FEATURES}, "visible_criteria": list(CRITERIA), "blocked_skills": []},
    "full": {"label": "Full transparency", "description": "All explanations, scores and the assistant.",
             "features": _all(), "visible_criteria": list(CRITERIA), "blocked_skills": []},
    "no_explanations": {"label": "No explanations",
                        "description": "Scores and the assistant, but no why-reasons, traces or score breakdowns.",
                        "features": _all(explanations=False, agent_trace=False, score_breakdown=False),
                        "visible_criteria": list(CRITERIA), "blocked_skills": []},
    "baseline": {"label": "Baseline list", "description": "A plain list of recipes with food details and feedback "
                 "buttons. No assistant, scores, explanations or supervisor.",
                 "features": _all(assistant=False, agent_trace=False, explanations=False, score_breakdown=False,
                                  utility=False, supervisor=False, weight_controls=False, page_insights=False,
                                  page_knowledge=False, page_activity=False, switch_user=False),
                 "visible_criteria": [], "blocked_skills": []},
}


def defaults():
    """The full default config (what `foodrec config init` writes)."""
    out = {"config_version": CONFIG_VERSION}
    for f in FIELDS:
        # machine-specific settings (service addresses) are left to the environment by default
        out.setdefault(f["section"], {})[f["key"]] = None if f.get("machine") else _builtin_default(f)
    out["access"] = {"default_role": "full", "roles": copy.deepcopy(DEFAULT_ROLES),
                     "users": {"admin": "admin", "demo": "admin"}}
    out["study"] = {"name": "", "description": "", "registration": "open", "max_users": None,
                    "assignment": "default", "assign_roles": ["full", "baseline"], "seed": 7}
    return out


_BUILTIN = {"llm_enabled": True, "ollama_url": "http://127.0.0.1:11434", "llm_model": "qwen3.5:2b-q4_K_M",
            "llm_ctx": 8192, "llm_think": False, "llm_timeout_s": 60.0, "llm_seed": 7,
            "embed_model": "nomic-embed-text", "embed_on_cpu": True,
            "catalogue_path": "data/hummus/catalogue.json.gz", "knowledge_dir": "data/knowledge",
            "data_dir": "data/runtime", "neo4j_uri": "", "neo4j_user": "neo4j", "neo4j_database": "neo4j",
            "qdrant": "local", "top_k": 5, "default_diversity": 0.3, "default_mode": "lightweight",
            "max_missing_share": 0.5, "min_nutrient_coverage": 0.6, "max_agent_steps": 3}


def _builtin_default(f):
    return _BUILTIN[f["attr"]]


# ---- coercion and validation ------------------------------------------------------------------
def coerce(f, v):
    """Convert a file/UI/environment value to the field's type; ValueError if impossible."""
    if v is None:
        return None
    t = f["type"]
    if t == "bool":
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
        raise ValueError(f"{f['section']}.{f['key']}: expected true or false, got {v!r}")
    if t in ("int", "float"):
        try:
            x = int(v) if t == "int" and float(v) == int(float(v)) else float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{f['section']}.{f['key']}: expected a number, got {v!r}") from None
        if t == "int" and not float(x).is_integer():
            raise ValueError(f"{f['section']}.{f['key']}: expected a whole number, got {v!r}")
        x = int(x) if t == "int" else float(x)
        if "min" in f and x < f["min"] or "max" in f and x > f["max"]:
            raise ValueError(f"{f['section']}.{f['key']}: {x} is outside {f.get('min')}..{f.get('max')}")
        return x
    if t == "choice":
        if v not in f["options"]:
            raise ValueError(f"{f['section']}.{f['key']}: must be one of {f['options']}")
        return v
    return str(v)


def resolve_path(p):
    p = Path(p).expanduser()
    return p if p.is_absolute() else ROOT / p


def role_features(role):
    """Complete feature map of a role (missing features: allowed, except admin-only ones)."""
    fs = role.get("features") or {}
    return {k: bool(fs.get(k, k not in ADMIN_ONLY)) for k in FEATURES}


def validate(cfg):
    """Return a list of human-readable problems (empty when the config is usable)."""
    errs = []
    if not isinstance(cfg, dict):
        return ["the config must be a JSON object"]
    for f in FIELDS:
        sec = cfg.get(f["section"])
        if sec is None:
            continue
        if not isinstance(sec, dict):
            errs.append(f"{f['section']}: must be an object")
            continue
        try:
            coerce(f, sec.get(f["key"]))
        except ValueError as e:
            errs.append(str(e))
    known = {f["section"] for f in FIELDS} | {"access", "study", "config_version", "$comment"}
    errs += [f"unknown section '{k}'" for k in cfg if k not in known]
    for sec in SECTIONS:
        keys = {f["key"] for f in FIELDS if f["section"] == sec}
        errs += [f"{sec}.{k}: unknown setting" for k in (cfg.get(sec) or {}) if k not in keys and k != "$comment"]
    acc = cfg.get("access") or {}
    roles = acc.get("roles") or {}
    if not roles:
        errs.append("access.roles: at least one role is needed")
    for name, r in roles.items():
        if not isinstance(r, dict):
            errs.append(f"access.roles.{name}: must be an object")
            continue
        errs += [f"access.roles.{name}.features: unknown feature '{k}'" for k in (r.get("features") or {})
                 if k not in FEATURES]
        errs += [f"access.roles.{name}.visible_criteria: unknown criterion '{c}' (use {', '.join(CRITERIA)})"
                 for c in r.get("visible_criteria", CRITERIA) if c not in CRITERIA]
    if acc.get("default_role") and acc["default_role"] not in roles:
        errs.append(f"access.default_role: '{acc['default_role']}' is not a defined role")
    for uid, rn in (acc.get("users") or {}).items():
        if rn not in roles:
            errs.append(f"access.users.{uid}: role '{rn}' is not defined")
    st = cfg.get("study") or {}
    if st.get("registration", "open") not in REGISTRATION:
        errs.append(f"study.registration: must be one of {', '.join(REGISTRATION)}")
    if st.get("assignment", "default") not in ASSIGNMENT:
        errs.append(f"study.assignment: must be one of {', '.join(ASSIGNMENT)}")
    for rn in st.get("assign_roles") or []:
        if rn not in roles:
            errs.append(f"study.assign_roles: role '{rn}' is not defined")
    if st.get("assignment") in ("round_robin", "random") and not st.get("assign_roles"):
        errs.append("study.assign_roles: list the roles to assign")
    mu = st.get("max_users")
    if mu is not None and (not isinstance(mu, int) or mu < 1):
        errs.append("study.max_users: a positive whole number or null")
    return errs


# Maps that a file replaces as a whole: a study that defines its own roles or users must not
# inherit the default ones.
REPLACED = {"roles", "users"}


def merge(base, over):
    """Deep-merge `over` into a copy of `base` (dicts merge, except REPLACED; the rest replaces)."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in REPLACED \
            else copy.deepcopy(v)
    return out


def set_path(cfg, dotted, value):
    """Set "llm.model" style keys; values are parsed as JSON when possible ("true", "3", "[...]")."""
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        pass
    node = cfg
    *head, last = dotted.split(".")
    for k in head:
        node = node.setdefault(k, {})
    node[last] = value
    return cfg


# ---- the loaded config -----------------------------------------------------------------------
def config_path():
    p = os.environ.get("FOODREC_CONFIG")
    if p is None:
        return DEFAULT_PATH
    return None if p.strip().lower() in ("", "none") else resolve_path(p)


class AppConfig:
    """The parsed config file merged over the defaults. `raw` is what the file holds."""

    def __init__(self, data=None, path=None):
        self.path = Path(path) if path else None
        self.raw = data or {}
        self.data = merge(defaults(), self.raw)
        self.errors = validate(self.data)

    @classmethod
    def load(cls, path=None):
        path = path if path is not None else config_path()
        if path is None or not Path(path).exists():
            return cls({}, path)
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: not valid JSON ({e})") from e
        cfg = cls(data, path)
        if cfg.errors:
            log.warning("config %s has problems: %s", path, "; ".join(cfg.errors))
        return cfg

    def save(self, data, path=None):
        """Validate and write `data` to the config file. Returns the new AppConfig."""
        errs = validate(merge(defaults(), data))
        if errs:
            raise ValueError("; ".join(errs))
        path = Path(path or self.path or DEFAULT_PATH)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
        return AppConfig(data, path)

    @property
    def version(self):
        return hashlib.sha256(json.dumps(self.data, sort_keys=True).encode()).hexdigest()[:10]

    # ---- settings values ----
    def value(self, section, key):
        """The value the FILE sets (None when it leaves it to the environment/default)."""
        return (self.raw.get(section) or {}).get(key)

    # ---- access ----
    @property
    def roles(self):
        return self.data["access"]["roles"]

    @property
    def study(self):
        return self.data["study"]

    def role_name(self, user_id, assigned=None):
        """Explicit entry in access.users > stored assignment > default role."""
        acc = self.data["access"]
        explicit = (acc.get("users") or {}).get(user_id)
        if explicit in self.roles:
            return explicit, "config"
        if assigned and assigned in self.roles:
            return assigned, "assigned"
        dr = acc.get("default_role")
        return (dr if dr in self.roles else next(iter(self.roles))), "default"

    def access(self, user_id, assigned=None):
        name, source = self.role_name(user_id, assigned)
        role = self.roles[name]
        feats = role_features(role)
        blocked = set(role.get("blocked_skills") or [])
        for feat, skills in SKILLS_BY_FEATURE.items():
            if not feats[feat]:
                blocked.update(skills)
        crit = [c for c in CRITERIA if c in (role.get("visible_criteria", CRITERIA) or [])]
        return {"role": name, "label": role.get("label", name), "description": role.get("description", ""),
                "source": source, "features": feats, "visible_criteria": crit,
                "blocked_skills": sorted(blocked)}

    def sanitized(self):
        """The effective config without anything secret (for exports and the API)."""
        return copy.deepcopy(self.data)


def feature_catalog():
    return [{"id": k, "label": v[0], "description": v[1], "admin_only": k in ADMIN_ONLY} for k, v in FEATURES.items()]


def criteria_catalog():
    return [{"id": c, "label": CRITERION_LABELS[c]} for c in CRITERIA]
