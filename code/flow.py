"""
Exclusion ledger of a run: flow.csv, the cascade (one row per filtering
step: papers in, excluded, out, and why), and exclusions.csv (one row per
excluded paper, with its step and reason).

    python code/flow.py --run main               print the cascade
    python code/flow.py --run main --check       exit 1 if it does not chain
    python code/flow.py --run main <paper_id>    why was this paper excluded?

Each row's n_in must equal the previous row's n_out. With several fields,
step 01 has one cascade per field, and step 02 takes in their sum.
"""
import csv
import os
from collections import Counter

FLOW_COLS = ["order", "step", "field", "stage", "label", "n_in", "n_excluded",
             "n_out", "reason"]
EXCL_COLS = ["paper_id", "field", "step", "reason"]


def _ensure(path, cols):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=cols).writeheader()


def breaks(rows: list[dict]) -> list[tuple[dict, dict]]:
    """Rows whose n_in differs from what the cascade put out before them.

    The cascade must chain: every paper that leaves one step enters the next.
    Rows of one field chain with that field's previous row; the first pooled
    row after per-field rows chains with the sum of every field's last output.
    """
    out, last, prev = [], {}, None
    for r in rows:
        f = r.get("field") or ""
        if f:
            before = last.get(f)
            last[f] = r
        elif prev is not None and (prev.get("field") or ""):
            before = {"label": "all fields together", "step": prev["step"],
                      "n_out": sum(int(x["n_out"]) for x in last.values())}
        else:
            before = prev
        if before is not None and int(r["n_in"]) != int(before["n_out"]):
            out.append((before, r))
        prev = r
    return out


def _warn(prev, r):
    print(f"  Ledger break: '{r['label']}' (step {r['step']}) takes in "
          f"{int(r['n_in']):,}, but '{prev['label']}' (step {prev['step']}) put "
          f"out {int(prev['n_out']):,}. Some papers are unaccounted for.")


