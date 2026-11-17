"""
Calls to the Anthropic API with a forced tool, so every answer is
structured JSON that can be validated; every raw response is archived.

Two modes, the same calls and the same archive:
  live   (default)  concurrent calls
  batch  (--batch)  Message Batches: cheaper, asynchronous; up to three
                    rounds, answers that need a retry go into the next round
                    (as live calls if only a few remain)
In both, the tool schema and system prompt are sent as a cached prefix
(config.PROMPT_CACHE_TTL), and an account problem stops the step.

Requires the anthropic package and ANTHROPIC_API_KEY (in .env or the shell).
"""
import datetime
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import anthropic
except ModuleNotFoundError:
    anthropic = None

import config


KEY_SOURCE = None


def _load_dotenv(path=config.path(".env")):
    """Read KEY=value lines from a local .env into the environment."""
    global KEY_SOURCE
    if os.environ.get("ANTHROPIC_API_KEY"):
        KEY_SOURCE = "shell environment"
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'").strip()
            existing = os.environ.get(key)
            if existing is None:
                os.environ[key] = val
                if key == "ANTHROPIC_API_KEY":
                    KEY_SOURCE = path
            elif existing != val and key == "ANTHROPIC_API_KEY":
                print(f"WARNING: ANTHROPIC_API_KEY is set in your shell "
                      f"(...{existing[-6:]}) and differs from {path} "
                      f"(...{val[-6:]}). The shell value is being used.\n"
                      f"         Run:  unset ANTHROPIC_API_KEY\n"
                      f"         then re-run, to use the key from {path}.")


_load_dotenv()

_client = None


def client():
    global _client
    if _client is None:
        if anthropic is None:
            raise SystemExit(
                f"the anthropic package cannot be imported by this Python "
                f"({sys.executable}).\nActivate the project environment first:"
                f"\n  source .venv/bin/activate\nor run the script with "
                f".venv/bin/python. Nothing was sent to the API.")
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit(
                "ANTHROPIC_API_KEY not set.\n"
                "Create a file called .env in the project folder containing:\n"
                "  ANTHROPIC_API_KEY=sk-ant-...\n"
                "(.env is already in .gitignore, so it will not be committed.)")
        headers = {}
        ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")
        if ws:
            headers["anthropic-workspace-id"] = ws
        _client = anthropic.Anthropic(api_key=key,
                                      default_headers=headers or None)
    return _client


def _enum_problems(tool, data):
    """Values the LLM returned that are not in the schema's enum."""
    bad = []
    for key, spec in tool["input_schema"]["properties"].items():
        allowed = spec.get("enum")
        if not allowed or key not in data:
            continue
        val = data[key]
        if val is None and (None in allowed or "null" in spec.get("type", [])):
            continue
        if val not in allowed:
            bad.append((key, val))
    return bad


def _shape_problems(tool, data):
    """Where the tool input does not have the schema's shape."""
    schema = tool["input_schema"]
    bad = [f"{k} missing" for k in schema.get("required", []) if k not in data]
    for key, spec in schema["properties"].items():
        types = spec.get("type")
        types = types if isinstance(types, list) else [types]
        if "object" not in types or key not in data:
            continue
        val = data[key]
        if val is None and "null" in types:
            continue
        if not isinstance(val, dict):
            bad.append(f"{key} is {type(val).__name__}, not an object")
            continue
        bad += [f"{key}.{k} missing" for k in spec.get("required", [])
                if k not in val]
    return bad


def _dump(resp):
    """The full API response as plain JSON (every block, usage, stop reason)."""
    if hasattr(resp, "model_dump"):
        return resp.model_dump(mode="json")
    return {"content": [{"type": b.type, "input": getattr(b, "input", None)}
                        for b in resp.content]}


def _params(system, user, tool, model, max_tokens, ttl):
    """The request: tool schema and system prompt form the cached prefix."""
    cache = {"type": "ephemeral"}
    if ttl == "1h":
        cache["ttl"] = "1h"
    return dict(model=model, max_tokens=max_tokens,
                system=[{"type": "text", "text": system,
                         "cache_control": cache}],
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]})


