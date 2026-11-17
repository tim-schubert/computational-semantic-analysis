"""
Step 01: Draw abstracts from OpenAlex.

Counts OpenAlex's filter cascade, draws a random sample of the eligible
pool, and keeps the papers whose journal is in the SCImago (SJR) table.

Inclusion:
  - title or abstract contains the field's search term    (OpenAlex filter)
  - type article, not paratext, has an abstract            (OpenAlex filter)
  - published config.YEAR_START or later                   (OpenAlex filter)
  - an ISSN on any of the paper's locations is in the SJR table
Sampling: a seeded server-side sample (--seed), oversampled
(config.SAMPLE_OVERDRAW) to absorb the SJR gate, then trimmed; --all keeps
every eligible paper. Draws above 10,000 combine several samples
(large_sample). The drawn papers' years are compared with the pool's as a
sanity check (year_check).

Usage, e.g.:
  python code/step01_fetch_corpus.py OT 4600 --seed=20261013 --run main
  python code/step01_fetch_corpus.py VP --all --seed=20261013 --run main --add
"""
import csv
import json
import os
import random
import sys
import time

import config
import flow
import openalex
import runs


SAMPLE_MAX = 10000            # the most records one OpenAlex sample returns


def large_sample(filt, select, n, seed, pool_path):
    """A random sample of n > SAMPLE_MAX works: seeded samples (seed,
    seed+1, ...) until n distinct works are in hand, then n of them at random.
    Every record fetched is archived to pool_path."""
    seen = {}
    samples = 0
    with open(pool_path, "w", encoding="utf-8") as raw:
        while len(seen) < n:
            before = len(seen)
            for w in openalex.sample(filt, select, SAMPLE_MAX, seed + samples):
                raw.write(json.dumps(w, ensure_ascii=False) + "\n")
                seen.setdefault((w["id"] or "").rsplit("/", 1)[-1], w)
            samples += 1
            print(f"  sample with seed {seed + samples - 1}: {len(seen)} "
                  f"distinct works so far")
            if len(seen) == before:
                break                 # the pool is exhausted
    ids = sorted(seen)
    random.Random(seed).shuffle(ids)
    return [seen[i] for i in ids[:n]], samples


def year_check(filt, drawn):
    """Sanity check. Publication years, drawn vs pool, in 5-year bins, with
    a chi-square p-value; None if OpenAlex gave no counts."""
    from scipy.stats import chisquare
    data = openalex.get(openalex.WORKS, {"filter": filt,
                                         "group_by": "publication_year",
                                         "per-page": 200}).json()
    pool = {int(g["key"]): g["count"] for g in data.get("group_by") or []
            if str(g["key"]).isdigit()}
    years = [w.get("publication_year") for w in drawn
             if w.get("publication_year")]
    if not pool or not years:
        return None
    lo = config.YEAR_START
    hi = max(max(pool), max(years))
    bins = [(a, min(a + 4, hi)) for a in range(lo, hi + 1, 5)]
    p_tot = sum(pool.values())
    out, obs, exp = {}, [], []
    for a, b in bins:
        p = sum(v for y, v in pool.items() if a <= y <= b) / p_tot
        o = sum(a <= y <= b for y in years)
        out[f"{a}-{b}"] = [round(p, 3), round(o / len(years), 3)]
        obs.append(o)
        exp.append(p * len(years))
    keep = [i for i, e in enumerate(exp) if e > 0]
    pval = chisquare([obs[i] for i in keep],
                     [exp[i] * sum(obs[j] for j in keep) / sum(exp[j] for j in keep)
                      for i in keep]).pvalue
    return {"bins": out, "chi2_p": round(float(pval), 3)}