class Ledger:
    """The exclusion ledger of one run."""

    def __init__(self, run):
        self.flow = run.flow
        self.excl = run.exclusions
        self._cursor = {}
        self._excl_cleared = set()

    def reset(self):
        """Start a fresh ledger (call at the top of step 01)."""
        for p in (self.flow, self.excl):
            if os.path.exists(p):
                os.remove(p)
        _ensure(self.flow, FLOW_COLS)
        _ensure(self.excl, EXCL_COLS)

    def record(self, step: str, label: str, n_in: int, n_out: int,
               reason: str = "", stage: int = 1, field: str = "") -> None:
        """Record one filtering step in the cascade.

        `field` marks a per-field row (step 01 of a run with several fields);
        a new per-field row goes after the last per-field row, before any
        pooled rows.
        """
        _ensure(self.flow, FLOW_COLS)
        with open(self.flow, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        new = {"step": step, "field": field, "stage": stage, "label": label,
               "n_in": n_in, "n_excluded": n_in - n_out, "n_out": n_out,
               "reason": reason}
        key = (step, field)

        def mine(r):
            return r["step"] == step and (r.get("field") or "") == field
        if key not in self._cursor:
            old = [k for k, r in enumerate(rows) if mine(r)]
            rows = [r for r in rows if not mine(r)]
            if old:
                at = old[0]
            elif field:
                per_field = [k for k, r in enumerate(rows) if r.get("field")]
                at = per_field[-1] + 1 if per_field else 0
            else:
                # a step recorded for the first time goes before any later
                # step's rows
                later = [k for k, r in enumerate(rows) if r["step"] > step]
                at = later[0] if later else len(rows)
            self._cursor[key] = at
            self._clear_exclusions(step, field)
        # the same label again in this process replaces its row; a new label
        # goes after this step's rows so far. Insertion order is the cascade
        # order.
        for k, r in enumerate(rows):
            if mine(r) and r["label"] == label:
                rows[k] = dict(new, order=r["order"])
                break
        else:
            at = self._cursor[key]
            rows.insert(at, dict(new, order=at))
            self._cursor[key] = at + 1
        for k, r in enumerate(rows):
            r["order"] = k
        with open(self.flow, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FLOW_COLS)
            w.writeheader()
            w.writerows(rows)
        for prev, r in breaks(rows):
            if (r["step"] == step and r["label"] == label
                    and (r.get("field") or "") == field):
                _warn(prev, r)

    def _clear_exclusions(self, step, field):
        """Remove a step's per-paper exclusions from an earlier run, if
        necessary."""
        if (step, field) in self._excl_cleared:
            return
        self._excl_cleared.add((step, field))
        if not os.path.exists(self.excl):
            return
        with open(self.excl, encoding="utf-8") as f:
            keep = [r for r in csv.DictReader(f)
                    if not (r["step"] == step
                            and (not field or r.get("field") == field))]
        with open(self.excl, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=EXCL_COLS)
            w.writeheader()
            w.writerows(keep)

    def exclude(self, paper_ids: list[str], step: str, reason: str,
                field: str = "") -> None:
        """Record individual papers dropped at a step."""
        self._clear_exclusions(step, field)
        _ensure(self.excl, EXCL_COLS)
        with open(self.excl, encoding="utf-8") as f:
            keep = [r for r in csv.DictReader(f)
                    if not (r["step"] == step and r["reason"] == reason)]
        with open(self.excl, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=EXCL_COLS)
            w.writeheader()
            w.writerows(keep)
        if not paper_ids:
            return
        with open(self.excl, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=EXCL_COLS)
            for pid in paper_ids:
                w.writerow({"paper_id": pid, "field": field, "step": step,
                            "reason": reason})

    def summary(self, step: str) -> None:
        """Print what one step did: papers in and out, exclusions by reason.
        Every step ends with this."""
        if not os.path.exists(self.flow):
            return
        with open(self.flow, encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["step"] == step]
        if not rows:
            return
        first, last = {}, {}
        for r in rows:                     # per field, or "" for pooled rows
            first.setdefault(r.get("field") or "", r)
            last[r.get("field") or ""] = r
        n_in = sum(int(r["n_in"]) for r in first.values())
        n_out = sum(int(r["n_out"]) for r in last.values())
        print(f"\n== step {step} summary: {n_in:,} papers in, {n_out:,} out ==")
        for r in rows:
            exc = int(r["n_excluded"])
            if exc:
                print(f"  {-exc:>+8,}  {r['reason']}")

    def show(self):
        """Print the cascade."""
        if not os.path.exists(self.flow):
            print("no ledger yet; run step 01 first")
            return
        with open(self.flow, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            print("ledger is empty")
            return
        print(f"\n{'step':<6}{'what':<34}{'in':>9}{'excluded':>10}{'out':>9}  why")
        print("-" * 96)
        for r in rows:
            exc = int(r["n_excluded"])
            what = (f"[{r['field']}] " if r.get("field") else "") + r["label"]
            print(f"{r['step']:<6}{what:<34}{int(r['n_in']):>9,}"
                  f"{(f'-{exc:,}' if exc else ''):>10}{int(r['n_out']):>9,}"
                  f"  {r['reason']}")
        print("-" * 96)
        print(f"{'':<6}{'ANALYSED':<34}{'':>9}{'':>10}{int(rows[-1]['n_out']):>9,}")
        for prev, r in breaks(rows):
            _warn(prev, r)

        if os.path.exists(self.excl):
            with open(self.excl, encoding="utf-8") as f:
                ex = list(csv.DictReader(f))
            if ex:
                print(f"\nper-paper exclusions ({len(ex)}), by reason:")
                for reason, n in Counter(r["reason"] for r in ex).most_common():
                    print(f"  {n:>6,}  {reason}")
                print(f"\n(full list: {self.excl})")

    def why(self, paper_id: str) -> str | None:
        """Why was this paper excluded?"""
        if not os.path.exists(self.excl):
            return None
        with open(self.excl, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["paper_id"] == paper_id:
                    return f"{r['step']}: {r['reason']}"
        return None


if __name__ == "__main__":
    import sys
    import runs
    ledger = Ledger(runs.from_argv())
    if "--check" in sys.argv:
        rows = list(csv.DictReader(open(ledger.flow, encoding="utf-8")))
        bad = breaks(rows)
        for prev, r in bad:
            _warn(prev, r)
        print("ledger chains cleanly" if not bad else f"{len(bad)} break(s)")
        sys.exit(1 if bad else 0)
    if len(sys.argv) > 1:
        pid = sys.argv[1]
        print(ledger.why(pid) or f"{pid}: not excluded (or not seen)")
    else:
        ledger.show()
