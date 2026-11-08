"""Configuration from environment variables, optionally overlaid by ~/.refwarden/config.json.

Nothing here is vendor-specific: every source and model is optional, and the tool degrades
to what it can reach, recording what it skipped in the run's coverage block.
"""
import json
import os
from dataclasses import dataclass, field, asdict

def _env(*names, default=""):
    """First non-empty variable; REFWARDEN_X also accepts the pre-0.4 name CITETRAIL_X."""
    for n in names:
        for m in (n, "CITETRAIL_" + n[len("REFWARDEN_"):]) if n.startswith("REFWARDEN_") else (n,):
            v = os.environ.get(m)
            if v:
                return v
    return default


def _home():
    home = os.path.expanduser(_env("REFWARDEN_HOME", default="~/.refwarden"))
    old = os.path.expanduser("~/.citetrail")   # data from versions before 0.4 (named citetrail)
    if not _env("REFWARDEN_HOME") and not os.path.exists(home) and os.path.exists(old):
        return old
    return home


HOME = _home()



def _default_db():
    new, old = os.path.join(HOME, "refwarden.db"), os.path.join(HOME, "citetrail.db")
    return old if not os.path.exists(new) and os.path.exists(old) else new


@dataclass
class Config:
    mailto: str = ""                  # polite-pool contact for Crossref / Europe PMC user agent
    openalex_key: str = ""            # OpenAlex requires a (free) key since Feb 2026
    s2_key: str = ""                  # Semantic Scholar key is optional; raises rate limits
    db_path: str = ""
    cache_days: float = 14.0
    offline: bool = False             # serve from cache only
    llm_provider: str = "none"        # none | openai | anthropic
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_effort: str = ""              # optional; sent only to providers that accept it
    token: str = ""                   # optional bearer token for `serve` on a shared host
    extra: dict = field(default_factory=dict)

    def public(self):
        d = asdict(self)
        for k in ("openalex_key", "s2_key", "llm_api_key", "token"):
            d[k] = bool(d[k])
        return d


def load_config(**overrides):
    cfg = Config(
        mailto=_env("REFWARDEN_MAILTO"),
        openalex_key=_env("OPENALEX_API_KEY", "REFWARDEN_OPENALEX_KEY"),
        s2_key=_env("S2_API_KEY", "SEMANTIC_SCHOLAR_API_KEY"),
        db_path=_env("REFWARDEN_DB", default=_default_db()),
        cache_days=float(_env("REFWARDEN_CACHE_DAYS", default="14")),
        offline=_env("REFWARDEN_OFFLINE") in ("1", "true", "yes"),
        llm_provider=_env("REFWARDEN_LLM", default="none").lower(),
        llm_base_url=_env("REFWARDEN_LLM_BASE_URL"),
        llm_model=_env("REFWARDEN_LLM_MODEL"),
        llm_api_key=_env("REFWARDEN_LLM_API_KEY"),
        llm_effort=_env("REFWARDEN_LLM_EFFORT"),
        token=_env("REFWARDEN_TOKEN"),
    )
    path = os.path.join(HOME, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for k, v in json.load(f).items():
                if hasattr(cfg, k) and v not in (None, ""):
                    setattr(cfg, k, v)
    for k, v in overrides.items():
        if v is not None:
            setattr(cfg, k, v)
    if not cfg.llm_api_key:
        if cfg.llm_provider == "anthropic":
            cfg.llm_api_key = _env("ANTHROPIC_API_KEY")
        elif cfg.llm_provider == "openai":
            cfg.llm_api_key = _env("OPENAI_API_KEY")
    return cfg
