"""
Step 07: Join, check and export tables.

Joins what steps 01-05 produced into the analysis tables in
<run>/analysis/ (spans, papers, flow, exclusions, levels, schema, and
agreement once <run>/human_coding.csv exists; columns are described in
the deposit's data dictionary).

Usage:  python code/step07_join_and_checks.py --run NAME
"""
import csv
import os
from collections import defaultdict

import config
import flow
import prompts
import runs

# ------------------------------------------------------------------- schema
SCHEMA = {
    "spans": [
        ("paper_id", "character"), ("work_id", "character"),
        ("field", "character"), ("location", "character"),
        ("abstract_structured", "integer"),
        ("year", "integer"), ("journal", "character"), ("issn", "character"),
        ("sjr_quartile", "character"), ("cited_by_count", "integer"),
        ("evidence_type", "character"), ("assay", "character"),
        ("is_na", "integer"), ("scorable", "integer"),
        ("span_issue", "character"), ("label_removed", "integer"),
        ("substance_does", "integer"), ("does_clause", "character"),
        ("paper_excluded", "integer"), ("paper_exclusion", "character"),
        ("span", "character"), ("n_sentences", "integer"),
        ("vocab_span", "character"),
        ("scope", "integer"), ("scope_quote", "character"),
        ("scope_unsure", "integer"), ("scope_alt", "character"),
        ("scope_unstable", "integer"),
        ("vocabulary", "character"), ("vocabulary_group", "character"),
        ("vocabulary_unsure", "integer"),
        ("vocabulary_alt", "character"), ("vocabulary_unstable", "integer"),
        ("functional_label", "character"), ("operation", "character"),
        ("scope_not_scorable", "integer"),
        ("vocabulary_not_scorable", "integer"),
    ],
    "papers": [
        ("paper_id", "character"), ("work_id", "character"),
        ("field", "character"), ("doi", "character"), ("title", "character"), ("year", "integer"),
        ("journal", "character"), ("issn", "character"), ("sjr", "double"),
        ("sjr_quartile", "character"), ("cited_by_count", "integer"),
        ("abstract_structured", "integer"), ("evidence_type", "character"),
        ("assay", "character"), ("headline_finding", "character"),
        ("title_abstract_mismatch", "integer"),
        ("scope", "integer"), ("vocabulary", "character"),
        ("vocabulary_group", "character"),
    ],
    "agreement": [
        ("paper_id", "character"), ("field", "character"),
        ("location", "character"), ("coder_id", "character"),
        ("scope_human", "integer"), ("scope_model", "integer"),
        ("scope_unsure_human", "integer"), ("scope_unsure_model", "integer"),
        ("vocabulary_human", "character"), ("vocabulary_model", "character"),
        ("vocabulary_group_human", "character"),
        ("vocabulary_group_model", "character"),
        ("not_scorable_human", "integer"), ("not_scorable_model", "integer"),
        ("vocabulary_unsure_human", "integer"),
        ("vocabulary_unsure_model", "integer"),
        ("scope_note", "character"), ("vocabulary_note", "character"),
    ],
    "flow": [
        ("order", "integer"), ("step", "character"), ("field", "character"),
        ("stage", "integer"),
        ("label", "character"), ("n_in", "integer"),
        ("n_excluded", "integer"), ("n_out", "integer"),
        ("reason", "character"),
    ],
    "exclusions": [
        ("paper_id", "character"), ("field", "character"),
        ("step", "character"), ("reason", "character"),
    ],
    "levels": [
        ("variable", "character"), ("level", "character"),
        ("order", "integer"),
    ],
    "schema": [
        ("table", "character"), ("column", "character"),
        ("type", "character"),
    ],
}

# the answer NA as written in the tables (R would read "NA" as missing)
NOT_SCORABLE = "not_scorable"


