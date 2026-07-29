#!/usr/bin/env python
"""MT5 MCP — dependency-free Model Context Protocol server for MetaTrader 5 on Windows.

Windows-native port of the useful surface of `mt5-quant` (which is macOS/Linux+Wine only).
Covers: live terminal data (via the `MetaTrader5` package), MQL5 compile + syntax check,
project discovery, tester `.set` files, backtest-report parsing + the full analytics suite,
and a headless backtest runner (dedicated terminal instance only). Speaks MCP over stdio
(newline-delimited JSON-RPC 2.0) using only the standard library.

Env:
  MT5_METAEDITOR         metaeditor64.exe (default C:\\Program Files\\MetaTrader 5\\metaeditor64.exe)
  MT5_DATA_DIR           MT5 data folder for discovery (default: terminal_info().data_path)
  MT5_BACKTEST_TERMINAL  terminal64.exe of a DEDICATED install for run_backtest (never the live one)
  MT5_BACKTEST_DATADIR   data folder of that dedicated install
"""
import sys
import os
import re
import json
import glob
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report as rp        # noqa: E402
import setfile as sf       # noqa: E402

SERVER_NAME = "mt5"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSION = "2024-11-05"
DEFAULT_METAEDITOR = r"C:\Program Files\MetaTrader 5\metaeditor64.exe"


def log(*a):
    print("[mt5-mcp]", *a, file=sys.stderr, flush=True)


try:
    import MetaTrader5 as mt5
except Exception as e:  # pragma: no cover
    mt5 = None
    _IMPORT_ERR = str(e)

_initialized = False


def ensure_mt5():
    global _initialized
    if mt5 is None:
        return f"MetaTrader5 package not importable: {_IMPORT_ERR}"
    if _initialized:
        return None
    if not mt5.initialize():
        return f"initialize() failed: {mt5.last_error()} (is another python client holding the terminal?)"
    _initialized = True
    return None


def data_dir():
    d = os.environ.get("MT5_DATA_DIR")
    if d:
        return d
    if ensure_mt5() is None:
        ti = mt5.terminal_info()
        if ti and ti.data_path:
            return ti.data_path
    return None


def _rows(nt):
    return [t._asdict() for t in nt] if nt else []


def _jsonable(o):
    return o._asdict() if hasattr(o, "_asdict") else o


def _list_dir(sub, pattern):
    dd = data_dir()
    if not dd:
        return {"error": "no data dir (connect MT5 or set MT5_DATA_DIR)"}
    base = os.path.join(dd, "MQL5", sub)
    hits = []
    for ext in ("*.mq5", "*.ex5", "*.mqh"):
        for p in glob.glob(os.path.join(base, "**", ext), recursive=True):
            name = os.path.relpath(p, base)
            if pattern and pattern.lower() not in name.lower():
                continue
            hits.append(name)
    hits = sorted(set(hits))
    return {"dir": base, "count": len(hits), "items": hits}


# ── live terminal tools ──────────────────────────────────────────────────────

def tool_health(a):
    err = ensure_mt5()
    if err:
        return {"ok": False, "error": err}
    ti, ai = mt5.terminal_info(), mt5.account_info()
    out = {"ok": True, "terminal_connected": bool(ti and ti.connected),
           "company": ti.company if ti else None, "build": ti.build if ti else None,
           "data_path": ti.data_path if ti else None}
    if ai:
        out.update({"login": ai.login, "server": ai.server, "demo": ai.trade_mode == 0,
                    "balance": ai.balance, "equity": ai.equity, "currency": ai.currency})
    return out


def tool_account(a):
    e = ensure_mt5()
    return {"error": e} if e else (_jsonable(mt5.account_info()) or {"error": "account_info failed"})


def tool_positions(a):
    e = ensure_mt5()
    if e:
        return {"error": e}
    p = mt5.positions_get(symbol=a["symbol"]) if a.get("symbol") else mt5.positions_get()
    return {"count": len(p or []), "positions": _rows(p)}


