"""
Step 05: Score the conclusions, blinded to the substance.

The LLM sees the masked text but no field, paper or location. Spans are
pooled across papers and fields and shuffled. Scope is scored on the whole
conclusion, vocabulary on the main sentence (determined in step 04), in
separate calls. Each is scored config.BLIND_REPEATS times; the modal code
is kept, and disagreement between repeats is flagged.

A paper is excluded if most repeats answer NA ("not a [substance X] does
Y statement") on either question. Spans that failed step 04's checks
(scorable=0) are not scored.

Usage:  python code/step05_blind_score.py --run NAME [--batch]
"""
import csv
import random
import sys
from collections import Counter

import config
import flow
import prompts
import runs
from anthropic_client import map_calls


def majority_true(flags):
    """True when most repeats flagged it."""
    flags = [bool(f) for f in flags if f is not None]
    return bool(flags) and sum(flags) * 2 > len(flags)


REASON = ("the scorer marked the conclusion as not saying what the "
          "substance does")


def split_not_does(answers):
    """Split one question's repeat answers: whether most are NA (not a
    "[substance X] does Y" statement), and the other answers, from which the
    code is taken."""
    answers = [a for a in answers if a is not None]
    marked = majority_true([a == prompts.NOT_DOES for a in answers])
    return marked, [a for a in answers if a != prompts.NOT_DOES]


def mode_rung(rungs):
    """The modal code, and whether the repeats disagree. A tie goes to the
    answer that came first (the repeats' order is the seeded shuffle of
    build_jobs, so in effect random)."""
    rungs = [r for r in rungs if r is not None]
    if not rungs:
        return None, True
    c = Counter(rungs)
    top = c.most_common()
    unstable = len(set(rungs)) > 1
    return top[0][0], unstable


def build_jobs(spans):
    """(scope jobs, vocabulary jobs): (key, masked text) only,
    BLIND_REPEATS times each, pooled and shuffled; key never reaches the
    LLM."""
    scope, vocab = [], []
    for rep in range(config.BLIND_REPEATS):
        for r in spans:
            key = f'{r["paper_id"]}|{r["location"]}#rep{rep}'
            scope.append((key, r["span_masked"]))
            if r["location"] == config.VOCAB_LOCATION:
                vocab.append((key, r["vocab_span_masked"]))
    rng = random.Random(20260922)
    rng.shuffle(scope)
    rng.shuffle(vocab)
    return scope, vocab