ACCOUNT_ERRORS = {
    "not scoped to a workspace": (
        "Your API key is not scoped to a workspace.\n"
        "Either (a) create a workspace-scoped key in the Console "
        "(Settings -> API keys -> pick a workspace) and put it in "
        ".env, or (b) add this line to .env:\n"
        "  ANTHROPIC_WORKSPACE_ID=wrkspc_...\n"
        "(find the ID in the Console URL when the workspace is "
        "open, or under Settings -> Workspaces)"),
    "credit balance is too low": (
        "The Anthropic account has run out of credit (\"credit balance is too "
        "low\").\nTop it up in the Console (Settings -> Billing), then run "
        "this step again. The step stopped before writing anything, so no "
        "paper was recorded as excluded; rejected calls are not billed."),
}


def _account_error(msg):
    """Stop on an account problem."""
    for needle, text in ACCOUNT_ERRORS.items():
        if needle in msg:
            raise SystemExit(text)


def _judge(tool, block_input, raw, log, attempt, last):
    """Validate one tool answer."""
    shape = _shape_problems(tool, block_input)
    if shape:
        log(attempt=attempt, response=raw, shape_problems=shape)
        if not last:
            print(f"  malformed answer {shape}, retrying")
            return None, True
        return None, False                 # never pass a malformed answer on
    bad = _enum_problems(tool, block_input)
    log(attempt=attempt, response=raw,
        enum_problems=[f"{k}={v!r}" for k, v in bad])
    if bad and not last:
        print(f"  off-enum value {bad}, retrying")
        return None, True
    if bad:
        block_input["_enum_violation"] = "; ".join(
            f"{k}={v!r}" for k, v in bad)
    return block_input, False


def _tool_input(resp):
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    return None


def call_tool(system, user, tool, model=None, max_tokens=None, log=None,
              ttl=None):
    """One forced-tool call. Returns the tool-input dict, or None on failure.

    An invalid answer is asked for again (at most three answers in all); API
    errors are retried, four attempts in all.

    `log`, if given, is called once per attempt with the raw response or the
    error, so every answer the LLM gave, including retried ones, is kept.
    The raw response includes `usage`, so cache reads and writes are archived.
    """
    params = _params(system, user, tool, model or config.MODEL,
                     max_tokens or config.MAX_TOKENS,
                     ttl or config.PROMPT_CACHE_TTL)
    log = log or (lambda **kw: None)
    for attempt in range(4):
        try:
            resp = client().messages.create(**params)
            raw = _dump(resp)
            got = _tool_input(resp)
            if got is None:
                log(attempt=attempt, response=raw, error="no tool_use block")
                return None
            answer, retry = _judge(tool, got, raw, log, attempt, attempt >= 2)
            if retry:
                continue
            return answer
        except Exception as e:
            msg = str(e)
            log(attempt=attempt, error=f"{e.__class__.__name__}: {msg[:500]}")
            _account_error(msg)
            # retry transient API errors
            if anthropic is not None and not isinstance(
                    e, (anthropic.RateLimitError, anthropic.APIStatusError,
                        anthropic.APIConnectionError)):
                raise
            last = e
            time.sleep(2 ** attempt)
    raise last


def _fmt(sec):
    sec = int(max(sec, 0))
    return f"{sec // 60}:{sec % 60:02d}" if sec >= 60 else f"{sec}s"