def tool_orders(a):
    e = ensure_mt5()
    if e:
        return {"error": e}
    o = mt5.orders_get(symbol=a["symbol"]) if a.get("symbol") else mt5.orders_get()
    return {"count": len(o or []), "orders": _rows(o)}


def tool_history_deals(a):
    e = ensure_mt5()
    if e:
        return {"error": e}
    days = int(a.get("days", 7))
    to = datetime.now()
    deals = mt5.history_deals_get(to - timedelta(days=days), to)
    return {"days": days, "count": len(deals or []), "deals": _rows(deals)}


def tool_symbol(a):
    e = ensure_mt5()
    if e:
        return {"error": e}
    if not a.get("symbol"):
        return {"error": "symbol required"}
    return _jsonable(mt5.symbol_info(a["symbol"])) or {"error": f"symbol_info: {mt5.last_error()}"}


_TF = {}


def _timeframe(name):
    if not _TF and mt5 is not None:
        for k in ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"):
            _TF[k] = getattr(mt5, f"TIMEFRAME_{k}", None)
    return _TF.get((name or "M1").upper(), _TF.get("M1"))


def tool_rates(a):
    e = ensure_mt5()
    if e:
        return {"error": e}
    if not a.get("symbol"):
        return {"error": "symbol required"}
    r = mt5.copy_rates_from_pos(a["symbol"], _timeframe(a.get("timeframe", "M1")), 0, int(a.get("count", 20)))
    if r is None:
        return {"error": f"copy_rates: {mt5.last_error()}"}
    return {"symbol": a["symbol"], "count": len(r),
            "rates": [{"time": int(x["time"]), "open": float(x["open"]), "high": float(x["high"]),
                       "low": float(x["low"]), "close": float(x["close"]), "volume": int(x["tick_volume"])}
                      for x in r]}


def tool_list_symbols(a):
    e = ensure_mt5()
    if e:
        return {"error": e}
    syms = mt5.symbols_get()
    names = [s.name for s in syms] if syms else []
    pat = a.get("pattern")
    if pat:
        names = [n for n in names if pat.lower() in n.lower()]
    return {"count": len(names), "symbols": names[:500]}


def tool_active_account(a):
    return tool_health(a)


# ── discovery ────────────────────────────────────────────────────────────────

def tool_list_experts(a):
    return _list_dir("Experts", a.get("pattern"))


def tool_list_indicators(a):
    return _list_dir("Indicators", a.get("pattern"))


def tool_list_scripts(a):
    return _list_dir("Scripts", a.get("pattern"))


# ── compile / validate ───────────────────────────────────────────────────────

def _compile(src, syntax_only=False):
    me = os.environ.get("MT5_METAEDITOR", DEFAULT_METAEDITOR)
    if not os.path.isfile(me):
        return {"error": f"metaeditor64.exe not found at {me} (set MT5_METAEDITOR)"}
    logf = os.path.join(tempfile.gettempdir(), "mt5mcp_compile.log")
    try:
        os.path.exists(logf) and os.remove(logf)
    except OSError:
        pass
    args = [me, f"/compile:{src}", f"/log:{logf}"]
    if syntax_only:
        args.append("/s")
    subprocess.run(args, timeout=120)
    text = ""
    if os.path.exists(logf):
        with open(logf, "r", encoding="utf-16", errors="replace") as fh:
            text = fh.read()
    line = next((l.strip() for l in text.splitlines() if l.strip().startswith("Result:")), "")
    return {"result": line or "(no Result line)", "compiled_ok": bool(line) and "0 error" in line,
            "log": text[-4000:]}


def tool_compile(a):
    src = a.get("path")
    if not src or not os.path.isfile(src):
        return {"error": f"path not found: {src}"}
    return _compile(src)


def tool_validate_syntax(a):
    src = a.get("path")
    if not src or not os.path.isfile(src):
        return {"error": f"path not found: {src}"}
    return _compile(src, syntax_only=True)


# ── .set files ───────────────────────────────────────────────────────────────

def tool_read_set(a):
    p = a.get("path")
    return sf.read_set(p) if p and os.path.isfile(p) else {"error": f"not found: {p}"}