def main(run, batch=False):
    run.require(run.spans, "step 04")
    with open(run.spans, encoding="utf-8") as f:
        all_spans = list(csv.DictReader(f))
    spans = [r for r in all_spans
             if r["is_na"] == "0" and r["span"] and r.get("scorable", "1") == "1"]
    skipped = [r for r in all_spans
               if r["is_na"] == "0" and r.get("scorable", "1") != "1"]
    if skipped:
        why = Counter(r.get("span_issue", "?") for r in skipped)
        print(f"skipping {len(skipped)} span(s): {dict(why)}")

    scope_jobs, vocab_jobs = build_jobs(spans)
    jobs = scope_jobs

    n = len(jobs)
    print(f"blind scoring {n // config.BLIND_REPEATS} spans x "
          f"{config.BLIND_REPEATS} reps: scope {n} calls, vocabulary "
          f"{len(vocab_jobs)} calls ({config.VOCAB_LOCATION} only) ...")

    # separate calls, so neither answer is in context while the other is chosen
    out_scope = map_calls(jobs, prompts.SCOPE_SYSTEM, prompts.SCOPE_TOOL,
                          "step05_scope", run.raw, label="scope     ",
                          batch=batch)
    out_vocab = map_calls(vocab_jobs, prompts.VOCAB_SYSTEM, prompts.VOCAB_TOOL,
                          "step05_vocabulary", run.raw, label="vocabulary",
                          batch=batch)

    def gather(out):
        per = {}
        for jobkey, res in out.items():
            per.setdefault(jobkey.split("#rep")[0], []).append(res or {})
        return per

    ps, pv = gather(out_scope), gather(out_vocab)

    rows = []
    for key in ps:
        pid, loc = key.split("|")
        rungs = [r.get("rung") for r in ps[key]]
        vocabs = [r.get("vocabulary") for r in pv.get(key, [])]
        s_na, s_rest = split_not_does(rungs)
        v_na, v_rest = split_not_does(vocabs)
        # the code is the mode of the repeats that gave one; instability is
        # over all repeats, NA included
        rung = mode_rung(s_rest)[0]
        vocab = mode_rung(v_rest)[0]
        unstable = len({r for r in rungs if r is not None}) > 1
        v_unstable = len({v for v in vocabs if v is not None}) > 1
        if key not in pv:                # not the conclusion: no vocabulary
            vocab, v_unstable = "", ""
        fs = next((r for r in ps[key] if r), {})
        fv = next((r for r in pv.get(key, []) if r), {})
        rows.append({"paper_id": pid, "location": loc,
                     "scope": rung, "vocabulary": vocab,
                     "scope_quote": fs.get("justification_quote", ""),
                     # majority of repeats; disagreement is in *_unstable
                     "scope_unsure": int(majority_true(
                         [r.get("uncertain") for r in ps[key] if r])),
                     "vocabulary_unsure": int(majority_true(
                         [r.get("uncertain") for r in pv[key] if r]))
                         if key in pv else "",
                     # NA ("not a '[substance X] does Y' statement") in most
                     # repeats; either question excludes the paper (below)
                     "scope_not_scorable": int(s_na),
                     "vocabulary_not_scorable": int(v_na)
                         if key in pv else "",
                     "scope_alt": fs.get("alt_rung")
                                  if fs.get("alt_rung") is not None else "",
                     "vocabulary_alt": fv.get("alt_vocabulary") or "",
                     "functional_label": fv.get("functional_label") or "",
                     "operation": fv.get("operation") or "",
                     "reps": ";".join(str(r) for r in rungs),
                     "vocab_reps": ";".join(str(v) for v in vocabs),
                     "scoring_unstable": int(unstable),
                     "vocab_unstable": int(v_unstable) if key in pv else ""})

    if not rows:
        raise SystemExit(f"no span was scored: every LLM call failed -- see the "
                         f"errors above and {run.raw}")
    with open(run.scores, "w", newline="", encoding="utf-8") as f:
        wri = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wri.writeheader()
        wri.writerows(rows)
    viol = sum(1 for d in list(out_scope.values()) + list(out_vocab.values())
               if d and d.get("_enum_violation"))
    if viol:
        print(f"  {viol} response(s) returned an off-enum value after "
              f"retries; see scores.csv")
    ns = sum(r["scoring_unstable"] for r in rows)
    nv = sum(r["vocab_unstable"] or 0 for r in rows)
    us = sum(r["scope_unsure"] for r in rows)
    uv = sum(r["vocabulary_unsure"] or 0 for r in rows)
    print(f"wrote {len(rows)} scores")
    print(f"  self-reported unsure  : scope {us}, vocabulary {uv}"
          f"   (majority of repeats; comparable with a coder's tick)")
    print(f"  unstable across repeats: scope {ns}, vocabulary {nv}"
          f"   (the repeats gave different codes)")
    print("  both are flagged per span in scores.csv")

    # the papers step 04 kept
    kept = sorted({r["paper_id"] for r in all_spans
                   if r.get("paper_excluded", "0") != "1"})
    out = sorted({r["paper_id"] for r in rows
                  if r["scope_not_scorable"] == 1
                  or r["vocabulary_not_scorable"] == 1})
    print(f"  {REASON} (majority of repeats): {len(out)} paper(s) excluded")
    ledger = flow.Ledger(run)
    ledger.record("05", "scorer: statement says what the substance does",
                  len(kept), len(kept) - len(out), REASON)
    ledger.exclude(out, "05", REASON)
    runs.record_step(run, "05", spans_scored=len(rows),
                     not_scorable_excluded=len(out),
                     repeats=config.BLIND_REPEATS, batch=batch)


if __name__ == "__main__":
    main(runs.from_argv(), batch="--batch" in sys.argv)
