"""
Step 04: Locate and mask the conclusion.

Two LLM calls per screened paper:
  1. LOCATE (title + abstract): the conclusion about the headline finding,
     verbatim (up to config.CONCLUSION_MAX_SENTENCES consecutive sentences;
     scope is scored on all of them), its main sentence (`vocab_span`,
     scored on vocabulary), assay and evidence type.
  2. DOES (substance + conclusion only): does the conclusion say what the
     substance does, i.e. attribute a function ("[substance X] does Y")?
A paper is excluded if its conclusion does not name the substance or does
not attribute a function, or if a call returns nothing.

Masking: the field's substance becomes config.MASK, including
abbreviations the abstract defines for it, and the other study substances
become config.MASK_OTHER. Steps 05 and 06 use this text.

Each span is checked (verbatim, sentence limit, main sentence, substance
named and fully masked); a failure is recorded in span_issue with
scorable=0. A potential leading section label is removed first.

Usage:  python code/step04_locate.py --run NAME [--batch | --replay]
"""
import csv
import os
import re
import sys
from collections import Counter

import config
import flow
import text
import prompts
import runs
from anthropic_client import map_calls, replay_calls


NOT_NAMED = "the conclusion does not name the substance"
NOT_DOES = "the conclusion does not say what the substance does"
NO_DOES = "the 'does' check returned no answer"


def mask_patterns(paper):
    """Mask patterns for this paper, in order: abbreviations it defines for
    its substance and the field's patterns (-> [substance X]), then the
    other substances' (-> [substance Y]); and the abbreviations."""
    field = paper["meta"]["field"]
    own = config.FIELDS[field]["mask"]
    source = f'{paper["title"]}\n{paper["abstract"]}'
    abbrevs = text.substance_abbreviations(source, own)
    others = [(pat, repl.replace(config.MASK, config.MASK_OTHER))
              for f, d in config.FIELDS.items() if f != field
              for pat, repl in d["mask"]]
    return list(abbrevs.values()) + own + others, abbrevs


def main_sentence_problems(main, span):
    """Why the main sentence cannot be scored on vocabulary ([] if it can)."""
    if not main:
        return ["main sentence missing"]
    out = []
    if text.sentence_count(main) > 1:
        out.append("main sentence is not one sentence")
    if not text.is_verbatim(main, span)[0]:
        out.append("main sentence not part of the conclusion")
    return out


def rows_for_paper(paper, res, does=None):
    """The spans.csv row for one paper, given the LOCATE answer and the DOES
    answer ({"does": bool, "clause": str}; None when not checked; "failed"
    when the DOES call returned nothing)."""
    meta = paper["meta"]
    fdef = config.FIELDS[meta["field"]]
    patterns, abbrevs = mask_patterns(paper)
    loc = config.VOCAB_LOCATION
    span = (res.get(loc) or "").strip()
    cut = text.leading_label_end(span)
    span = span[cut:].strip()
    verbatim, sim = (text.is_verbatim(span, paper["abstract"]) if span
                     else (True, 1.0))
    masked = text.mask_entity(span, patterns)
    leaks = text.mask_leaks(masked, fdef["leak"]) if span else []
    n_sent = text.sentence_count(span) if span else 0
    has_agent = text.names_agent(masked, config.MASK) if span else True
    problems = []
    if span and not verbatim:
        problems.append("not verbatim")
    if n_sent > config.CONCLUSION_MAX_SENTENCES:
        problems.append(f"{n_sent} sentences")
    if span and not has_agent:
        problems.append("substance not named")
    if leaks:
        problems.append("substance not fully masked")
    main = main_masked = ""
    if span:
        # a one-sentence conclusion is its own main sentence, by definition;
        # the LLM names the main sentence only when there are several
        main = (span if n_sent == 1 else
                (res.get(prompts.MAIN_KEY) or "").strip())
        main = main[text.leading_label_end(main):].strip()
        main_masked = text.mask_entity(main, patterns)
        problems += main_sentence_problems(main, span)
        if main and not text.names_agent(main_masked, config.MASK):
            problems.append("main sentence does not name the substance")
    # eligibility: such a paper is excluded (and recorded as excluded)
    named = bool(span) and has_agent
    reason = ("" if named and (does is None or
                               (does != "failed" and does["does"]))
              else NOT_NAMED if not named
              else NO_DOES if does == "failed" else NOT_DOES)
    if reason and span:
        problems.append("paper excluded: " + reason)
    checked = isinstance(does, dict)
    return [{
        "paper_id": meta["paper_id"], "field": meta["field"],
        "location": loc,
        "span": span, "is_na": 0 if span else 1,
        "span_masked": masked or "",
        # vocabulary is scored on this sentence
        "vocab_span": main,
        "vocab_span_masked": main_masked or "",
        "masked_abbreviations": "; ".join(
            f"{short} = {repl}" for short, (pat, repl) in abbrevs.items()
            if span and re.search(pat, span)),
        "label_removed": int(bool(cut)),
        "verbatim": int(verbatim),
        "verbatim_similarity": round(sim, 3),
        "n_sentences": n_sent,
        "names_agent": int(has_agent),
        "substance_does": int(does["does"]) if checked else "",
        "does_clause": does.get("clause", "") if checked else "",
        "paper_excluded": int(bool(reason)),
        "paper_exclusion": reason,
        "scorable": int(bool(span) and not problems),
        "span_issue": "; ".join(problems),
        "headline_finding": res.get("headline_finding", ""),
        "assay": res.get("assay") or "",
        "evidence_type": res.get("evidence_type", "unclear"),
        # from step 02, not the LLM; descriptive only
        "abstract_structured": int(meta["abstract_structured"] == "yes"),
        "title_abstract_mismatch": int(bool(
            res.get("title_abstract_mismatch"))),
        "notes": res.get("notes") or "",
    }]