def tool_write_set(a):
    p, params = a.get("path"), a.get("params")
    if not p or not isinstance(params, dict):
        return {"error": "path and params (object) required"}
    sf.write_set(p, params)
    return {"ok": True, "path": p, "written": len(params)}


def tool_patch_set(a):
    p, upd = a.get("path"), a.get("updates")
    if not p or not os.path.isfile(p) or not isinstance(upd, dict):
        return {"error": "path (existing) and updates (object) required"}
    return {"ok": True, "diff": sf.patch_set(p, upd)}


def tool_clone_set(a):
    src, dst = a.get("path"), a.get("dest")
    if not src or not os.path.isfile(src) or not dst:
        return {"error": "path (existing) and dest required"}
    shutil.copyfile(src, dst)
    if isinstance(a.get("overrides"), dict):
        sf.patch_set(dst, a["overrides"])
    return {"ok": True, "dest": dst}


def tool_describe_sweep(a):
    p = a.get("path")
    return sf.describe_sweep(p) if p and os.path.isfile(p) else {"error": f"not found: {p}"}


def tool_diff_set(a):
    x, y = a.get("path_a"), a.get("path_b")
    if not (x and y and os.path.isfile(x) and os.path.isfile(y)):
        return {"error": "path_a and path_b (both existing) required"}
    return {"changed": sf.diff_set(x, y)}


def tool_list_set_files(a):
    dd = data_dir()
    if not dd:
        return {"error": "no data dir"}
    base = os.path.join(dd, "MQL5", "Profiles", "Tester")
    hits = glob.glob(os.path.join(base, "**", "*.set"), recursive=True)
    return {"dir": base, "count": len(hits),
            "files": [os.path.relpath(h, base) for h in sorted(hits)]}


# ── report analytics ─────────────────────────────────────────────────────────

def _load_deals(a):
    p = a.get("report")
    if not p or not os.path.isfile(p):
        return None, {"error": f"report not found: {p} (pass the tester .htm/.xml path)"}
    metrics, deals = rp.parse_report(p)
    return (metrics, deals), None


def tool_analyze_report(a):
    r, err = _load_deals(a)
    if err:
        return err
    metrics, deals = r
    return {"metrics_from_report": metrics, "computed": rp.summary(deals),
            "direction_bias": rp.direction_bias(deals), "streaks": rp.streaks(deals)}


def tool_list_deals(a):
    r, err = _load_deals(a)
    if err:
        return err
    _, deals = r
    lo = a.get("min_profit")
    hi = a.get("max_profit")
    out = deals
    if lo is not None:
        out = [d for d in out if d["profit"] >= float(lo)]
    if hi is not None:
        out = [d for d in out if d["profit"] <= float(hi)]
    return {"count": len(out), "deals": out[: int(a.get("limit", 100))]}


def tool_search_deals_comment(a):
    r, err = _load_deals(a)
    if err:
        return err
    _, deals = r
    q = (a.get("query") or "").lower()
    hits = [d for d in deals if q in str(d["comment"]).lower()]
    return {"query": q, "count": len(hits), "deals": hits[:200]}


def tool_search_deals_magic(a):
    r, err = _load_deals(a)
    if err:
        return err
    _, deals = r
    mg = str(a.get("magic", ""))
    hits = [d for d in deals if str(d.get("magic", "")) == mg]
    return {"magic": mg, "count": len(hits), "deals": hits[:200]}


def _analytic(fn, **kw):
    def h(a):
        r, err = _load_deals(a)
        if err:
            return err
        _, deals = r
        return fn(deals, **kw) if kw else fn(deals)
    return h


# ── backtest runner (dedicated terminal only) ────────────────────────────────