class Progress:
    """Progress bar."""

    def __init__(self, total, label="", width=32):
        self.total, self.label, self.width = total, label, width
        self.done = self.errors = 0
        self.t0 = time.time()
        self.tty = sys.stderr.isatty()
        self.draw()

    def draw(self):
        frac = self.done / self.total if self.total else 1.0
        elapsed = time.time() - self.t0
        eta = (elapsed / self.done) * (self.total - self.done) if self.done else 0
        err = f"  {self.errors} failed" if self.errors else ""
        if self.tty:
            filled = int(self.width * frac)
            bar = "█" * filled + "░" * (self.width - filled)
            line = (f"  {self.label} |{bar}| {self.done}/{self.total} "
                    f"({frac:5.1%})  {_fmt(elapsed)} elapsed"
                    f"{'' if self.done == self.total else f', ~{_fmt(eta)} left'}"
                    f"  ${SPEND['usd']:.2f}{err}")
            self._w = max(getattr(self, "_w", 0), len(line))
            sys.stderr.write("\r" + line.ljust(self._w))
            sys.stderr.flush()
        elif self.done == self.total or self.done % max(self.total // 10, 1) == 0:
            print(f"  {self.label} {self.done}/{self.total} ({frac:.0%})"
                  f"  ${SPEND['usd']:.2f}{err}", flush=True)

    def tick(self, failed=False):
        self.done += 1
        self.errors += failed
        self.draw()

    def close(self):
        if self.tty:
            sys.stderr.write("\n")
            sys.stderr.flush()


def prompt_hash(system, tool, model=None):
    """The hash RawLog records for a pass's prompt (system, tool, model)."""
    prompt = {"model": model or config.MODEL, "max_tokens": config.MAX_TOKENS,
              "system": system, "tool": tool}
    blob = json.dumps(prompt, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def replay_calls(jobs, system, tool, raw_name, raw_dir, model=None):
    """The answers an earlier run of this pass gave, read back from its raw
    archive.

    For redoing what a step does after the LLM call (e.g. masking) without
    new LLM output. Refuses unless the archived prompt hash is this pass's
    current one and every job's archived user text is the job's own, so the
    answers are exactly the ones these inputs got. The archive is read only.
    """
    path = os.path.join(raw_dir, raw_name + ".jsonl")
    if not os.path.exists(path):
        raise SystemExit(f"nothing to replay: {path} does not exist")
    want = prompt_hash(system, tool, model)
    last, users = {}, {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec["prompt_sha256"] != want:
                raise SystemExit(f"cannot replay {path}: it was made with a "
                                 f"different prompt than the current one")
            last[rec["key"]] = rec              # last attempt decides
            users[rec["key"]] = rec["user"]
    out = {}
    for key, user in jobs:
        if key not in users:
            raise SystemExit(f"cannot replay {path}: no archived call for {key}")
        if users[key] != user:
            raise SystemExit(f"cannot replay {path}: the input for {key} has "
                             f"changed since it was archived")
        rec = last[key]
        # an accepted answer is the attempt _judge logged with enum_problems
        if "enum_problems" not in rec or not rec.get("response"):
            out[key] = None
            continue
        got = next((b.get("input") for b in rec["response"]["content"]
                    if b.get("type") == "tool_use"), None)
        if got is not None and rec["enum_problems"]:
            got["_enum_violation"] = "; ".join(rec["enum_problems"])
        out[key] = got
    print(f"  replayed {sum(v is not None for v in out.values())}/{len(jobs)} "
          f"answers from {path}")
    return out


# Rough live tally of what the current pass has cost.
SPEND = {"usd": 0.0}


def response_cost(response, batch=False):
    """Approx. dollars for one archived response, from its `usage` block."""
    u = (response or {}).get("usage") or {}
    pin = config.PRICE_INPUT_PER_MTOK / 1e6
    write = config.CACHE_WRITE_MULTIPLIER["1h" if batch else "5m"]
    usd = (u.get("input_tokens", 0) * pin
           + (u.get("cache_creation_input_tokens") or 0) * pin * write
           + (u.get("cache_read_input_tokens") or 0) * pin
           * config.CACHE_READ_MULTIPLIER
           + u.get("output_tokens", 0) * config.PRICE_OUTPUT_PER_MTOK / 1e6)
    return usd * (config.BATCH_MULTIPLIER if batch else 1.0)


class RawLog:
    """Archive of one pass: the exact prompt, and every raw response.

      <raw_dir>/<name>_prompt.json   system prompt, tool schema, model, hash
      <raw_dir>/<name>.jsonl         one line per attempt: key, user text,
                                     raw response or error
    raw_dir is the run's raw folder (data/runs/<run>/raw).
    """

    def __init__(self, name, system, tool, model, raw_dir):
        os.makedirs(raw_dir, exist_ok=True)
        prompt = {"model": model, "max_tokens": config.MAX_TOKENS,
                  "system": system, "tool": tool}
        self.sha = prompt_hash(system, tool, model)
        base = os.path.join(raw_dir, name)
        with open(base + "_prompt.json", "w", encoding="utf-8") as f:
            json.dump(dict(prompt, sha256=self.sha), f, ensure_ascii=False,
                      indent=2)
        self.path = base + ".jsonl"
        self.f = open(self.path, "w", encoding="utf-8")
        self.lock = threading.Lock()

    def writer(self, key, user):
        def log(**kw):
            rec = dict(key=key, time=datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec="seconds"),
                prompt_sha256=self.sha, user=user, **kw)
            line = json.dumps(rec, ensure_ascii=False)
            usd = response_cost(kw.get("response"), "batch_id" in kw)
            with self.lock:
                SPEND["usd"] += usd
                self.f.write(line + "\n")
                self.f.flush()
        return log

    def close(self):
        self.f.close()


def map_calls(jobs, system, tool, raw_name, raw_dir, model=None, label="calls",
              batch=False):
    """Run many forced-tool calls, live (concurrently, with a progress bar) or
    as Message Batches (`batch=True`). Returns dict key -> tool-input dict.
    raw_name, raw_dir: every prompt and raw response is archived under this
    name in the run's raw folder.
    An account problem stops the whole step (SystemExit).
    """
    client()          # fail here, before work is queued
    SPEND["usd"] = 0.0
    raw = RawLog(raw_name, system, tool, model or config.MODEL, raw_dir)
    try:
        if batch:
            results = _map_batch(jobs, system, tool, raw, model, label)
        else:
            results = _map_live(jobs, system, tool, raw, model, label)
    finally:
        raw.close()
    print(f"  raw responses written to {raw.path}")
    print(f"  cost of this pass: ~${SPEND['usd']:.2f} (from the responses' "
          f"token counts, at list prices)")
    return results


def _map_live(jobs, system, tool, raw, model, label):
    results, problems = {}, []
    bar = Progress(len(jobs), label)
    ex = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENCY)
    futs = {ex.submit(call_tool, system, user, tool, model,
                      log=raw.writer(key, user)): key
            for key, user in jobs}
    try:
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                results[key] = fut.result()
                bar.tick()
            except Exception as e:
                results[key] = None
                problems.append((key, str(e)[:160]))
                bar.tick(failed=True)
    except SystemExit:
        # an account problem: stop now
        ex.shutdown(wait=False, cancel_futures=True)
        bar.close()
        raise
    ex.shutdown()
    bar.close()
    _report(problems)
    return results


def _report(problems):
    if problems:
        print(f"  {len(problems)} call(s) failed:")
        for key, msg in problems[:5]:
            print(f"    {key}: {msg}")
        if len(problems) > 5:
            print(f"    ... and {len(problems) - 5} more")


def _map_batch(jobs, system, tool, raw, model, label):
    """The jobs as Message Batches, in up to three rounds: answers that need
    a retry (malformed, off-enum) and errored or expired requests go into the
    next round, or are retried as live calls if few remain
    (config.BATCH_RETRY_LIVE)."""
    model = model or config.MODEL
    pending = {f"r{i}": (key, user) for i, (key, user) in enumerate(jobs)}
    results = {key: None for key, _ in jobs}
    # Submitted batch ids are saved next to the raw archive, so a rerun of the
    # step (after a crash) reattaches to the batches already submitted for
    # these exact jobs.
    state_path = raw.path[:-len(".jsonl")] + "_batches.json"
    sig = hashlib.sha256(json.dumps([raw.sha, jobs]).encode("utf-8")).hexdigest()
    state = {"jobs_sha256": sig, "batches": {}}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("jobs_sha256") == sig:
            state = saved
            print(f"  {label}: reattaching to {len(state['batches'])} "
                  f"batch(es) already submitted")
    for attempt in range(3):
        if not pending:
            break
        if attempt and len(pending) <= config.BATCH_RETRY_LIVE:
            # for a handful of retries, live calls are used
            print(f"  {label}: {len(pending)} retr{'y' if len(pending) == 1 else 'ies'} "
                  f"as live calls")
            for key, user in pending.values():
                write = raw.writer(key, user)
                results[key] = call_tool(
                    system, user, tool, model, log=lambda _w=write, **kw:
                    _w(mode="live retry", **kw), ttl="1h")
            pending = {}
            break
        last = attempt == 2
        ids = list(pending)
        retry = {}
        for start in range(0, len(ids), config.BATCH_CHUNK):
            chunk = ids[start:start + config.BATCH_CHUNK]
            requests = [{"custom_id": cid,
                         "params": _params(system, pending[cid][1], tool, model,
                                           config.MAX_TOKENS, "1h")}
                        for cid in chunk]
            slot = f"{attempt}:{start}"
            saved = state["batches"].get(slot)
            reattached = bool(saved and saved["custom_ids"] == chunk)
            if reattached:
                bid = saved["id"]
            else:
                try:
                    bid = client().messages.batches.create(
                        requests=requests).id
                except Exception as e:
                    _account_error(str(e))
                    raise
                state["batches"][slot] = {"id": bid, "custom_ids": chunk}
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=1)
            print(f"  {label} batch {bid}: {len(chunk)} requests "
                  f"(round {attempt + 1})")
            _wait(bid, label)
            entries = _patient(
                lambda: list(client().messages.batches.results(bid)), label)
            for entry in entries:
                key, user = pending[entry.custom_id]
                write = raw.writer(key, user)

                def log(_w=write, _b=bid, **kw):    # every line names its batch
                    _w(batch_id=_b, **kw)
                kind = entry.result.type
                if kind == "succeeded":
                    msg = entry.result.message
                    got = _tool_input(msg)
                    if got is None:
                        log(attempt=attempt, response=_dump(msg),
                            error="no tool_use block")
                        continue
                    answer, again = _judge(tool, got, _dump(msg), log,
                                           attempt, last)
                    if again:
                        retry[entry.custom_id] = (key, user)
                    else:
                        results[key] = answer
                else:                       # errored, expired or canceled
                    err = getattr(entry.result, "error", None)
                    text = str(err) if err is not None else kind
                    log(attempt=attempt, error=f"batch {kind}: {text[:500]}")
                    # an account error in a batch submitted by an earlier,
                    # stopped run is history (e.g. credit since topped up):
                    # retry it in the next round instead of stopping again
                    if not reattached:
                        _account_error(text)
                    if not last:
                        retry[entry.custom_id] = (key, user)
        pending = retry
    failed = [k for k, v in results.items() if v is None]
    if failed:
        print(f"  {len(failed)} request(s) without a usable answer after "
              f"3 rounds")
    return results


def _patient(fn, label, hours=6):
    """Call fn, retrying lost connections with growing pauses. The batch
    keeps running on the server meanwhile. Gives up after `hours`; rerunning
    the step then reattaches."""
    delay, t0 = 15, time.time()
    while True:
        try:
            return fn()
        except Exception as e:
            transient = anthropic is not None and isinstance(
                e, (anthropic.APIConnectionError, anthropic.RateLimitError,
                    anthropic.InternalServerError))
            if not transient or time.time() - t0 > hours * 3600:
                raise
            print(f"  {label}: {e.__class__.__name__}, retrying in "
                  f"{delay}s (the batch keeps running on the server)",
                  flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 300)


def _wait(bid, label):
    """Poll a batch until it has ended, printing its counts."""
    t0 = time.time()
    while True:
        b = _patient(lambda: client().messages.batches.retrieve(bid), label)
        c = b.request_counts
        print(f"  {label} {bid}: {b.processing_status}, "
              f"{c.succeeded} succeeded, {c.errored} errored, "
              f"{c.expired} expired, {c.processing} processing "
              f"({_fmt(time.time() - t0)})", flush=True)
        if b.processing_status == "ended":
            return
        time.sleep(config.BATCH_POLL_SECONDS)


