# -*- coding: utf-8 -*-
"""
verify_mt5_install.py

Checks that the mql-zmq dependencies are in the right places inside an MT5
data folder BEFORE you try to compile AmaroZmqBridge.mq5 -- because the
failure mode otherwise is a missing-file compile error that names
<Mql/Lang/Native.mqh> and looks nothing like a ZeroMQ problem.

This machine has no MT5 installation, so this script cannot be run
end-to-end here; it is written to be run on the machine that actually hosts
the terminal (or against a Wine prefix's data folder).

Find your data folder in MT5: File -> Open Data Folder. Then:

    python3 scripts/verify_mt5_install.py "/path/to/MT5/data/folder"

On Windows that path looks like:
    C:\\Users\\<you>\\AppData\\Roaming\\MetaQuotes\\Terminal\\<32-hex-id>
Under Wine on macOS, typically:
    ~/.wine/drive_c/users/<you>/AppData/Roaming/MetaQuotes/Terminal/<32-hex-id>
"""

from __future__ import annotations

import sys
from pathlib import Path

# (relative path, why it matters, hard requirement?)
CHECKS = [
    ("MQL5/Include/Zmq/Zmq.mqh", "main ZeroMQ binding header the EA includes", True),
    ("MQL5/Include/Zmq/Socket.mqh", "Socket class (bind/recv/send)", True),
    ("MQL5/Include/Zmq/Context.mqh", "Context class", True),
    ("MQL5/Include/Zmq/ZmqMsg.mqh", "ZmqMsg class (getData/string ctor)", True),
    ("MQL5/Include/Mql/Lang/Native.mqh", "REQUIRED by Zmq.mqh's first #include -- the "
                                          "step most installs miss by copying only Include/Zmq/", True),
    ("MQL5/Libraries/libzmq.dll", "ZeroMQ runtime (must be the 64-bit build for 64-bit MT5)", True),
    ("MQL5/Libraries/libsodium.dll", "required by libzmq -- not optional", True),
    ("MQL5/Experts/AmaroZmqBridge.mq5", "the EA source itself", True),
    ("MQL5/Experts/AmaroZmqBridge.ex5", "compiled EA -- absent until you compile (F7)", False),
]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).expanduser()
    if not root.exists():
        print(f"FAIL: data folder does not exist: {root}")
        return 1
    if not (root / "MQL5").is_dir():
        print(f"FAIL: {root} has no MQL5/ subdirectory -- this is probably not an MT5 data folder.\n"
              f"      Use MT5's File -> Open Data Folder to get the right path.")
        return 1

    print(f"Checking MT5 data folder: {root}\n")
    missing_required = 0
    for rel, why, required in CHECKS:
        p = root / rel
        if p.exists():
            size = p.stat().st_size
            print(f"  OK       {rel}  ({size:,} bytes)")
        elif required:
            missing_required += 1
            print(f"  MISSING  {rel}\n           ^ {why}")
        else:
            print(f"  absent   {rel}  ({why})")

    # Bitness sanity: PE machine type lives at the offset named by the PE header pointer.
    for dll in ["libzmq.dll", "libsodium.dll"]:
        p = root / "MQL5/Libraries" / dll
        if not p.exists():
            continue
        try:
            data = p.read_bytes()
            pe_off = int.from_bytes(data[0x3C:0x40], "little")
            machine = int.from_bytes(data[pe_off + 4:pe_off + 6], "little")
            arch = {0x8664: "64-bit (x64)", 0x014C: "32-bit (x86)"}.get(machine, f"unknown (0x{machine:04x})")
            flag = "" if machine == 0x8664 else "   <-- MT5 64-bit needs the x64 build from Library/MT5/"
            print(f"\n  {dll}: {arch}{flag}")
        except Exception as e:
            print(f"\n  {dll}: could not read PE header ({e})")

    if missing_required:
        print(f"\n{missing_required} required item(s) missing -- compiling now will fail. Fix those first.")
        return 1
    print("\nAll required files present. Compile with MetaEditor (F7) or scripts/compile_mql5.sh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
