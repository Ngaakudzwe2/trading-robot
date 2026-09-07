#!/usr/bin/env bash
#
# compile_mql5.sh -- headless MQL5 compilation on macOS via Wine + MetaEditor's
# command-line compile flag.
#
# MetaEditor is a Windows binary; MetaTrader doesn't ship a native macOS build,
# and there is no MetaEditor-as-a-standalone-download for Mac either -- it's
# bundled with the MT5 terminal's Windows installer. So the real pipeline is:
#
#   1. Install Wine on macOS (one-time):     brew install --cask wine-stable
#   2. Install the MT5 terminal under Wine (one-time), which brings MetaEditor
#      with it:                              wine mt5setup.exe
#      (download mt5setup.exe from your broker or metatrader5.com first)
#   3. Install the mql-zmq library (one-time, referenced by
#      mt5_bridge_ea/AmaroZmqBridge.mq5's #include) into that Wine prefix's
#      MQL5/Include/Zmq/ and MQL5/Libraries/ -- see the .mq5 file's header
#      comment for exactly what's needed.
#   4. Run this script to compile.
#
# THIS ENVIRONMENT (corrected 2026-09-07): an earlier version of this comment
# claimed no Wine and no MetaEditor existed on this machine. That was WRONG --
# it was based on a `find` that only searched ~/.wine and /, and never looked
# where MetaTrader's official macOS build actually installs:
#   /Applications/MetaTrader 5.app          (bundles its own wine64)
#   ~/Library/Application Support/net.metaquotes.wine.metatrader5/   (prefix)
# Both are present, and this script now finds and uses them automatically.
#
# What IS still missing here is the mql-zmq library itself: the terminal's
# MQL5/Include/Zmq/, MQL5/Include/Mql/ and MQL5/Libraries/ are all empty, so
# AmaroZmqBridge.mq5 cannot compile until step 3 below is done. Run
# scripts/verify_mt5_install.py against the data folder to confirm.
#
# Note also that driving MetaEditor headlessly through the bundled Wine did
# not reliably emit a compile log in testing -- MetaEditor started (its GUI
# stack initialises) but produced no artifact. Compiling from the MetaEditor
# GUI with F7 is the dependable path on this setup.
#
# Usage:
#   scripts/compile_mql5.sh [path/to/Source.mq5]
#     [--metaeditor /path/to/metaeditor64.exe]
#     [--data-dir "/path/to/Wine/prefix/drive_c/.../Terminal/<hash>"]
#     [--wine-prefix ~/.wine]
#
# Defaults to compiling mt5_bridge_ea/AmaroZmqBridge.mq5.
# Exit code 0 only on a confirmed, verified .ex5 build artifact.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SRC_MQ5="$REPO_ROOT/mt5_bridge_ea/AmaroZmqBridge.mq5"
METAEDITOR_PATH=""
DATA_DIR=""
WINE_PREFIX="${WINEPREFIX:-$HOME/.wine}"
WINE_BIN=""
LOG_DIR="$REPO_ROOT/logs"

# ---------------------------------------------------------------- arg parsing
while [[ $# -gt 0 ]]; do
  case "$1" in
    --metaeditor) METAEDITOR_PATH="$2"; shift 2 ;;
    --data-dir)   DATA_DIR="$2"; shift 2 ;;
    --wine-prefix) WINE_PREFIX="$2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) SRC_MQ5="$1"; shift ;;
  esac
done

if [[ ! -f "$SRC_MQ5" ]]; then
  echo "ERROR: source file not found: $SRC_MQ5" >&2
  exit 1
fi
mkdir -p "$LOG_DIR"

