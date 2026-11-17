"""
OpenAlex client: GET with the API key and retries, a count, a server-side
random sample, full paging, and an abstract's text.
"""
import time

import requests

import config

WORKS = "https://api.openalex.org/works"


class BudgetExhausted(SystemExit):
    """The daily budget is spent: stops the step instead of retrying."""
    pass


def reconstruct_abstract(inv: dict | None) -> str | None:
    """Plain text from OpenAlex's abstract_inverted_index (word -> positions)."""
    if not inv:
        return None
    positions = [(pos, word) for word, poss in inv.items() for pos in poss]
    positions.sort()
    return " ".join(w for _, w in positions)


def _auth(params):
    """The parameters plus the API key, if one is set."""
    p = dict(params)
    if config.OPENALEX_API_KEY:
        p["api_key"] = config.OPENALEX_API_KEY
    return p


def _budget_message(r):
    reset = r.headers.get("X-RateLimit-Reset") or r.headers.get("Retry-After")
    try:
        hrs = f"{float(reset) / 3600:.1f} h"
    except (TypeError, ValueError):
        hrs = "at midnight UTC"
    keyed = bool(config.OPENALEX_API_KEY)
    return (f"\nOpenAlex daily budget used up; it resets in {hrs}.\n"
            + ("" if keyed else
               "No API key: add OPENALEX_API_KEY to .env "
               "(free at openalex.org/settings/api).\n")
            + "Run the step again later.")


def get(url, params, tries=None):
    """GET, retried on 429 and 5xx (waiting per Retry-After, capped); stops
    if the daily budget is spent."""
    tries = tries or config.OPENALEX_RETRY_TRIES
    delay = config.OPENALEX_RETRY_BASE
    params = _auth(params)
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, timeout=90)
        except requests.RequestException as e:
            if attempt == tries - 1:
                raise
            print(f"    network error ({e.__class__.__name__}), "
                  f"retrying in {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if r.status_code == 429:
            # a spent daily budget (not a burst limit) stops the step
            remaining = r.headers.get("X-RateLimit-Remaining")
            ra = r.headers.get("Retry-After")
            try:
                long_wait = float(ra) > config.OPENALEX_MAX_WAIT
            except (TypeError, ValueError):
                long_wait = False
            if remaining == "0" or long_wait:
                raise BudgetExhausted(_budget_message(r))
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == tries - 1:
                r.raise_for_status()
            wait = delay
            ra = r.headers.get("Retry-After")
            if ra:
                try:
                    wait = float(ra)
                except ValueError:
                    wait = delay  # e.g. an HTTP date
            wait = max(1.0, min(wait, config.OPENALEX_MAX_WAIT))
            print(f"    HTTP {r.status_code}, waiting {wait:.0f}s "
                  f"(attempt {attempt + 1}/{tries})")
            time.sleep(wait)
            delay = min(delay * 2, config.OPENALEX_MAX_WAIT)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError("unreachable")


def count(filt: str) -> int:
    """How many works match this filter? Uses meta.count."""
    data = get(WORKS, {"filter": filt, "per-page": 1}).json()
    return data["meta"]["count"]


def sample(filt: str, select: str, n: int, seed: int):
    """Random sample of n works, drawn by OpenAlex with the given seed."""
    got, page, per = 0, 1, config.OPENALEX_PER_PAGE
    while got < n:
        data = get(WORKS, {"filter": filt, "select": select, "sample": n,
                           "seed": seed, "per-page": per, "page": page}).json()
        # per-page stays the same on every page and the last page is
        # trimmed here: asking for a smaller last page repeats records
        res = data["results"][:n - got]
        if not res:
            break
        yield from res
        got += len(res)
        page += 1
        time.sleep(config.OPENALEX_PAUSE)


def pages(filt: str, select: str):
    """Every work matching the filter, by cursor paging."""
    cursor = "*"
    while cursor:
        data = get(WORKS, {"filter": filt, "select": select,
                           "per-page": config.OPENALEX_PER_PAGE,
                           "cursor": cursor}).json()
        yield from data["results"]
        cursor = data["meta"].get("next_cursor")
        time.sleep(config.OPENALEX_PAUSE)
