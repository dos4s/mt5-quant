# Linux fixes & known bugs (local, v1.32.4)

Workarounds discovered while running mt5-quant on Linux (Wine 11.5, WINEPREFIX
`~/.mt5`, MT5 in portable mode). None of these are patched in the Rust source —
they live in the MT5 install or in how the server is launched. Apply the
symlinks with `scripts/apply-linux-fixes.sh`.

## Fixed via symlinks (required)

1. **Config dir case-sensitivity.** MT5 under Wine creates
   `…/MetaTrader 5/Config` (capital C) but the server hardcodes lowercase
   `config` (`config/common.ini` account gate, terminal.ini patching). Without
   the fix every backtest fails with *"No active MT5 account session
   detected."*
   Fix: `ln -s Config "…/MetaTrader 5/config"`

2. **MetaEditor case bug.** `compile_ea` / `check_mt5_status` look for
   lowercase `metaeditor64.exe`; the real binary is `MetaEditor64.exe`. Without
   the fix every compile fails with *"metaeditor64.exe not found"*.
   Fix: `ln -s MetaEditor64.exe "…/MetaTrader 5/metaeditor64.exe"`
   (`check_mt5_status` still reports `metaeditor64_exe:false` — cosmetic.)

## Operational workarounds (no code fix possible)

3. **EA subfolder resolution.** The server only finds EAs at
   `Experts/<name>.ex5` or `Experts/<name>/<name>.ex5`, never deeper paths like
   `Experts/Examples/MACD/`. Workaround: stage/copy the EA into the Experts
   root before compiling/backtesting.

4. **`set_file` is IGNORED by `run_backtest` / `run_backtest_quick` (major).**
   The tester loads `MQL5/Profiles/Tester/<EAName>.set`, which the MCP
   regenerates from the EA's *compiled default inputs* on every launch; the
   `set_file` you pass is recorded in metadata but never applied, and manually
   overwriting the preset gets clobbered too. Root cause: the MCP can't parse
   MT5-format .set files at all — `write_set_file` / `list_set_files` report
   `param_count 0`.
   Workaround: edit the EA source input defaults + `compile_ea`, then the
   regenerated preset carries them. For sweeps, edit+recompile per value.
   Always verify actual params via the tester journal line
   `…started with inputs:` (`get_tester_log`).
   Related: multi-symbol EAs need model 1 or 0 — model 2 ("open prices")
   throws *"wrong timeframe request"* on the secondary symbol's M1.

4b. **MCP backtests run on an OFFLINE local tester agent** that may not see
   the broker's data at all (on AXI: pip-denominated, ~0 bars). The terminal's
   MAIN tester, run *connected* via a `[Tester]` startup config, works
   correctly (real data, USD). `scripts/bt.py` is a standalone harness for
   that path: it launches the terminal with a generated tester config, waits,
   parses MT5's own HTML report, and appends metrics to `rankings.jsonl`
   (canonical copy + driver scripts live in `../research/`). EAs must still be
   compiled via `compile_ea` first and exist at `Experts/<name>/<name>.ex5`.

5. **`get_latest_report` returns stale/duplicate metrics** across genuinely
   different runs. Compute metrics from a per-report `export_deals_csv`
   instead. (`export_deals_csv` itself has also failed — fallback: have the EA
   log day-equity to the journal and parse that.)

6. **Headless operation.** The server inherits the ambient `DISPLAY` and never
   spawns Xvfb itself (`display_mode` is effectively informational on Linux).
   For headless/SSH use register the MCP through the wrapper
   `scripts/mt5-quant-headless` (uses `xvfb-run -a`).

7. **`setup.sh` cannot detect this install** — it only scans `~/.wine` /
   `~/.wine64`, never `~/.mt5` (the MetaQuotes installer default). Configure
   `config/mt5-quant.yaml` manually.

8. **No custom-symbol support.** The MCP can only backtest symbols in the
   active broker account's set; a custom symbol name gets fuzzy-matched back to
   the broker's native one. If a broker symbol spec is broken (e.g.
   FundingPips ETHUSD reports pip-denominated profit, 100× off), the only real
   fix is a broker whose native specs are correct (e.g. AXI demo).
