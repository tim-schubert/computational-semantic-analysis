"""
Run folders. A run reads and writes inside data/runs/<name>/ (the
study's run is `main`); each step is given the run and takes every path
from it.

Layout of data/runs/<name>/:
    corpus_manifest.csv   steps 01-02  the sampled papers
    papers/               step 02      one JSON per paper
    screen.csv            step 03
    spans.csv             step 04
    scores.csv            step 05
    extracted_review.txt, coding_tool.html   step 06 (for the coders)
    human_coding.csv      the coders' combined codes
    analysis/             step 07      the tables R reads
    flow.csv, exclusions.csv           exclusions, from every step
    raw/                  every fetched record, prompt and LLM response
    run_info.json         per step: time, git commit, packages, settings,
                          prompt hash
    commands.log          every command run_main.sh ran
"""
import datetime
import hashlib
import json
import os
import platform
import subprocess
import sys
from importlib import metadata

import config

RUNS_DIR = config.path("data/runs")


class Run:
    """All paths of one run."""

    def __init__(self, name: str):
        if (not name or os.sep in name or "/" in name or name.startswith(".")
                or name != name.strip()):
            raise SystemExit(f"invalid run name: {name!r}")
        self.name = name
        self.dir = os.path.join(RUNS_DIR, name)

        def at(*parts):
            return os.path.join(self.dir, *parts)

        self.manifest = at("corpus_manifest.csv")
        self.papers = at("papers")
        self.screen = at("screen.csv")
        self.spans = at("spans.csv")
        self.scores = at("scores.csv")
        self.analysis = at("analysis")
        self.flow = at("flow.csv")
        self.exclusions = at("exclusions.csv")
        self.raw = at("raw")
        self.review = at("extracted_review.txt")
        self.coding_tool = at("coding_tool.html")
        self.human_coding = at("human_coding.csv")
        self.finding_check = at("finding_check.csv")
        self.qc = at("qc")
        self.info = at("run_info.json")

    def __repr__(self):
        return f"Run({self.name!r})"

    def require(self, path: str, made_by: str) -> None:
        """Stop with a clear message if an input of this run is missing."""
        if not os.path.exists(path):
            raise SystemExit(f"[run {self.name}] missing {os.path.relpath(path, self.dir)}"
                             f" -- run {made_by} first (with --run {self.name})")


def existing() -> list[str]:
    """Names of the runs on disk."""
    if not os.path.isdir(RUNS_DIR):
        return []
    return sorted(d for d in os.listdir(RUNS_DIR)
                  if os.path.isdir(os.path.join(RUNS_DIR, d)))


def from_argv(argv: list[str] | None = None) -> "Run":
    """Take `--run NAME` (or `--run=NAME`) out of argv and return the Run.
    The run name is required and there is no default.
    """
    argv = sys.argv if argv is None else argv
    name = None
    for i, a in enumerate(argv):
        if a == "--run" and i + 1 < len(argv):
            name = argv[i + 1]
            del argv[i:i + 2]
            break
        if a.startswith("--run="):
            name = a.split("=", 1)[1]
            del argv[i]
            break
    if not name:
        have = ", ".join(existing()) or "none yet"
        raise SystemExit("this script needs --run NAME (e.g. --run main); "
                         f"runs on disk: {have}")
    return Run(name)


# ------------------------------------------------------------- JSON files
def read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------- run_info.json
# keys and local paths, not research settings, thus not recorded
_NOT_RECORDED = {"OPENALEX_API_KEY", "CONTACT_EMAIL", "ROOT"}


def settings() -> dict:
    """config.py's settings as recorded in run_info.json."""
    out = {}
    for k in sorted(vars(config)):
        if not k.isupper() or k in _NOT_RECORDED:
            continue
        v = getattr(config, k)
        if isinstance(v, str) and v.startswith(str(config.ROOT)):
            v = os.path.relpath(v, config.ROOT)
        try:
            json.dumps(v)
        except TypeError:
            v = repr(v)
        out[k] = v
    return out


def prompts_sha256() -> str:
    """One hash over every prompt and tool schema the LLM sees, to check that
    two runs used the same prompts."""
    import prompts
    blob = json.dumps({
        "screen": [prompts.SCREEN_SYSTEM, prompts.SCREEN_TOOL],
        "locate": [prompts.LOCATE_SYSTEM, prompts.LOCATE_TOOL],
        "does": [prompts.DOES_SYSTEM, prompts.DOES_TOOL],
        "scope": [prompts.SCOPE_SYSTEM, prompts.SCOPE_TOOL],
        "vocabulary": [prompts.VOCAB_SYSTEM, prompts.VOCAB_TOOL],
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git():
    """The commit the code ran from, and whether tracked files had changes."""
    def git(*args):
        return subprocess.run(["git", "-C", str(config.ROOT), *args],
                              capture_output=True, text=True, timeout=10)
    try:
        head = git("rev-parse", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    if head.returncode:
        return {"commit": None, "dirty": None}
    return {"commit": head.stdout.strip(), "dirty": bool(dirty.stdout.strip())}


def _environment():
    return {"python": platform.python_version(),
            "platform": platform.platform(),
            "packages": dict(sorted(
                (d.metadata["Name"].lower(), d.version)
                for d in metadata.distributions()))}


def record_step(run: Run, step: str, **details) -> None:
    """Add/replace this step's entry in the run's run_info.json."""
    info = {}
    if os.path.exists(run.info):
        with open(run.info, encoding="utf-8") as f:
            info = json.load(f)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    info.setdefault("run", run.name)
    info.setdefault("created", now)
    info["updated"] = now
    info.setdefault("steps", {})[step] = {
        "finished": now,
        "git": _git(),
        "model": config.MODEL,
        "prompts_sha256": prompts_sha256(),
        "details": details,
        "settings": settings(),
        "environment": _environment(),
    }
    os.makedirs(run.dir, exist_ok=True)
    with open(run.info, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