def main(run, batch=False, replay=False):
    run.require(run.screen, "step 03")

    def calls(jobs, system, tool, name, label):
        if replay:
            return replay_calls(jobs, system, tool, name, run.raw)
        return map_calls(jobs, system, tool, name, run.raw, label=label,
                         batch=batch)

    ledger = flow.Ledger(run)
    with open(run.screen, encoding="utf-8") as f:
        screened = [r for r in csv.DictReader(f) if r["eligible"] == "1"]
    if not screened:
        raise SystemExit(f"no eligible papers - check {run.screen}")

    papers = {}
    for r in screened:
        p = runs.read_json(os.path.join(run.papers,
                                       f"{r['paper_id']}.json"))
        papers[r["paper_id"]] = p

    print(f"locating the conclusion in {len(papers)} papers ...")
    out = calls(
        [(pid, prompts.render_paper(p)) for pid, p in papers.items()],
        prompts.LOCATE_SYSTEM, prompts.LOCATE_TOOL, "step04_locate", "locate")
    failed = [pid for pid in papers if not out.get(pid)]

    # DOES: for every conclusion that names the substance, one short call on
    # the conclusion alone (unmasked: the check needs to know the substance)
    first = {pid: rows_for_paper(papers[pid], out[pid])[0]
             for pid in papers if out.get(pid)}
    does_jobs = [(pid, prompts.render_conclusion(
                     config.FIELDS[r["field"]]["search"], r["span"]))
                 for pid, r in first.items() if r["paper_exclusion"] == ""]
    print(f"checking whether {len(does_jobs)} conclusions say what the "
          f"substance does ...")
    does = (calls(does_jobs, prompts.DOES_SYSTEM, prompts.DOES_TOOL,
                  "step04_does", "does")
            if does_jobs else {})

    rows = []
    for pid in first:
        d = does.get(pid) or ("failed" if pid in dict(does_jobs) else None)
        rows.extend(rows_for_paper(papers[pid], out[pid], d))

    if not rows:
        raise SystemExit(f"no claim was located: every LLM call failed -- see the "
                         f"errors above and {run.raw}")
    with open(run.spans, "w", newline="", encoding="utf-8") as f:
        wri = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wri.writeheader()
        wri.writerows(rows)

    n_papers = len({r["paper_id"] for r in rows})
    ledger.record("04", "claim located", len(papers), n_papers,
                "no headline finding could be extracted")
    ledger.exclude(failed, "04", "no headline finding could be extracted")
    n_in = n_papers
    for label, reason in (("conclusion names the substance", NOT_NAMED),
                          ("'does' check answered", NO_DOES),
                          ("conclusion says what the substance does",
                           NOT_DOES)):
        out = sorted({r["paper_id"] for r in rows
                      if r["paper_exclusion"] == reason})
        ledger.record("04", label, n_in, n_in - len(out), reason)
        ledger.exclude(out, "04", reason)
        n_in -= len(out)

    head = rows
    stated = [r for r in rows if not r["is_na"]]
    bad = [r for r in stated if not r["scorable"]]
    print(f"\nwrote {len(rows)} conclusions: {len(stated) - len(bad)} "
          f"scorable of {len(stated)} stated")
    if bad:
        reasons = Counter(p for r in bad for p in r["span_issue"].split("; "))
        print(f"\n  {len(bad)} span(s) excluded from scoring:")
        for reason, n in reasons.most_common():
            print(f"     {n:>3}  {reason}")
        for r in bad[:4]:
            print(f"     - {r['paper_id']}/{r['location']} [{r['span_issue']}]")
            print(f"       {r['span'][:105]}")
        print("     (all are in spans.csv with scorable=0; step 05 skips them)")
    else:
        print("  all conclusions verbatim, within their sentence limit, name "
              "the substance, mask cleanly, and say what it does")
    n_multi = sum(r["n_sentences"] > 1 for r in head)
    if n_multi:
        print(f"  {n_multi} conclusion(s) run over several sentences; "
              f"vocabulary is scored on their main sentence")
    n_abbr = sum(bool(r["masked_abbreviations"]) for r in rows)
    if n_abbr:
        print(f"  {n_abbr} span(s) name the substance by an abbreviation the "
              f"abstract defines (masked; see masked_abbreviations)")
    n_cut = sum(r["label_removed"] for r in rows)
    if n_cut:
        print(f"  section label removed from the start of {n_cut} span(s)")
    print("evidence_type:", dict(Counter(r["evidence_type"] for r in head)))
    ledger.summary("04")
    runs.record_step(run, "04", papers=len(papers), located=n_papers,
                     spans_stated=len(stated), spans_scorable=len(stated) - len(bad),
                     batch=batch, replay=replay)


if __name__ == "__main__":
    main(runs.from_argv(), batch="--batch" in sys.argv,
         replay="--replay" in sys.argv)
