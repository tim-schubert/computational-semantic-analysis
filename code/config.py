"""
Settings for all steps.

  - Inclusion: OpenAlex type 'article' with an abstract, published from
    2000-01-01; the journal must be in the SJR table.
"""

import sys as _sys

if _sys.version_info < (3, 10):
    raise SystemExit(
        f"this project needs Python 3.10 (see .python-version); this is "
        f"{_sys.version.split()[0]} at {_sys.executable}.\nActivate the project "
        f"environment first:  source .venv/bin/activate")

# --- project root ---
from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parent.parent


def path(rel):
    """Absolute path of `rel`, a path relative to the project root."""
    return str(ROOT / rel)


# --- corpus scope ---
YEAR_START = 2000                     # inclusive
YEAR_END = None                       # None = present

# placeholder that replaces the paper's own substance in the conclusion
MASK = "[substance X]"
# placeholder for the other three substances, if the conclusion names them
MASK_OTHER = "[substance Y]"

# Per field:
# - `search`: the OpenAlex title-and-abstract search term (the substance's name)
# - `mask`: (regex, replacement) pairs, applied case-insensitively and in
#   order. Receptor and transporter subtypes come first and absorb a preceding
#   substance name and a following "receptor", so "dopamine D2 receptor"
#   becomes "[substance X] receptor". Every field covers the same kinds of
#   name: the substance, its adjective, prefixed forms, receptor and
#   transporter subtypes and their gene symbols.
# - `leak`: word roots that must not survive masking; step 04 flags a span
#   that still contains one as unscorable.
_PRE = r"\b(hyper|hypo|anti|non|pro)-?"
FIELDS = {
    "OT": {
        "search": "oxytocin",
        "mask": [
            (r"\bOXTR\b", MASK + " receptor"),
            (r"\bOTR\b", MASK + " receptor"),
            (_PRE + r"oxytocin(?:-?ergic)?\b", r"\1-" + MASK),
            (r"\boxytocin(?:-?ergic)?\b", MASK),
            (r"\bOXT(?:-?ergic)?\b", MASK),
            (r"\bOT(?:-?ergic)?\b", MASK),
        ],
        "leak": ["oxytoc"],
    },
    "VP": {
        "search": "vasopressin",
        "mask": [
            (r"\b(?:(?:arginine[- ])?vasopressin\s+)?(?:V1[ab]R?|V2R?)"
             r"\s+receptors?\b", MASK + " receptor"),
            (r"\bV1[ab]R?\b", MASK + " receptor"),
            (r"\bV2R\b", MASK + " receptor"),
            (r"\bAVPR(?:1[AB]|2)\b", MASK + " receptor"),
            (_PRE + r"vasopressin(?:-?ergic)?\b", r"\1-" + MASK),
            (r"\b(?:arginine[- ])?vasopressin(?:-?ergic)?\b", MASK),
            (r"\bAVP(?:-?ergic)?\b", MASK),
            (r"\bADH\b", MASK),
        ],
        "leak": ["vasopress"],
    },
    "DA": {
        "search": "dopamine",
        "mask": [
            (r"\bDRD\s?[1-5]\b", MASK + " receptor"),
            (r"\bSLC6A3\b", MASK + " transporter"),
            (r"\bD\s[1-5]\s?R\b", MASK + " receptor"),      # "D 3 R"
            # not "vitamin D3"
            (r"(?<!vitamin )\b(?:dopamine\s+)?D[1-5](?:R|-like)?"
             r"(?:\s+receptors?)?\b", MASK + " receptor"),
            (r"\bDAT\b", MASK + " transporter"),
            (r"\bdopamine\s+transporters?\b", MASK + " transporter"),
            (_PRE + r"dopamine(?:rgic)?\b", r"\1-" + MASK),
            (r"\bdopamine(?:rgic)?\b", MASK),
            (r"\bDA(?:-?ergic)?\b", MASK),
        ],
        "leak": ["dopamin"],
    },
    "5HT": {
        "search": "serotonin",
        "mask": [
            (r"\b5-?HTTLPR\b", MASK + " transporter gene"),
            (r"\bSLC6A4\b", MASK + " transporter"),
            (r"\b(?:5-?HTR|HTR)\s?[1-7][A-Z]?\b", MASK + " receptor"),
            (r"\b(?:serotonin\s+)?5-?HT[1-7][A-Za-z]?R?(?:\s+receptors?)?\b",
             MASK + " receptor"),
            (r"\b(?:5-?HTT|SERT)\b", MASK + " transporter"),
            (r"\bserotonin\s+transporters?\b", MASK + " transporter"),
            (_PRE + r"seroton(?:in)?(?:-?ergic)?\b", r"\1-" + MASK),
            (r"\bseroton(?:in)?(?:-?ergic)?\b", MASK),
            (r"\b5-hydroxytryptamine\b", MASK),
            (r"\b5-?HT(?:-?ergic)?\b", MASK),
        ],
        "leak": ["seroton", "5-ht", "5ht", "hydroxytryptamine"],
    },
}

# --- sampling ---
PILOT_FIELD = "OT"                    # step 01's field when none is given
PILOT_N = 30                          # step 01's draw size when none is given
SAMPLE_SEED = 20260924                # default seed (main run used --seed=20261013)
SAMPLE_OVERDRAW = 1.8                 # draw 80% extra

# --- journal criterion ---
# the SCImago Journal Rank table (a journal must be listed in it)
SJR_CSV = path("data/reference/scimagojr_2025_downloaded_24_sep_2026.csv")

# --- API keys ---
import os as _os


def _dotenv(path=path(".env")):
    """Load KEY=value lines from .env."""
    if not _os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        _os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_dotenv()


OPENALEX_API_KEY = _os.environ.get("OPENALEX_API_KEY", "")

# OpenAlex throttles bursts (HTTP 429), so requests are spaced out
OPENALEX_PER_PAGE = 100               # max allowed by API
OPENALEX_PAUSE = 1.5                  # seconds between pages
OPENALEX_RETRY_BASE = 8.0             # first backoff wait, doubles each retry
OPENALEX_RETRY_TRIES = 7
OPENALEX_MAX_WAIT = 90.0              # hard cap on any single retry sleep

# --- Anthropic ---
MODEL = "claude-sonnet-5"
MAX_TOKENS = 2048
BLIND_REPEATS = 3                     # for step 05: repeats per statement (mode is kept)
MAX_CONCURRENCY = 8                   # parallel calls in live mode
PROMPT_CACHE_TTL = "5m"               # prompt cache lifetime in live mode; batch mode uses "1h"
BATCH_CHUNK = 10000                   # requests per batch (--batch)
BATCH_RETRY_LIVE = 50                 # failed batch requests are retried live if there are this many or fewer
BATCH_POLL_SECONDS = 30

# --- what is measured ---
# location measured: the abstract's conclusion
LOCATIONS = ["absconc"]
VOCAB_LOCATION = "absconc"
# The conclusion may run over a few consecutive sentences (all about the
# headline finding); scope is scored across them, vocabulary on the one
# sentence stating the main conclusion.
CONCLUSION_MAX_SENTENCES = 3

# --- rough estimated pricing, for a live cost tally, no guarantees ---
PRICE_INPUT_PER_MTOK = 2.0
PRICE_OUTPUT_PER_MTOK = 10.0
CACHE_READ_MULTIPLIER = 0.10
BATCH_MULTIPLIER = 0.50
CACHE_WRITE_MULTIPLIER = {"5m": 1.25, "1h": 2.0}
