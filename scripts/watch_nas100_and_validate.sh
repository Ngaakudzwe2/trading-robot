#!/usr/bin/env bash
#
# watch_nas100_and_validate.sh -- waits for the USATECHIDXUSD M5 fetch to finish,
# then runs the full validation battery automatically and prints the gate verdict.
#
# Emits ONE compact line per milestone on stdout (each becomes a notification);
# full battery output goes to logs/nas100_battery_run.log and
# data/nas100_battery.json.
#
# "Terminal" means finished OR circuit-breaker-stopped OR the process died --
# a watcher that matched only the success line would sit silent through a crash,
# and silence is indistinguishable from "still fetching". It also verifies the
# exported CSV actually spans the period before trusting a "done" message, so a
# fetch that completed having written little usable data is visible rather than
# quietly producing a battery run on a thin file.
#
# It deliberately does NOT write CAPITAL_PROTECTION_REPORT.md. That document
# informs a decision about $20,000 of someone's money; the computation is
# automated, the client-facing write-up is not.

set -uo pipefail

REPO="/Users/admin/Desktop - admin’s MacBook Pro/AMARO"
NASDAQ_REPO="/private/tmp/claude-501/-Users-admin-Desktop---admin-s-MacBook-Pro-AMARO/772c29ac-de74-4635-980f-4a0c138d8318/scratchpad/NASDAQ"
cd "$REPO" || exit 1

FETCH_LOG="logs/nas100_fetch.log"
BATTERY_LOG="logs/nas100_battery_run.log"
CSV="$NASDAQ_REPO/data/NAS100_M5.csv"
PY="venv/bin/python3"

fetch_alive() { pgrep -f "export_nas100_m5.py" >/dev/null 2>&1; }

# The export step prints "<n> M5 bars -> <path>" as its last act.
export_done() { grep -aE "M5 bars ->" "$FETCH_LOG" 2>/dev/null | tail -1; }
breaker()     { grep -aE "Circuit breaker" "$FETCH_LOG" 2>/dev/null | tail -1; }

# ------------------------------------------------------------------ wait
while true; do
  line="$(export_done)"
  if [ -n "$line" ]; then
    echo "Fetch+export complete: $line"
    break
  fi
  cb="$(breaker)"
  if [ -n "$cb" ]; then
    echo "Fetch stopped by circuit breaker: $cb"
    echo "Partial data may still be usable; continuing to the battery so the result is visible."
    break
  fi
  if ! fetch_alive; then
    echo "Fetch process GONE with no completion line -- check logs/nas100_fetch.log"
    break
  fi
  sleep 120
done

# -------------------------------------------------------- verify coverage
COVERAGE="$("$PY" - <<'PYEOF' 2>/dev/null
import csv, os, collections
p = ("/private/tmp/claude-501/-Users-admin-Desktop---admin-s-MacBook-Pro-AMARO/"
     "772c29ac-de74-4635-980f-4a0c138d8318/scratchpad/NASDAQ/data/NAS100_M5.csv")
if not os.path.exists(p):
    print("MISSING"); raise SystemExit
years = collections.Counter(); n = 0; first = last = None
with open(p) as f:
    r = csv.reader(f); next(r, None)
    for row in r:
        n += 1; years[row[0][:4]] += 1
        first = first or row[0]; last = row[0]
print(f"{n} bars | {first[:10]} -> {last[:10]} | years={sorted(years)}")
PYEOF
)"
echo "CSV coverage: ${COVERAGE:-unreadable}"
if [ "$COVERAGE" = "MISSING" ]; then
  echo "No CSV to validate -- stopping. Re-run the export once the fetch finishes."
  exit 1
fi

# ------------------------------------------------------------- battery
echo "Running 30-fold validation battery (measured 0.89pt spread + 1.5pt + 2x stress)..."
"$PY" -W ignore -m src.nas100_battery --folds 30 --csv "$CSV" > "$BATTERY_LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  echo "Battery FAILED (exit $rc) -- see $BATTERY_LOG"
  tail -5 "$BATTERY_LOG"
  exit 1
fi

# One line per spread scenario's verdict, plus the headline metrics.
grep -aE "GATES:|POOLED|MEDIAN FOLD SHARPE|SIGNIFICANCE|BUY & HOLD|^--- " "$BATTERY_LOG" || true
echo "BATTERY COMPLETE -> $BATTERY_LOG and data/nas100_battery.json"