# ------------------------------------------------------------ dependency check
check_dependencies() {
  local ok=1

  # MT5.app bundles its own wine64; prefer it, since a Mac with the official MT5
  # build has no system-wide wine on PATH.
  local bundled="/Applications/MetaTrader 5.app/Contents/SharedSupport/wine/bin/wine64"
  if [ -x "$bundled" ]; then
    WINE_BIN="$bundled"
    WINE_PREFIX="${WINEPREFIX:-$HOME/Library/Application Support/net.metaquotes.wine.metatrader5}"
    echo "Using MT5.app's bundled wine64 and prefix: $WINE_PREFIX"
  elif ! command -v wine >/dev/null 2>&1 && ! command -v wine64 >/dev/null 2>&1; then
    echo "ERROR: Wine is not installed or not on PATH." >&2
    echo "  Install it with:  brew install --cask wine-stable" >&2
    echo "  (or wine-crossover / wine-devel, any Wine build works for MetaEditor)" >&2
    ok=0
  fi

  if [[ -z "$METAEDITOR_PATH" ]]; then
    local candidates=(
      # MetaTrader's OFFICIAL macOS build ships its own bundled Wine and lives here --
      # verified present on this machine 2026-09-07. Checked FIRST because the earlier
      # version of this script only searched ~/.wine and therefore reported "no
      # MetaEditor found" on a machine that had one installed all along.
      "$HOME/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MetaEditor64.exe"
      "$WINE_PREFIX/drive_c/Program Files/MetaTrader 5/metaeditor64.exe"
      "$WINE_PREFIX/drive_c/Program Files (x86)/MetaTrader 5/metaeditor64.exe"
    )
    for c in "${candidates[@]}"; do
      if [[ -f "$c" ]]; then METAEDITOR_PATH="$c"; break; fi
    done
    if [[ -z "$METAEDITOR_PATH" ]]; then
      # broader search, bounded depth so this doesn't scan the whole filesystem
      METAEDITOR_PATH="$(find "$WINE_PREFIX" -maxdepth 6 -iname 'metaeditor64.exe' 2>/dev/null | head -1)"
    fi
  fi

  if [[ -z "$METAEDITOR_PATH" || ! -f "$METAEDITOR_PATH" ]]; then
    echo "ERROR: metaeditor64.exe not found under Wine prefix '$WINE_PREFIX'." >&2
    echo "  MetaEditor isn't distributed standalone for Mac -- it's installed" >&2
    echo "  alongside the MT5 terminal. Install the terminal under Wine first:" >&2
    echo "    wine mt5setup.exe    # download mt5setup.exe from your broker/metatrader5.com" >&2
    echo "  then re-run this script, or pass --metaeditor /full/path/to/metaeditor64.exe" >&2
    ok=0
  fi

  if [[ $ok -eq 0 ]]; then
    echo "" >&2
    echo "Dependency check FAILED -- see above. No compilation was attempted." >&2
    return 1
  fi
  return 0
}

# --------------------------------------------------------------- run compile
run_compile() {
  local log_file="$LOG_DIR/mql5_compile.log"
  rm -f "$log_file"

  local wine_bin
  wine_bin="${WINE_BIN:-$(command -v wine || command -v wine64)}"
  export WINEPREFIX="$WINE_PREFIX" WINEDEBUG=-all

  local cmd=("$wine_bin" "$METAEDITOR_PATH" "/compile:$SRC_MQ5" "/log:$log_file")
  if [[ -n "$DATA_DIR" ]]; then
    cmd=("$wine_bin" "$METAEDITOR_PATH" "/compile:$SRC_MQ5" "/log:$log_file" "/datapath:$DATA_DIR")
  fi

  echo "Running: ${cmd[*]}"
  # MetaEditor's CLI compiler returns 0 on success in modern builds, but this
  # has genuinely varied across versions -- the log file's own "N errors"
  # line (checked below) is the authoritative signal, not just the exit code.
  "${cmd[@]}"
  local wine_exit=$?

  if [[ ! -f "$log_file" ]]; then
    echo "ERROR: MetaEditor produced no log file at $log_file -- compilation likely did not run at all" \
         "(wine exit code $wine_exit). Check that Wine and MetaEditor both actually launched." >&2
    return 1
  fi

  # MetaEditor writes its log as UTF-16LE; normalize to UTF-8 for grepping/printing.
  local log_utf8="$LOG_DIR/mql5_compile.utf8.log"
  iconv -f UTF-16LE -t UTF-8 "$log_file" -o "$log_utf8" 2>/dev/null || cp "$log_file" "$log_utf8"

  echo "----- MetaEditor compile log -----"
  cat "$log_utf8"
  echo "-----------------------------------"

  local error_count
  error_count="$(grep -Eio '[0-9]+ errors?' "$log_utf8" | head -1 | grep -Eo '^[0-9]+')"
  error_count="${error_count:-0}"

  local ex5_path="${SRC_MQ5%.mq5}.ex5"
  if [[ "$error_count" -gt 0 || ! -f "$ex5_path" ]]; then
    echo "" >&2
    echo "COMPILATION FAILED: $error_count error(s) reported, or no .ex5 artifact produced." >&2
    echo "Expected artifact: $ex5_path" >&2
    return 1
  fi

  # Confirm the artifact is actually fresh (newer than the source), not a stale
  # leftover from a previous successful build sitting next to a now-broken source.
  if [[ "$ex5_path" -ot "$SRC_MQ5" ]]; then
    echo "WARNING: $ex5_path exists but is OLDER than $SRC_MQ5 -- treating as stale, not a valid build." >&2
    return 1
  fi

  echo "SUCCESS: compiled artifact verified at $ex5_path ($(stat -f%z "$ex5_path" 2>/dev/null || stat -c%s "$ex5_path") bytes)."
  return 0
}

# --------------------------------------------------------------------- main
if ! check_dependencies; then
  exit 1
fi
if ! run_compile; then
  exit 1
fi
exit 0
