"""
Step 03: Screen the papers.

One LLM call per paper, on title and abstract (prompts.SCREEN_SYSTEM),
with three single-label questions: study design, study scope, and the
role of the field's substance. Labels outside the taxonomy are
retried, then recoded ambiguous (design, scope) or unknown (role); either
excludes the paper.

Usage:  python code/step03_screen.py --run NAME [--batch]
"""
import csv
import os
import sys
from collections import Counter

import flow
import prompts
import runs
from anthropic_client import map_calls


def main(run, batch=False):
    run.require(run.manifest, "steps 01-02")
    ledger = flow.Ledger(run)
    # screen the papers step 02 prepared
    with open(run.manifest, encoding="utf-8") as f:
        ids = [r["paper_id"] for r in csv.DictReader(f)
               if r.get("fulltext_status") == "abstract_only"]
    papers = [runs.read_json(os.path.join(run.papers, f"{pid}.json"))
              for pid in ids]
    if not papers:
        raise SystemExit("no papers - run steps 01 and 02 first")

    jobs = [(p["meta"]["paper_id"], prompts.render_paper(p)) for p in papers]
    print(f"screening {len(jobs)} papers ...")
    out = map_calls(jobs, prompts.SCREEN_SYSTEM, prompts.SCREEN_TOOL,
                    "step03_screen", run.raw, label="screening", batch=batch)

    by_id = {p["meta"]["paper_id"]: p for p in papers}
    rows, failed, violations = [], [], []
    for pid, res in out.items():
        if not res:
            failed.append(pid)
            continue
        m = by_id[pid]["meta"]
        design = res["design"]
        scope = res["scope"]
        if res.get("_enum_violation"):
            # a label outside the taxonomy even after retries: ambiguous
            violations.append((pid, res["_enum_violation"]))
            if design not in prompts.DESIGNS:
                design = "ambiguous"
            if scope not in prompts.SCOPES:
                scope = "ambiguous"
        role = res.get("role")
        if role not in prompts.ROLES:
            # off-taxonomy or missing: not shown to be focal, so not eligible
            violations.append((pid, f"role={role!r}"))
            role = "unknown"
        empirical = design in prompts.EMPIRICAL_DESIGNS
        cns = empirical and scope == "cns"
        rows.append({
            "paper_id": pid, "field": m["field"], "title": by_id[pid]["title"],
            "design": design, "scope": scope,
            "design_uncertain": int(bool(res.get("design_uncertain"))),
            "scope_uncertain": int(bool(res.get("scope_uncertain"))),
            "role": role,
            "role_uncertain": int(bool(res.get("role_uncertain"))),
            "eligible": int(cns and role == "focal"),
            "exclusion_reason": (
                "" if cns and role == "focal"
                else ("substance not the focus of the headline finding: "
                      + role) if cns
                else "design ambiguous (cannot tell if data are the authors' own)"
                if design == "ambiguous"
                else ("not original research: " + design) if not empirical
                else "scope ambiguous (cns vs peripheral)" if scope == "ambiguous"
                else "not a CNS study: " + scope),
        })

    if not rows:
        raise SystemExit(f"no paper was classified: every LLM call failed -- see the "
                         f"errors above and {run.raw}")
    with open(run.screen, "w", newline="", encoding="utf-8") as f:
        wri = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wri.writeheader()
        wri.writerows(rows)

    n = len(papers)
    amb_design = [r["paper_id"] for r in rows if r["design"] == "ambiguous"]
    non_emp = [r["paper_id"] for r in rows
               if r["design"] not in prompts.EMPIRICAL_DESIGNS
               and r["design"] != "ambiguous"]
    emp = [r for r in rows if r["design"] in prompts.EMPIRICAL_DESIGNS]
    # (ambiguous-design papers are excluded above and never reach scope)
    ambiguous = [r["paper_id"] for r in emp if r["scope"] == "ambiguous"]
    non_cns = [r["paper_id"] for r in emp
               if r["scope"] not in ("cns", "ambiguous")]
    cns_rows = [r for r in emp if r["scope"] == "cns"]
    not_focal = [r["paper_id"] for r in cns_rows if r["role"] != "focal"]
    eligible = [r for r in rows if r["eligible"]]

    ledger.record("03", "screened successfully", n, len(rows),
                "model returned no classification")
    ledger.exclude(failed, "03", "model returned no classification")
    ledger.record("03", "original research article", len(rows),
                len(rows) - len(non_emp),
                "review / commentary / methods / case report")
    ledger.exclude(non_emp, "03", "review / commentary / methods / case report")
    ledger.record("03", "design resolvable", len(rows) - len(non_emp),
                len(rows) - len(non_emp) - len(amb_design),
                "design ambiguous: cannot tell if the data are the authors' own")
    ledger.exclude(amb_design, "03",
                 "design ambiguous: cannot tell if the data are the authors' own")
    ledger.record("03", "CNS study", len(emp), len(emp) - len(non_cns),
                "peripheral or non-nervous-system study")
    ledger.exclude(non_cns, "03", "peripheral or non-nervous-system study")
    ledger.record("03", "scope resolvable", len(emp) - len(non_cns),
                len(cns_rows), "scope ambiguous between CNS and peripheral")
    ledger.exclude(ambiguous, "03", "scope ambiguous between CNS and peripheral")
    ledger.record("03", "substance is the focus", len(cns_rows), len(eligible),
                "substance not the focus of the headline finding")
    ledger.exclude(not_focal, "03",
                   "substance not the focus of the headline finding")

    if violations:
        print(f"\n  {len(violations)} paper(s) returned a label outside the "
              f"taxonomy after retries; recoded as ambiguous:")
        for pid, v in violations[:5]:
            print(f"     {pid}: {v}")
    print(f"\ndesign: {dict(Counter(r['design'] for r in rows))}")
    print(f"scope : {dict(Counter(r['scope'] for r in rows))}")
    print(f"role  : {dict(Counter(r['role'] for r in rows))}")
    unc = sum(r["design_uncertain"] or r["scope_uncertain"]
              or r["role_uncertain"] for r in rows)
    n_amb = len(amb_design) + len(ambiguous)
    print(f"\n{len(eligible)}/{n} eligible; {unc} flagged uncertain by the LLM"
          f"{f'; {n_amb} excluded as ambiguous' if n_amb else ''}")
    if n_amb:
        print(f"   ({len(amb_design)} ambiguous design, {len(ambiguous)} "
              f"ambiguous scope; excluded, and listed in screen.csv)")
    print(f"written to {run.screen}")
    ledger.summary("03")
    runs.record_step(run, "03", screened=n, classified=len(rows),
                     eligible=len(eligible), batch=batch)


if __name__ == "__main__":
    main(runs.from_argv(), batch="--batch" in sys.argv)
