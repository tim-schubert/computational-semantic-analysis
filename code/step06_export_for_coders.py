"""
Step 06: Export the conclusions for the coders.

Writes extracted_review.txt (every paper's conclusion) and
coding_tool.html. The page shows the same masked statements the LLM scored,
scope and vocabulary as separate questions, shuffled and never adjacent;
its instructions are generated from prompts.py, so coders and the LLM read
the same definitions and examples. Spans that failed step 04's checks are
left out, as for the LLM. The coders' CSVs, combined into
<run>/human_coding.csv, are read by step 07.

Usage:  python code/step06_export_for_coders.py --run NAME [--max=N]
        --max=N: only N statements in the coding tool, a random subset
        balanced across fields
"""
import csv
import json
import random
from collections import Counter

import config
import prompts
import runs

import datetime

CODER_EMAIL = "you@example.org"
BUILD = datetime.datetime.now().strftime("build %d %b %H:%M")


def load_spans(run):
    with open(run.spans, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_review(spans, path):
    by_paper = {}
    for r in spans:
        by_paper.setdefault(r["paper_id"], {})[r["location"]] = r
    with open(path, "w", encoding="utf-8") as f:
        f.write("EXTRACTED CONCLUSIONS — read this before trusting any score.\n"
                "A conclusion marked [EXCLUDED] failed a check (not verbatim, "
                "too many sentences, the substance not named or not fully "
                "masked, or it does not say what the substance does) and is "
                "not scored.\n"
                "Check each paper's 'finding' line and record whether it is the "
                "right headline finding in finding_check.csv in this run's "
                "folder (copy data/templates/finding_check_TEMPLATE.csv).\n\n")
        for pid, locs in by_paper.items():
            r = locs[config.VOCAB_LOCATION]
            f.write("=" * 78 + f"\n{pid}   [{r['field']}]\n")
            f.write(f"  finding : {r.get('headline_finding', '')}\n")
            f.write(f"  assay   : {r.get('assay', '')}   "
                    f"evidence: {r.get('evidence_type', '')}\n\n")
            if r["is_na"] == "1":
                f.write("  conclusion: (none located)\n\n")
                continue
            flag = ("" if r.get("scorable", "1") == "1"
                    else f"  [EXCLUDED: {r.get('span_issue', '')}]")
            f.write(f"  conclusion{flag}\n      {r['span']}\n")
            if r.get("vocab_span") and r["vocab_span"] != r["span"]:
                f.write(f"      main sentence (vocabulary): {r['vocab_span']}\n")
            if r.get("does_clause"):
                f.write(f"      'does' check: {r['does_clause']}\n")
            f.write("\n")
    print(f"wrote {path} ({len(by_paper)} papers)")


def pick_statements(spans, n):
    """A random subset of n scorable statements, balanced across fields
    (round robin)."""
    ok = [r for r in spans if r["is_na"] != "1" and r["span"]
          and r.get("scorable", "1") == "1"]
    rng = random.Random(config.SAMPLE_SEED)
    by = {}
    for r in sorted(ok, key=lambda r: r["paper_id"]):
        by.setdefault(r["field"], []).append(r)
    for rows in by.values():
        rng.shuffle(rows)
    order = [f for f in config.FIELDS if f in by]
    out = []
    while len(out) < n and any(by.values()):
        for f in order:
            if by[f] and len(out) < n:
                out.append(by[f].pop())
    return out


def build_tasks(spans):
    """One task per statement and axis, shuffled; the two
    questions about one statement are never adjacent."""
    tasks = []
    for r in spans:
        if r["is_na"] == "1" or not r["span"]:
            continue
        if r.get("scorable", "1") != "1":
            continue
        sid = f'{r["paper_id"]}|{r["location"]}'
        # the same masked texts the LLM was given (step 04 masks once)
        axes = [("scope", r["span_masked"])]
        if r["location"] == config.VOCAB_LOCATION:
            axes.append(("vocabulary", r["vocab_span_masked"]))
        for axis, masked in axes:
            tasks.append({"tid": f"{sid}#{axis}", "sid": sid,
                          "axis": axis, "text": masked})
    rng = random.Random(config.SAMPLE_SEED)
    # shuffle, then push apart any pair that landed adjacent; repeat until
    # none is left (a swap can create a new neighbour)
    rng.shuffle(tasks)
    for _ in range(1000):
        clash = [i for i in range(len(tasks) - 1)
                 if tasks[i]["sid"] == tasks[i + 1]["sid"]]
        if not clash:
            break
        i = clash[0]
        j = rng.randrange(len(tasks))
        tasks[i + 1], tasks[j] = tasks[j], tasks[i + 1]
    return tasks


def axis_payload(axis):
    """The scale's definitions and rules as the coding tool needs them,
    from prompts.py."""
    return {
        "key": axis["key"], "title": axis["title"], "intro": axis["intro"],
        "columns": axis["columns"],
        "levels": [{"key": l["key"], "name": l["name"], "about": l["about"],
                    "definition": l["definition"], "examples": l["examples"]}
                   for l in axis["levels"]],
        "rules": [{"name": n, "text": t} for n, t in axis["rules"]],
    }


# The coding tool's page.
TEMPLATE = config.path("code/coding_tool_template.html")
PLACEHOLDERS = ("__TASKS__", "__SCOPE__", "__VOCAB__", "__EMAIL__",
                "__UNSURE__", "__MASK__", "__PREAMBLE__", "__BUILD__")


def write_tool(tasks, path):
    with open(TEMPLATE, encoding="utf-8", newline="") as f:
        page = f.read()
    missing = [p for p in PLACEHOLDERS if p not in page]
    assert not missing, f"coding_tool_template.html lacks {missing}"
    html = (page
            .replace("__TASKS__", json.dumps(tasks, ensure_ascii=False))
            .replace("__SCOPE__", json.dumps(axis_payload(prompts.SCOPE_AXIS),
                                             ensure_ascii=False))
            .replace("__VOCAB__", json.dumps(axis_payload(prompts.VOCAB_AXIS),
                                             ensure_ascii=False))
            .replace("__EMAIL__", CODER_EMAIL)
            .replace("__UNSURE__", json.dumps(prompts.UNSURE_POLICY))
            .replace("__MASK__", config.MASK)
            .replace("__PREAMBLE__", json.dumps(prompts.SHARED_PREAMBLE))
            .replace("__BUILD__", BUILD))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    n_spans = len({t["sid"] for t in tasks})
    n_vocab = sum(t["axis"] == "vocabulary" for t in tasks)
    print(f"wrote {path}  [{BUILD}]")
    print("  (page unchanged? reload with Cmd+Shift+R; the header shows the build)")
    print(f"  {n_spans} statements, {len(tasks)} questions")
    print(f"  coders send their CSV to {CODER_EMAIL}")


def main(run, max_statements=None):
    run.require(run.spans, "step 04")
    spans = load_spans(run)
    write_review(spans, run.review)
    coded = spans
    if max_statements:
        coded = pick_statements(coded, max_statements)
        print(f"coding tool: {len(coded)} statements, a random subset "
              f"balanced across fields: "
              f"{dict(Counter(r['field'] for r in coded))}")
    tasks = build_tasks(coded)
    write_tool(tasks, run.coding_tool)
    runs.record_step(run, "06", statements=len({t["sid"] for t in tasks}),
                     questions=len(tasks), max_statements=max_statements,
                     coded_ids=sorted({t["sid"] for t in tasks})
                     if max_statements else None)


if __name__ == "__main__":
    import sys
    run_ = runs.from_argv()
    cap = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--max=")]
    main(run_, int(cap[0]) if cap else None)