def _write_tester_ini(datadir, params, report_id):
    ini = os.path.join(datadir, "config", "terminal.ini")
    tester = "\r\n".join([
        "[Tester]",
        f"Expert={params['expert']}",
        f"Symbol={params['symbol']}",
        f"Period={params.get('timeframe', 'H1')}",
        f"Deposit={params.get('deposit', 10000):.2f}",
        f"Model={params.get('model', 1)}",
        "Optimization=0",
        f"FromDate={params['from']}",
        f"ToDate={params['to']}",
        f"Report=reports\\{report_id}.htm",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
    ])
    prev = ""
    if os.path.isfile(ini):
        with open(ini, "r", encoding="utf-16", errors="replace") as fh:
            prev = re.sub(r"(?s)\[Tester\].*?(?=\r?\n\[|\Z)", "", fh.read()).strip()
    with open(ini, "wb") as fh:
        fh.write(b"\xff\xfe" + ((prev + "\r\n" + tester + "\r\n") if prev else tester + "\r\n").encode("utf-16-le"))
    return ini


def tool_run_backtest(a):
    term = os.environ.get("MT5_BACKTEST_TERMINAL")
    datadir = os.environ.get("MT5_BACKTEST_DATADIR")
    if not term or not datadir:
        return {"error": "run_backtest needs a DEDICATED terminal — set MT5_BACKTEST_TERMINAL and "
                         "MT5_BACKTEST_DATADIR (never the live terminal; it gets killed/relaunched)."}
    for k in ("expert", "symbol", "from", "to"):
        if not a.get(k):
            return {"error": f"missing '{k}' (need expert, symbol, from=YYYY.MM.DD, to=YYYY.MM.DD)"}
    report_id = f"{datetime.now():%Y%m%d_%H%M%S}_{a['symbol']}"
    _write_tester_ini(datadir, a, report_id)
    subprocess.run(["taskkill", "/F", "/IM", "terminal64.exe"], capture_output=True)
    time.sleep(1)
    subprocess.Popen([term, "/skipupdate"])
    target = os.path.join(datadir, "reports", f"{report_id}.htm")
    deadline = time.time() + int(a.get("timeout", 180))
    while time.time() < deadline:
        if os.path.isfile(target):
            time.sleep(1)
            subprocess.run(["taskkill", "/F", "/IM", "terminal64.exe"], capture_output=True)
            metrics, deals = rp.parse_report(target)
            return {"ok": True, "report": target, "metrics": metrics,
                    "summary": rp.summary(deals), "deals": len(deals)}
        time.sleep(2)
    subprocess.run(["taskkill", "/F", "/IM", "terminal64.exe"], capture_output=True)
    return {"error": "timeout waiting for report", "expected": target,
            "hint": "symbol history may be missing — the tester needs it downloaded"}


def tool_get_backtest_status(a):
    p = a.get("report")
    return {"exists": bool(p and os.path.isfile(p)), "report": p}


def tool_system_resources(a):
    du = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
    return {"disk_total_gb": round(du.total / 2**30, 1), "disk_free_gb": round(du.free / 2**30, 1),
            "cpu_count": os.cpu_count()}


def tool_verify_setup(a):
    me = os.environ.get("MT5_METAEDITOR", DEFAULT_METAEDITOR)
    return {"metaeditor": os.path.isfile(me), "metaeditor_path": me,
            "mt5_python": mt5 is not None, "data_dir": data_dir(),
            "backtest_terminal": os.environ.get("MT5_BACKTEST_TERMINAL")}


# ── dev agility: logs, deploy, common files ──────────────────────────────────

