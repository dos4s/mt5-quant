# mt5-quant — Windows backend (native, no Wine)

Dependency-free Python MCP for MetaTrader 5 on **Windows**. Wraps the official `MetaTrader5`
package (attaches to a **running** terminal) + `metaeditor64.exe`, so it needs no Wine. Speaks MCP
over stdio using only the standard library — runs on bleeding-edge Python where the `mcp` SDK's
wheels don't exist yet.

The repo's `launch.py` selects this on Windows and the Rust/Wine binary on Linux/macOS, so one
`.mcp.json` entry (`python launch.py`) works on every OS.

## Tools (42)

- **Live terminal**: `mt5_health`, `mt5_account`, `mt5_positions`, `mt5_orders`,
  `mt5_history_deals`, `mt5_symbol`, `mt5_rates`, `mt5_list_symbols`, `mt5_active_account`
- **Dev agility**: `mt5_logs` (read EA `Print()` output), `mt5_deploy_ea`, `mt5_read_common_file`
- **Discovery**: `mt5_list_experts` / `_indicators` / `_scripts`
- **Compile**: `mt5_compile`, `mt5_validate_syntax` (MetaEditor CLI)
- **.set files**: `mt5_read_set`, `mt5_write_set`, `mt5_patch_set`, `mt5_clone_set`,
  `mt5_describe_sweep`, `mt5_diff_set`, `mt5_list_set_files`
- **Report analytics** (pass a tester `.htm`/`.xml`): `mt5_analyze_report`, `mt5_list_deals`,
  `mt5_search_deals_comment` / `_magic`, `mt5_analyze_top_losses` / `_monthly_pnl` /
  `_time_performance` / `_direction_bias` / `_streaks` / `_costs` / `_profit_distribution` /
  `_volume_vs_profit` / `_layer_performance` / `_loss_sequences`
- **Backtest runner** (`mt5_run_backtest`, `mt5_get_backtest_status`) — **DEDICATED terminal only**
  (`MT5_BACKTEST_TERMINAL` + `MT5_BACKTEST_DATADIR`); it kills/relaunches `terminal64.exe`.
- **System**: `mt5_system_resources`, `mt5_verify_setup`

## Requirements

A running MT5 terminal (logged in) + `pip install MetaTrader5` + `metaeditor64.exe`
(env `MT5_METAEDITOR`). **One python client per terminal.** No `order_send` in this cut.

## Smoke test

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | python windows/server.py
```