def levels():
    """(variable, level, order) for every ordered or coded variable, taken
    from the definitions themselves (config, prompts) so they cannot drift."""
    def codes(axis):           # NA is the exclusion row, not a code
        return [lv["key"] for lv in axis["levels"]
                if lv["key"] != prompts.NOT_DOES]
    vocab = codes(prompts.VOCAB_AXIS)
    defs = {
        "field": list(config.FIELDS),            # OT first: the reference
        "location": config.LOCATIONS,
        "scope": codes(prompts.SCOPE_AXIS),
        "vocabulary": vocab,
        # not_scorable occurs in agreement.csv only
        "vocabulary_group": list(dict.fromkeys(
            prompts.VOCAB_GROUPS[v] for v in vocab)) + [NOT_SCORABLE],
        "evidence_type": prompts.EVIDENCE_TYPES,
        "sjr_quartile": ["Q1", "Q2", "Q3", "Q4"],
    }
    return [{"variable": var, "level": lev, "order": i}
            for var, levs in defs.items() for i, lev in enumerate(levs)]


# ------------------------------------------------------------------ helpers
def load(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _int(v):
    """An integer cell, or empty. Accepts '2', '2.0', 2; '', 'None' -> ''."""
    if v in (None, "", "None"):
        return ""
    return int(float(v))


def _vocab(v):
    """A vocabulary answer as written in the analysis tables."""
    if v in (None, "", "None"):
        return ""
    return NOT_SCORABLE if v == prompts.NOT_DOES else v


def _alt_scope(v):
    """The scope level weighed against: 0-3, not_scorable, or empty."""
    if v in (None, "", "None"):
        return ""
    return NOT_SCORABLE if v == prompts.NOT_DOES else str(int(float(v)))


def _group(code):
    """The two-level vocabulary group (behavioural / mechanistic) for a table
    code (A, B, C); not_scorable stays not_scorable."""
    if not code:
        return ""
    return NOT_SCORABLE if code == NOT_SCORABLE else prompts.VOCAB_GROUPS[code]


def write_table(path, table, rows):
    """Write rows with exactly the schema's columns, in order. No cell may be
    the string 'NA' or 'None': missing is an empty cell."""
    cols = [c for c, _ in SCHEMA[table]]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="raise")
        w.writeheader()
        for r in rows:
            out = {c: ("" if r.get(c) is None else r.get(c, "")) for c in cols}
            bad = [c for c, v in out.items() if v in ("NA", "None")]
            assert not bad, f"{table}: sentinel value in {bad} ({r})"
            w.writerow(out)