def _read_text(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _common_dir():
    dd = data_dir()
    return os.path.join(os.path.dirname(dd), "Common", "Files") if dd else None


_LOGDIRS = {"experts": ("MQL5", "Logs"), "journal": ("Logs",), "tester": ("Tester", "logs")}


def tool_logs(a):
    """Read MT5 logs — `experts` (EA/script Print output), `journal` (terminal), or `tester`."""
    dd = data_dir()
    if not dd:
        return {"error": "no data dir (connect MT5 or set MT5_DATA_DIR)"}
    kind = a.get("kind", "experts")
    sub = _LOGDIRS.get(kind)
    if not sub:
        return {"error": f"kind must be one of {list(_LOGDIRS)}"}
    ldir = os.path.join(dd, *sub)
    if a.get("date"):
        path = os.path.join(ldir, f"{a['date']}.log")
    else:
        # Newest by mtime = the current session's log (name-sort would pick metaeditor.log).
        logs = glob.glob(os.path.join(ldir, "*.log"))
        path = max(logs, key=os.path.getmtime) if logs else None
    if not path or not os.path.isfile(path):
        return {"error": f"no log found ({path or ldir})"}
    lines = [ln for ln in _read_text(path).splitlines() if ln.strip()]
    if a.get("filter"):
        f = a["filter"].lower()
        lines = [ln for ln in lines if f in ln.lower()]
    n = int(a.get("lines", 100))
    return {"log": path, "kind": kind, "total": len(lines), "lines": lines[-n:]}


def tool_deploy_ea(a):
    """Copy a .mq5 into a terminal's MQL5\\Experts and clear the stale .ex5 (MT5 recompiles)."""
    src = a.get("path")
    if not src or not os.path.isfile(src):
        return {"error": f"path not found: {src}"}
    dd = a.get("datadir") or data_dir()
    if not dd:
        return {"error": "no data dir"}
    experts = os.path.join(dd, "MQL5", "Experts")
    os.makedirs(experts, exist_ok=True)
    name = os.path.basename(src)
    dst = os.path.join(experts, name)
    shutil.copyfile(src, dst)
    ex5 = os.path.join(experts, os.path.splitext(name)[0] + ".ex5")
    if os.path.exists(ex5):
        try:
            os.remove(ex5)
        except OSError:
            pass
    out = {"ok": True, "deployed": dst}
    if a.get("compile"):
        out["compile"] = _compile(dst)
    return out


def tool_read_common_file(a):
    """Read a file from the terminal's shared <Common>\\Files (e.g. an EA's FILE_COMMON output)."""
    cd = _common_dir()
    if not cd:
        return {"error": "no common dir"}
    name = a.get("name")
    path = os.path.join(cd, name) if name else None
    if not path or not os.path.isfile(path):
        avail = [os.path.basename(p) for p in glob.glob(os.path.join(cd, "*"))]
        return {"error": f"not found: {name}", "dir": cd, "available": avail[:50]}
    return {"path": path, "content": _read_text(path)[:8000]}


# ── registry ─────────────────────────────────────────────────────────────────

def _t(name, desc, props, required=None):
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return (name, desc, schema)

_REPORT = {"report": {"type": "string", "description": "path to a tester .htm/.xml report"}}

TOOLS = [
    _t("mt5_health", "Terminal + account summary.", {}),
    _t("mt5_account", "Full account_info.", {}),
    _t("mt5_positions", "Open positions.", {"symbol": {"type": "string"}}),
    _t("mt5_orders", "Pending orders.", {"symbol": {"type": "string"}}),
    _t("mt5_history_deals", "Closed deals over last N days.", {"days": {"type": "integer"}}),
    _t("mt5_symbol", "symbol_info.", {"symbol": {"type": "string"}}, ["symbol"]),
    _t("mt5_rates", "Recent candles.", {"symbol": {"type": "string"}, "timeframe": {"type": "string"}, "count": {"type": "integer"}}, ["symbol"]),
    _t("mt5_list_symbols", "All symbols (optional pattern).", {"pattern": {"type": "string"}}),
    _t("mt5_active_account", "Current session summary.", {}),
    _t("mt5_list_experts", "EAs in MQL5/Experts.", {"pattern": {"type": "string"}}),
    _t("mt5_list_indicators", "Indicators in MQL5/Indicators.", {"pattern": {"type": "string"}}),
    _t("mt5_list_scripts", "Scripts in MQL5/Scripts.", {"pattern": {"type": "string"}}),
    _t("mt5_compile", "Compile a .mq5 (MetaEditor).", {"path": {"type": "string"}}, ["path"]),
    _t("mt5_validate_syntax", "Syntax-only compile check.", {"path": {"type": "string"}}, ["path"]),
    _t("mt5_read_set", "Parse a .set file.", {"path": {"type": "string"}}, ["path"]),
    _t("mt5_write_set", "Write a .set file from params.", {"path": {"type": "string"}, "params": {"type": "object"}}, ["path", "params"]),
    _t("mt5_patch_set", "Update params in a .set in place.", {"path": {"type": "string"}, "updates": {"type": "object"}}, ["path", "updates"]),
    _t("mt5_clone_set", "Copy a .set with optional overrides.", {"path": {"type": "string"}, "dest": {"type": "string"}, "overrides": {"type": "object"}}, ["path", "dest"]),
    _t("mt5_describe_sweep", "Swept params + total combinations.", {"path": {"type": "string"}}, ["path"]),
    _t("mt5_diff_set", "Diff two .set files.", {"path_a": {"type": "string"}, "path_b": {"type": "string"}}, ["path_a", "path_b"]),
    _t("mt5_list_set_files", "All .set files under Profiles/Tester.", {}),
    _t("mt5_analyze_report", "Metrics + summary + direction bias + streaks.", _REPORT, ["report"]),
    _t("mt5_list_deals", "List deals (profit filters).", {**_REPORT, "min_profit": {"type": "number"}, "max_profit": {"type": "number"}, "limit": {"type": "integer"}}, ["report"]),
    _t("mt5_search_deals_comment", "Search deal comments.", {**_REPORT, "query": {"type": "string"}}, ["report", "query"]),
    _t("mt5_search_deals_magic", "Filter deals by magic.", {**_REPORT, "magic": {"type": "string"}}, ["report", "magic"]),
    _t("mt5_analyze_top_losses", "Worst losing deals.", _REPORT, ["report"]),
    _t("mt5_analyze_monthly_pnl", "Monthly P/L.", _REPORT, ["report"]),
    _t("mt5_analyze_time_performance", "P/L by hour and weekday.", _REPORT, ["report"]),
    _t("mt5_analyze_direction_bias", "Buy vs sell P/L.", _REPORT, ["report"]),
    _t("mt5_analyze_streaks", "Win/loss streaks.", _REPORT, ["report"]),
    _t("mt5_analyze_costs", "Commission + swap impact.", _REPORT, ["report"]),
    _t("mt5_analyze_profit_distribution", "Win/loss size histogram.", _REPORT, ["report"]),
    _t("mt5_analyze_volume_vs_profit", "P/L by lot size.", _REPORT, ["report"]),
    _t("mt5_analyze_layer_performance", "Grid/martingale layer P/L from comments.", _REPORT, ["report"]),
    _t("mt5_analyze_loss_sequences", "Consecutive-loss runs.", {**_REPORT, "min_len": {"type": "integer"}}, ["report"]),
    _t("mt5_run_backtest", "Headless backtest (DEDICATED terminal only).", {"expert": {"type": "string"}, "symbol": {"type": "string"}, "timeframe": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}, "deposit": {"type": "number"}, "timeout": {"type": "integer"}}, ["expert", "symbol", "from", "to"]),
    _t("mt5_get_backtest_status", "Check if a report exists.", _REPORT, ["report"]),
    _t("mt5_system_resources", "Disk/CPU.", {}),
    _t("mt5_verify_setup", "Check MetaEditor/MT5/data-dir/backtest wiring.", {}),
    _t("mt5_logs", "Read MT5 logs: kind=experts|journal|tester (see EA Print output), optional date/lines/filter.", {"kind": {"type": "string"}, "date": {"type": "string"}, "lines": {"type": "integer"}, "filter": {"type": "string"}}),
    _t("mt5_deploy_ea", "Copy a .mq5 into a terminal's MQL5/Experts (+clear .ex5); optional compile.", {"path": {"type": "string"}, "datadir": {"type": "string"}, "compile": {"type": "boolean"}}, ["path"]),
    _t("mt5_read_common_file", "Read a file from <Common>/Files (e.g. an EA's FILE_COMMON output).", {"name": {"type": "string"}}, ["name"]),
]