def load_sjr_issns(path):
    """{issn: (sjr, best_quartile, journal_title)} from the SJR table
    (delimiter and column names are detected)."""
    if not os.path.exists(path):
        raise SystemExit(f"SJR file not found: {path}\n"
                         "Download from scimagojr.com/journalrank.php "
                         "(\"Download data\") and set SJR_CSV in config.py")
    with open(path, encoding="utf-8-sig") as f:
        head = f.readline()
    delim = ";" if head.count(";") >= head.count(",") else ","

    def pick(fieldnames, *wanted):
        for w in wanted:
            for fn in fieldnames:
                if fn and fn.strip().lower() == w:
                    return fn
        return None

    issn_map = {}
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delim)
        fn = reader.fieldnames or []
        c_issn = pick(fn, "issn")
        c_sjr = pick(fn, "sjr")
        c_q = pick(fn, "sjr best quartile", "best quartile", "quartile")
        c_t = pick(fn, "title", "journal", "source title")
        if not c_issn:
            raise SystemExit(f"no ISSN column in {path}; columns: {fn}")
        for row in reader:
            issns = (row.get(c_issn) or "").replace(" ", "")
            sjr = (row.get(c_sjr) or "").replace(",", ".") if c_sjr else ""
            quart = (row.get(c_q) or "") if c_q else ""
            title = (row.get(c_t) or "") if c_t else ""
            for issn in issns.split(","):
                issn = issn.strip().replace("-", "")
                if issn:
                    issn_map[issn] = (sjr, quart, title)
    return issn_map


def norm_issn(issn):
    return (issn or "").replace("-", "").strip()


def filter_cascade(field_term):
    """Counts under successively tighter filters, for the ledger."""
    steps = [
        ("mentions the peptide", [f"title_and_abstract.search:{field_term}"],
         ""),
        ("original research articles", ["type:article"],
         "reviews, editorials, book chapters, etc."),
        ("not paratext", ["is_paratext:false"], "front matter, indexes"),
        ("has an abstract", ["has_abstract:true"], "abstract not available"),
        (f"published {config.YEAR_START}+",
         [f"from_publication_date:{config.YEAR_START}-01-01"],
         f"published before {config.YEAR_START}"),
    ]
    acc, out = [], []
    prev = None
    for label, parts, reason in steps:
        acc += parts
        n = openalex.count(",".join(acc))
        out.append((label, prev if prev is not None else n, n, reason))
        prev = n
        time.sleep(config.OPENALEX_RETRY_BASE / 2)
    return out


SELECT = ("id,doi,title,publication_year,abstract_inverted_index,"
          "primary_location,locations,type,cited_by_count")


def eligible_filter(field_term):
    """The OpenAlex filter for the eligible pool."""
    return ",".join([
        f"title_and_abstract.search:{field_term}", "type:article",
        "is_paratext:false", "has_abstract:true",
        f"from_publication_date:{config.YEAR_START}-01-01",
    ] + ([f"to_publication_date:{config.YEAR_END}-12-31"] if config.YEAR_END else []))


def journal_match(work, sjr):
    """The SJR gate for one work: (source, issns, sjr_hit).

    Locations are checked primary first; the first ISSN in the SJR table
    wins. sjr_hit is None if none matches; issns then shows whether any
    location had an ISSN at all.
    """
    locs = [work.get("primary_location") or {}] + list(work.get("locations") or [])
    src, issns, hit = {}, [], None
    for loc in locs:
        s_ = (loc or {}).get("source") or {}
        li = [norm_issn(x) for x in (s_.get("issn") or [])]
        if s_.get("issn_l"):
            li.append(norm_issn(s_["issn_l"]))
        h = next((sjr[i] for i in li if i in sjr), None)
        if li and not issns:
            src, issns = s_, li
        if h:
            src, issns, hit = s_, li, h
            break
    return src, issns, hit


def unit_id(work_id, field):
    """The unit of analysis, a paper in a field: a paper drawn into two
    fields is processed once per field."""
    return f"{work_id}_{field}"


def _row(w, field, src, issns, hit):
    sjr_val, quartile, jtitle = hit
    wid = (w["id"] or "").rsplit("/", 1)[-1]
    return {
        "paper_id": unit_id(wid, field),
        "work_id": wid,
        "field": field,
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        "title": w.get("title") or "",
        "year": w.get("publication_year"),
        "journal": src.get("display_name") or jtitle,
        "issn": issns[0] if issns else "",
        "sjr": sjr_val,
        "sjr_quartile": quartile,
        "cited_by_count": w.get("cited_by_count"),
        "abstract": openalex.reconstruct_abstract(w.get("abstract_inverted_index")),
        # fulltext_status is set by step 02; the other two stay empty
        # (full-text stage not built)
        "fulltext_status": "", "fulltext_version": "", "oa_status": "",
    }