# --------------------------------------------------------------------- join
def build(run):
    """The spans and papers tables, from the run's step outputs."""
    manifest = {r["paper_id"]: r for r in load(run.manifest)}
    spans = load(run.spans)
    scores = {(r["paper_id"], r["location"]): r for r in load(run.scores)}
    excl05 = {r["paper_id"]: r["reason"] for r in load(run.exclusions)
              if r["step"] == "05"}

    span_rows = []
    for sp in spans:
        pid, loc = sp["paper_id"], sp["location"]
        sc = scores.get((pid, loc), {})
        m = manifest.get(pid, {})
        scope = _int(sc.get("scope"))
        vocab = _vocab(sc.get("vocabulary")) if loc == config.VOCAB_LOCATION else ""
        is_conc = loc == config.VOCAB_LOCATION
        # step 05 excluded the paper: the scorer answered NA, "not a
        # '[substance X] does Y' statement". Any codes stay for the audit;
        # it gets no outcome.
        excluded = _int(sp.get("paper_excluded"))
        exclusion = sp.get("paper_exclusion", "")
        if pid in excl05 and not excluded:
            excluded, exclusion = 1, excl05[pid]
        span_rows.append({
            "paper_id": pid, "work_id": m.get("work_id", ""),
            "field": sp["field"], "location": loc,
            "abstract_structured": _int(sp.get("abstract_structured")),
            "year": _int(m.get("year")), "journal": m.get("journal", ""),
            "issn": m.get("issn", ""), "sjr_quartile": m.get("sjr_quartile", ""),
            "cited_by_count": _int(m.get("cited_by_count")),
            "evidence_type": sp.get("evidence_type", ""),
            "assay": sp.get("assay", ""),
            "is_na": _int(sp.get("is_na")), "scorable": _int(sp.get("scorable")),
            "span_issue": sp.get("span_issue", ""),
            "label_removed": _int(sp.get("label_removed")),
            "span": sp.get("span", ""),
            "n_sentences": _int(sp.get("n_sentences")),
            "substance_does": _int(sp.get("substance_does")),
            "does_clause": sp.get("does_clause", ""),
            "paper_excluded": excluded,
            "paper_exclusion": exclusion,
            "vocab_span": sp.get("vocab_span", ""),
            "scope": scope, "scope_quote": sc.get("scope_quote", ""),
            "scope_unsure": _int(sc.get("scope_unsure")),
            "scope_alt": _alt_scope(sc.get("scope_alt")),
            "scope_unstable": _int(sc.get("scoring_unstable")),
            "vocabulary": vocab,
            "vocabulary_group": _group(vocab),
            "vocabulary_unsure": _int(sc.get("vocabulary_unsure")) if is_conc else "",
            "vocabulary_alt": _vocab(sc.get("vocabulary_alt")) if is_conc else "",
            "vocabulary_unstable": _int(sc.get("vocab_unstable")) if is_conc else "",
            "functional_label": sc.get("functional_label", "") if is_conc else "",
            "operation": sc.get("operation", "") if is_conc else "",
            "scope_not_scorable": _int(sc.get("scope_not_scorable")),
            "vocabulary_not_scorable": (_int(sc.get("vocabulary_not_scorable"))
                                        if is_conc else ""),
        })

    by_paper = defaultdict(dict)
    for r in span_rows:
        by_paper[r["paper_id"]][r["location"]] = r
    first = {}
    for sp in spans:
        first.setdefault(sp["paper_id"], sp)
    paper_rows = []
    for pid, locs in by_paper.items():
        m, sp = manifest.get(pid, {}), first[pid]
        conc = locs.get(config.VOCAB_LOCATION, {})
        structured = _int(sp.get("abstract_structured"))
        paper_rows.append({
            "paper_id": pid, "work_id": m.get("work_id", ""),
            "field": sp["field"],
            "doi": m.get("doi", ""), "title": m.get("title", ""),
            "year": _int(m.get("year")), "journal": m.get("journal", ""),
            "issn": m.get("issn", ""),
            "sjr": m.get("sjr", "") and float(m["sjr"]),
            "sjr_quartile": m.get("sjr_quartile", ""),
            "cited_by_count": _int(m.get("cited_by_count")),
            "abstract_structured": structured,
            "evidence_type": sp.get("evidence_type", ""),
            "assay": sp.get("assay", ""),
            "headline_finding": sp.get("headline_finding", ""),
            "title_abstract_mismatch": _int(sp.get("title_abstract_mismatch")),
            # an excluded paper has no outcome here (its codes, if any, are
            # in the spans table for the audit)
            "scope": "" if conc.get("paper_excluded") else conc.get("scope", ""),
            "vocabulary": ("" if conc.get("paper_excluded")
                           else conc.get("vocabulary", "")),
            "vocabulary_group": ("" if conc.get("paper_excluded")
                                 else conc.get("vocabulary_group", "")),
        })
    return span_rows, paper_rows