HANDLERS = {
    "mt5_health": tool_health, "mt5_account": tool_account, "mt5_positions": tool_positions,
    "mt5_orders": tool_orders, "mt5_history_deals": tool_history_deals, "mt5_symbol": tool_symbol,
    "mt5_rates": tool_rates, "mt5_list_symbols": tool_list_symbols, "mt5_active_account": tool_active_account,
    "mt5_list_experts": tool_list_experts, "mt5_list_indicators": tool_list_indicators,
    "mt5_list_scripts": tool_list_scripts, "mt5_compile": tool_compile, "mt5_validate_syntax": tool_validate_syntax,
    "mt5_read_set": tool_read_set, "mt5_write_set": tool_write_set, "mt5_patch_set": tool_patch_set,
    "mt5_clone_set": tool_clone_set, "mt5_describe_sweep": tool_describe_sweep, "mt5_diff_set": tool_diff_set,
    "mt5_list_set_files": tool_list_set_files, "mt5_analyze_report": tool_analyze_report,
    "mt5_list_deals": tool_list_deals, "mt5_search_deals_comment": tool_search_deals_comment,
    "mt5_search_deals_magic": tool_search_deals_magic,
    "mt5_analyze_top_losses": _analytic(rp.top_losses), "mt5_analyze_monthly_pnl": _analytic(rp.monthly_pnl),
    "mt5_analyze_time_performance": _analytic(rp.time_performance),
    "mt5_analyze_direction_bias": _analytic(rp.direction_bias), "mt5_analyze_streaks": _analytic(rp.streaks),
    "mt5_analyze_costs": _analytic(rp.costs), "mt5_analyze_profit_distribution": _analytic(rp.profit_distribution),
    "mt5_analyze_volume_vs_profit": _analytic(rp.volume_vs_profit),
    "mt5_analyze_layer_performance": _analytic(rp.layer_performance),
    "mt5_analyze_loss_sequences": _analytic(rp.loss_sequences),
    "mt5_run_backtest": tool_run_backtest, "mt5_get_backtest_status": tool_get_backtest_status,
    "mt5_system_resources": tool_system_resources, "mt5_verify_setup": tool_verify_setup,
    "mt5_logs": tool_logs, "mt5_deploy_ea": tool_deploy_ea, "mt5_read_common_file": tool_read_common_file,
}


