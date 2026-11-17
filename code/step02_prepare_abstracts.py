"""
Step 02: Prepare the abstracts.

Normalises whitespace, drops abstracts under MIN_ABSTRACT_CHARS
characters, marks each abstract as structured or not, and writes one JSON
per paper. "Structured" (text.split_structured: a Results-type and a
Conclusions-type label) is descriptive only.

Usage:  python code/step02_prepare_abstracts.py --run NAME
"""
import csv
import glob
import os
import re

import flow
import text
import runs

MIN_ABSTRACT_CHARS = 250


def build_paper(row, abstract):
    """The paper JSON for one manifest row; sets row["abstract_structured"]."""
    sections = text.split_structured(abstract)
    row["abstract_structured"] = "yes" if sections else "no"
    return {"meta": {k: row.get(k, "") for k in
                     ("paper_id", "field", "doi", "year", "journal",
                      "sjr_quartile", "cited_by_count", "abstract_structured")},
            "title": row["title"],
            "abstract": abstract,
            # [[kind, text], ...] without labels; null if unstructured
            "sections": [list(x) for x in sections] if sections else None}


def main(run):
    run.require(run.manifest, "step 01")
    ledger = flow.Ledger(run)
    os.makedirs(run.papers, exist_ok=True)
    with open(run.manifest, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    kept, dropped, short_ids = 0, 0, []
    for row in rows:
        abstract = (row.get("abstract") or "").strip()
        abstract = re.sub(r"\s+", " ", abstract)
        if len(abstract) < MIN_ABSTRACT_CHARS:
            row["fulltext_status"] = "abstract_too_short"
            short_ids.append(row["paper_id"])
            dropped += 1
            continue
        row["fulltext_status"] = "abstract_only"
        runs.write_json(
            os.path.join(run.papers, f"{row['paper_id']}.json"),
            build_paper(row, abstract))
        kept += 1

    fields = list(rows[0].keys())
    if "abstract_structured" not in fields:
        fields.append("abstract_structured")
    with open(run.manifest, "w", newline="", encoding="utf-8") as f:
        wri = csv.DictWriter(f, fieldnames=fields)
        wri.writeheader()
        wri.writerows(rows)

    ledger.record("02", "abstract long enough to code", len(rows), kept,
                f"abstract under {MIN_ABSTRACT_CHARS} characters")
    ledger.exclude(short_ids, "02",
                 f"abstract under {MIN_ABSTRACT_CHARS} characters")

    stale = ({os.path.basename(p)[:-5] for p in
              glob.glob(os.path.join(run.papers, "*.json"))}
             - {r["paper_id"] for r in rows})
    if stale:
        print(f"note: {len(stale)} JSON(s) in {run.papers} are left over "
              f"from earlier runs; steps 03+ ignore them (they read the manifest)")

    n_struct = sum(1 for r in rows if r.get("abstract_structured") == "yes")
    print(f"prepared {kept} papers ({dropped} dropped: abstract too short)")
    print(f"structured abstracts: {n_struct}/{kept} "
          f"({(n_struct/kept*100 if kept else 0):.0f}%)")
    ledger.summary("02")
    runs.record_step(run, "02", papers_in=len(rows), kept=kept,
                     too_short=dropped, structured=n_struct)


if __name__ == "__main__":
    main(runs.from_argv())