def agreement_rows(run, span_rows):
    """Coders vs LLM, one row per coded statement x coder; None if no file."""
    if not os.path.exists(run.human_coding):
        return None
    model = {(r["paper_id"], r["location"]): r for r in span_rows}
    out = []
    for h in load(run.human_coding):
        m = model.get((h["paper_id"], h["location"]), {})
        vh = (NOT_SCORABLE if (_int(h.get("vocabulary_not_scorable")) or 0)
              == 1 else _vocab(h.get("vocabulary")))
        vm = (NOT_SCORABLE if m.get("vocabulary_not_scorable") == 1
              else m.get("vocabulary", ""))
        out.append({
            "paper_id": h["paper_id"], "field": m.get("field", ""),
            "location": h["location"], "coder_id": h.get("coder_id", ""),
            "scope_human": _int(h.get("scope")), "scope_model": m.get("scope", ""),
            "scope_unsure_human": _int(h.get("scope_unsure")),
            "scope_unsure_model": m.get("scope_unsure", ""),
            # the vocabulary answer, with NA ("not a '[substance X] does Y'
            # statement") written not_scorable, so its agreement counts too
            "vocabulary_human": vh,
            "vocabulary_model": vm,
            "vocabulary_group_human": _group(vh),
            "vocabulary_group_model": _group(vm),
            # the coder answered NA on either question: a disagreement with
            # step 04's DOES check
            "not_scorable_human": int(
                (_int(h.get("scope_not_scorable")) or 0) == 1
                or (_int(h.get("vocabulary_not_scorable")) or 0) == 1),
            # the scorer's own mark (majority of repeats, either question)
            "not_scorable_model": int(
                m.get("scope_not_scorable") == 1
                or m.get("vocabulary_not_scorable") == 1),
            "vocabulary_unsure_human": _int(h.get("vocabulary_unsure")),
            "vocabulary_unsure_model": m.get("vocabulary_unsure", ""),
            "scope_note": h.get("scope_note", ""),
            "vocabulary_note": h.get("vocabulary_note", ""),
        })
    return out


def write_analysis(run, span_rows, paper_rows, agree):
    os.makedirs(run.analysis, exist_ok=True)
    at = lambda name: os.path.join(run.analysis, name + ".csv")
    write_table(at("spans"), "spans", span_rows)
    write_table(at("papers"), "papers", paper_rows)
    write_table(at("flow"), "flow", load(run.flow))
    write_table(at("exclusions"), "exclusions", load(run.exclusions))
    write_table(at("levels"), "levels", levels())
    write_table(at("schema"), "schema",
                [{"table": t, "column": c, "type": ty}
                 for t, cols in SCHEMA.items() for c, ty in cols])
    if agree is not None:
        write_table(at("agreement"), "agreement", agree)
    elif os.path.exists(at("agreement")):
        os.remove(at("agreement"))
    print(f"wrote {run.analysis}/: spans ({len(span_rows)} rows), papers "
          f"({len(paper_rows)}), flow, exclusions, levels, schema"
          + (f", agreement ({len(agree)})" if agree is not None else ""))


# ------------------------------------------------------------------- checks
def checks(rows):
    """Console summaries for auditing the run (rows: the analysis spans)."""
    conc = [r for r in rows if r["location"] == config.VOCAB_LOCATION
            and not r["paper_excluded"]]
    print("\n== coverage ==")
    for field in sorted({r["field"] for r in conc}):
        here = [r for r in conc if r["field"] == field]
        sc = sum(r["scope"] != "" for r in here)
        print(f"  {field:<4} scored {sc}/{len(here)} conclusions")

    uns = sum(r["scope_unstable"] == 1 for r in rows)
    vun = sum(r["vocabulary_unstable"] == 1 for r in rows)
    print(f"unstable across repeats:  scope {uns}, vocabulary {vun} "
          f"(of {len(rows)} spans) -- review these first")


# --------------------------------------------------------------------- main
def main(run):
    run.require(run.scores, "step 05")
    span_rows, paper_rows = build(run)

    # papers excluded at step 04 or 05 are already out of the cascade
    located = {r["paper_id"] for r in span_rows if not r["paper_excluded"]}
    scored = {r["paper_id"] for r in span_rows
              if r["scope"] != "" and not r["paper_excluded"]}
    ledger = flow.Ledger(run)
    ledger.record("07", "at least one span scored", len(located), len(scored),
                  "no location could be scored")
    ledger.exclude(sorted(located - scored), "07", "no location could be scored")
    ledger.show()

    agree = agreement_rows(run, span_rows)
    write_analysis(run, span_rows, paper_rows, agree)
    checks(span_rows)
    runs.record_step(run, "07", spans=len(span_rows), papers=len(paper_rows),
                     papers_scored=len(scored),
                     agreement_rows=None if agree is None else len(agree))


if __name__ == "__main__":
    main(runs.from_argv())