# ── MCP stdio loop ───────────────────────────────────────────────────────────

def result(i, r):
    return {"jsonrpc": "2.0", "id": i, "result": r}


def error(i, code, msg):
    return {"jsonrpc": "2.0", "id": i, "error": {"code": code, "message": msg}}


def handle(msg):
    m, i = msg.get("method"), msg.get("id")
    if m == "initialize":
        return result(i, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                          "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}})
    if m in ("notifications/initialized", "initialized"):
        return None
    if m == "ping":
        return result(i, {})
    if m == "tools/list":
        return result(i, {"tools": [{"name": n, "description": d, "inputSchema": s} for (n, d, s) in TOOLS]})
    if m == "tools/call":
        p = msg.get("params") or {}
        fn = HANDLERS.get(p.get("name"))
        if not fn:
            return error(i, -32601, f"unknown tool: {p.get('name')}")
        try:
            data = fn(p.get("arguments") or {})
            is_err = isinstance(data, dict) and "error" in data
            return result(i, {"content": [{"type": "text", "text": json.dumps(data, default=str, indent=2)}],
                              "isError": bool(is_err)})
        except Exception as ex:
            return result(i, {"content": [{"type": "text", "text": f"tool error: {ex}"}], "isError": True})
    return error(i, -32601, f"method not found: {m}") if i is not None else None


def main():
    log(f"v{SERVER_VERSION} starting (python {sys.version.split()[0]}, MetaTrader5 {'ok' if mt5 else 'MISSING'}, {len(TOOLS)} tools)")
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except Exception as ex:
            log("bad json:", ex)
            continue
        resp = handle(msg)
        if resp is not None:
            out.write((json.dumps(resp) + "\n").encode("utf-8"))
            out.flush()
    if _initialized and mt5 is not None:
        mt5.shutdown()


if __name__ == "__main__":
    main()
