#!/usr/bin/env python
"""Cross-platform MT5 MCP launcher — dispatches to the right backend for this OS, so a single
MCP entry works everywhere and the two implementations in this repo coexist:

  - Windows       → `windows/server.py` (native Python: MetaTrader5 package + metaeditor64.exe,
                    talks to a running terminal). No Wine.
  - Linux / macOS → the `mt5-quant` binary (this repo's Rust MCP: MetaEditor-via-Wine).

`os.exec*` replaces this process so the child owns stdio directly — the MCP stream passes through.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    if sys.platform == "win32":
        os.execv(sys.executable, [sys.executable, os.path.join(HERE, "windows", "server.py")])
        return

    # Linux / macOS: run the Rust binary. Prefer an explicit env, then a local release build,
    # then PATH.
    candidates = [
        os.environ.get("MT5_QUANT_BIN"),
        os.path.join(HERE, "target", "release", "mt5-quant"),
        "mt5-quant",
    ]
    for binary in candidates:
        if not binary:
            continue
        try:
            os.execvp(binary, [binary])
        except FileNotFoundError:
            continue
    sys.stderr.write(
        "[mt5-mcp] no backend found. Build it (`cargo build --release`) or install mt5-quant, "
        "or set MT5_QUANT_BIN.\n"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
