#!/bin/bash
# The main run's steps, in order. Asks before each (y = run, s = skip,
# q = stop); logs every command run to data/runs/main/commands.log.
# Draw sizes aim at about 250 scored papers per field.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
RUN=main
LOG=data/runs/$RUN/commands.log

SEED=20261013
N_OT=4600      # papers kept after the SJR gate
N_VP=--all     # every eligible paper
N_DA=9200
N_5HT=16000
CODED=30       # statements for the coders

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "!! uncommitted changes: run_info.json will record a dirty commit"
  read -r -p "continue anyway? [y/N] " a; [ "$a" = y ] || exit 0
fi

step() {
  echo
  echo ">>> $*"
  read -r -p "run this step? [y = run, s = skip, q = stop] " a
  case "$a" in
    y) commit=$(git rev-parse --short HEAD)
       git diff --quiet && git diff --cached --quiet || commit="$commit-dirty"
       mkdir -p "data/runs/$RUN"
       echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $commit $*" >> "$LOG"
       "$@" ;;
    s) echo "skipped" ;;
    *) echo "stopped; run the file again to resume (skip the steps done)"
       exit 0 ;;
  esac
}

step $PY code/step01_fetch_corpus.py OT $N_OT --seed=$SEED --run $RUN
step $PY code/step01_fetch_corpus.py VP $N_VP --seed=$SEED --run $RUN --add
step $PY code/step01_fetch_corpus.py DA $N_DA --seed=$SEED --run $RUN --add
step $PY code/step01_fetch_corpus.py 5HT $N_5HT --seed=$SEED --run $RUN --add
step $PY code/step02_prepare_abstracts.py --run $RUN
step $PY code/step03_screen.py --run $RUN --batch
step $PY code/step04_locate.py --run $RUN --batch
step $PY code/step05_blind_score.py --run $RUN --batch
step $PY code/step06_export_for_coders.py --run $RUN --max=$CODED
step $PY code/flow.py --run $RUN --check
# after the coders' files are combined into data/runs/main/human_coding.csv:
step $PY code/step07_join_and_checks.py --run $RUN