def main(run, field=None, limit=None, scan_cap=None, cascade=True,
         force=False, seed=None, add=False):
    field = field or config.PILOT_FIELD
    existing = []
    if add:
        run.require(run.manifest, "step 01 for the run's first field")
        with open(run.manifest, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
        if any(r["field"] == field for r in existing):
            raise SystemExit(f"run {run.name!r} already has {field} papers")
    elif os.path.exists(run.manifest) and not force:
        raise SystemExit(f"run {run.name!r} already has a corpus "
                         f"({run.manifest}); choose a new run name, add a "
                         f"field with --add, or pass --force to replace it")
    os.makedirs(run.dir, exist_ok=True)
    ledger = flow.Ledger(run)

    def rec(*a, **k):
        ledger.record(*a, field=field, **k)          # this field's cascade
    seed = seed or config.SAMPLE_SEED
    term = config.FIELDS[field]["search"]
    sjr = load_sjr_issns(config.SJR_CSV)
    print(f"[{field}] SJR file: {len(sjr)} ISSNs loaded")
    if not config.OPENALEX_API_KEY:
        print("  (no OPENALEX_API_KEY in .env: the keyless budget is small; "
              "a free key is at openalex.org/settings/api)")

    if not add:
        ledger.reset()
    n_pool = None                   # size of the eligible pool
    if cascade:
        print(f"[{field}] counting the filter cascade ...")
        for label, n_in, n_out, reason in filter_cascade(term):
            rec("01", label, n_in, n_out, reason)
            n_pool = n_out

    # with a target size: a server-side sample, oversampled for the SJR gate
    n_samples = None
    if limit:
        n_draw = int(limit * config.SAMPLE_OVERDRAW) + 20
        if n_pool is not None:
            n_draw = min(n_draw, n_pool)
        print(f"[{field}] drawing a server-side random sample of {n_draw} "
              f"(seed {seed}) ...")
        if n_draw <= SAMPLE_MAX:
            source = openalex.sample(eligible_filter(term), SELECT, n_draw,
                                     seed)
        else:
            os.makedirs(run.raw, exist_ok=True)
            source, n_samples = large_sample(
                eligible_filter(term), SELECT, n_draw, seed,
                os.path.join(run.raw, f"openalex_{field}_pool.jsonl"))
    else:
        print(f"[{field}] scanning OpenAlex (all eligible records) ...")
        source = openalex.pages(eligible_filter(term), SELECT)

    # every record as returned
    os.makedirs(run.raw, exist_ok=True)
    raw_path = os.path.join(run.raw, f"openalex_{field}.jsonl")
    raw = open(raw_path, "w", encoding="utf-8")

    rows, seen, no_sjr, no_issn = [], 0, [], []
    ids, dups = set(), []
    for w in source:
        seen += 1
        raw.write(json.dumps(w, ensure_ascii=False) + "\n")
        pid = (w["id"] or "").rsplit("/", 1)[-1]
        # a work returned twice is archived and counted, but used once
        if pid in ids:
            dups.append(pid)
            continue
        ids.add(pid)
        src, issns, hit = journal_match(w, sjr)
        if not hit:
            (no_issn if not issns else no_sjr).append(unit_id(pid, field))
            continue
        rows.append(_row(w, field, src, issns, hit))
        if scan_cap and seen >= scan_cap:
            break
        if not limit and seen % 2000 == 0:
            print(f"  scanned {seen}, eligible {len(rows)} ...")

    raw.close()
    print(f"[{field}] {seen} raw records written to {raw_path}")
    years = None
    if limit:
        with open(raw_path, encoding="utf-8") as f:
            drawn = [json.loads(line) for line in f]
        years = year_check(eligible_filter(term), drawn)
        if years:
            print(f"[{field}] publication years, pool vs drawn "
                  f"(chi-square p = {years['chi2_p']}):")
            for b, (p, d) in years["bins"].items():
                print(f"    {b}: pool {p:.0%}, drawn {d:.0%}")
    eligible = len(rows)
    # the draw comes before the SJR gate (undrawn papers are not listed
    # individually in exclusions.csv)
    if n_pool is not None:
        if limit:
            rec("01", f"random draw (seed {seed})",
                        n_pool, seen, "sampling, not a scientific criterion")
        else:
            rec("01", "records retrieved", n_pool, seen,
                        "not returned while paging (index changed during the "
                        "scan, or --scan-cap)")
    unique = seen - len(dups)
    rec("01", "one record per paper", seen, unique,
                  "duplicate record in OpenAlex's sample")
    if dups:
        print(f"[{field}] {len(dups)} duplicate record(s) in the sample "
              f"skipped (kept in the raw file)")
    rec("01", "has a journal ISSN", unique, unique - len(no_issn),
                "no ISSN on any location (repository/preprint only)")
    ledger.exclude(no_issn, "01",
                 "no ISSN on any location (repository/preprint only)", field)
    rec("01", "journal listed in SJR", unique - len(no_issn), eligible,
                "journal ISSN not indexed in SCImago")
    ledger.exclude(no_sjr, "01", "journal ISSN not indexed in SCImago", field)

    if limit and eligible > limit:
        dropped = [r["paper_id"] for r in rows[limit:]]
        rows = rows[:limit]           # the sample is already random
        ledger.exclude(dropped, "01", "surplus from oversampling", field)
        rec("01", "trimmed to target size", eligible, limit,
                    "surplus from oversampling (not a scientific criterion)")
        how = f"random sample of {limit} (seed {seed})"
    elif limit:
        how = (f"random sample; only {eligible} passed SJR from {seen} drawn "
               f"— raise SAMPLE_OVERDRAW if you need exactly {limit}")
    else:
        how = "all eligible"

    if not rows:
        raise SystemExit(f"[{field}] no eligible papers -- check SJR file/filters")
    with open(run.manifest, "w", newline="", encoding="utf-8") as f:
        wri = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wri.writeheader()
        wri.writerows(existing + rows)
    print(f"[{field}] {seen} drawn, {eligible} passed SJR, kept {len(rows)} "
          f"({how}); written to {run.manifest}")
    # one entry per field, so a run with several fields keeps them all
    fields = {}
    if add and os.path.exists(run.info):
        fields = (runs.read_json(run.info).get("steps", {}).get("01", {})
                  .get("details", {}).get("fields", {}))
    fields[field] = dict(
        openalex_filter=eligible_filter(term),
        openalex_select=SELECT,
        sampling=(f"server-side sample, seed {seed}" if limit and not n_samples
                  else f"{n_samples} server-side samples (seeds {seed}-"
                       f"{seed + n_samples - 1}), deduplicated, then a "
                       f"random draw (seed {seed})" if limit
                  else "all eligible records, paged"),
        year_check=years,
        seed=seed if limit else None,
        target=limit, scan_cap=scan_cap, drawn=seen, duplicates=len(dups),
        passed_sjr=eligible,
        kept=len(rows), sjr_file=os.path.relpath(config.SJR_CSV, config.ROOT),
        sjr_sha256=runs.file_sha256(config.SJR_CSV))
    runs.record_step(run, "01", fields=fields)
    if add:
        print(f"  {field} added to run {run.name!r}; run steps 02 onward "
              f"again for the whole run")
    ledger.show()
    ledger.summary("01")


if __name__ == "__main__":
    run_ = runs.from_argv()
    force = "--force" in sys.argv
    args = [a for a in sys.argv[1:] if a not in ("--force", "--add")]
    fld = args[0] if args and not args[0].startswith("--") else None
    rest = [a for a in args[1:]] if fld else args
    if "--all" in rest:
        lim = None
    elif rest and rest[0].isdigit():
        lim = int(rest[0])
    else:
        lim = config.PILOT_N
    cap = None
    for a in rest:
        if a.startswith("--scan-cap="):
            cap = int(a.split("=", 1)[1])
    sd = None
    for a in rest:
        if a.startswith("--seed="):
            sd = int(a.split("=", 1)[1])
    main(run_, fld, lim, cap, force=force, seed=sd, add="--add" in sys.argv)
