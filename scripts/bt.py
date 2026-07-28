#!/usr/bin/env python3
"""
Connected-tester backtest harness for mt5-quant + AXI.

WHY: the mt5-quant MCP's run_backtest uses an OFFLINE local agent that can't see
AXI data (pip-denominated, ~0 bars). The terminal's MAIN tester run CONNECTED via
a [Tester] startup config works correctly (real data, USD). This drives that path
and parses MT5's own HTML report.

Usage:
  python3 bt.py EXPERT SYMBOL PERIOD FROM TO [DEPOSIT] [LEVERAGE] [MODEL]
  e.g. python3 bt.py ETHRegimeEA_v2 ETHUSD H1 2025.07.01 2026.06.01 5000 200 1

EXPERT = compiled EA name (must exist at Experts/<name>/<name>.ex5; compile via
MCP compile_ea first). Prints a JSON line of metrics and appends to rankings.jsonl.
"""
import sys, os, time, subprocess, glob, re, json

MT5 = os.path.expanduser("~/.mt5/drive_c/Program Files/MetaTrader 5")
WINEPREFIX = os.path.expanduser("~/.mt5")
RESEARCH = os.path.expanduser("~/Documents/code/mt5-quant/research")
TLOG_DIR = os.path.join(MT5, "Tester", "logs")


def _kill_terminal():
    # NB: never `pkill -f terminal64` from a shell whose cmdline contains it (self-kill).
    subprocess.run(["pkill", "-x", "terminal64.exe"], check=False)
    out = subprocess.run(["ps", "-eo", "pid,comm"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "terminal64" in line:
            try: os.kill(int(line.split()[0]), 9)
            except Exception: pass
    time.sleep(1)


def _read_report(path):
    raw = open(path, "rb").read()
    for enc in ("utf-16-le", "utf-8", "latin-1"):
        try:
            txt = raw.decode(enc)
            if "Total Net Profit" in txt or "Profit Factor" in txt:
                return txt
        except Exception:
            continue
    return raw.decode("latin-1", "ignore")


def _flat(txt):
    txt = re.sub(r"<[^>]+>", "\n", txt)
    return [l.strip() for l in txt.splitlines() if l.strip()]


def _after(lines, label):
    for i, l in enumerate(lines):
        if l.lower().startswith(label.lower()):
            return lines[i + 1] if i + 1 < len(lines) else ""
    return ""


def _num(s):
    s = s.replace(" ", " ").replace(" ", "").replace(",", "")
    m = re.search(r"-?\d+\.?\d*", s)
    return float(m.group()) if m else None


def _dd(s):  # "28.48 (0.56%)" -> (28.48, 0.56)
    m = re.search(r"(-?[\d ,]+\.?\d*)\s*\(([\d.]+)%\)", s.replace(" ", " "))
    if m: return _num(m.group(1)), float(m.group(2))
    return _num(s), None


def extract_trades(path):
    """Parse report Deals table -> list of (time_str, profit, balance) for closing ('out') deals."""
    raw = open(path, "rb").read()
    txt = raw.decode("utf-16-le", "ignore")
    if "Deals" not in txt:
        txt = raw.decode("utf-8", "ignore")
    i = txt.find(">Deals<")
    seg = txt[i:] if i >= 0 else txt
    out = []
    for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr.group(1), re.S)]
        if len(cells) >= 12 and cells[4] == "out":
            p = _num(cells[10].replace(" ", "").replace(" ", "").replace(",", ""))
            b = _num(cells[11].replace(" ", "").replace(" ", "").replace(",", ""))
            if p is not None:
                out.append((cells[0], p, b))
    return out


def run_bt(expert, symbol, period, frm, to, deposit=5000, leverage=200, model=1, timeout=300, setfile=None):
    report = "bt_run"
    rpt_htm = os.path.join(MT5, report + ".htm")
    for p in glob.glob(os.path.join(MT5, report + ".*")):
        try: os.remove(p)
        except Exception: pass
    params = ""
    if setfile:  # copy .set into Profiles/Tester and reference by name
        import shutil
        pt = os.path.join(MT5, "MQL5", "Profiles", "Tester")
        os.makedirs(pt, exist_ok=True)
        sname = os.path.basename(setfile)
        shutil.copy(os.path.expanduser(setfile), os.path.join(pt, sname))
        params = f"ExpertParameters={sname}\n"
    ini = (f"[Tester]\nExpert={expert}\\{expert}\nSymbol={symbol}\nPeriod={period}\n"
           f"Model={model}\nFromDate={frm}\nToDate={to}\nDeposit={deposit}\n"
           f"Leverage={leverage}\nOptimization=0\nVisual=0\n{params}Report={report}\n"
           f"ReplaceReport=1\nShutdownTerminal=1\n")
    ini_path = os.path.join(RESEARCH, "bt_run.ini")
    open(ini_path, "w").write(ini)
    win_ini = "Z:" + ini_path.replace("/", "\\")

    _kill_terminal()
    tlog = os.path.join(TLOG_DIR, time.strftime("%Y%m%d") + ".log")
    start = os.path.getsize(tlog) if os.path.exists(tlog) else 0
    env = dict(os.environ, WINEPREFIX=WINEPREFIX, DISPLAY=":1")
    subprocess.Popen(["setsid", "wine", "terminal64.exe", "/portable", f"/config:{win_ini}"],
                     cwd=MT5, env=env, stdout=open("/tmp/bt_run.log", "w"), stderr=subprocess.STDOUT)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(rpt_htm) and time.time() - os.path.getmtime(rpt_htm) > 2:
            break
        time.sleep(2)
    res = {"expert": expert, "symbol": symbol, "period": period, "from": frm, "to": to,
           "deposit": deposit, "leverage": leverage, "model": model}
    # denomination guard from tester log
    if os.path.exists(tlog):
        new = open(tlog, "rb").read()[start:].decode("latin-1", "ignore")
        fb = re.search(r"final balance ([\d.\- ]+) (USD|pips)", new)
        bars = re.search(r"(\d+) bars generated", new)
        if fb: res["final_balance"], res["denomination"] = _num(fb.group(1)), fb.group(2)
        if bars: res["bars"] = int(bars.group(1))
    if not os.path.exists(rpt_htm):
        res["error"] = "no report produced"
        return res
    L = _flat(_read_report(rpt_htm))
    res["net_profit"] = _num(_after(L, "Total Net Profit:"))
    res["profit_factor"] = _num(_after(L, "Profit Factor:"))
    res["expected_payoff"] = _num(_after(L, "Expected Payoff:"))
    res["sharpe"] = _num(_after(L, "Sharpe Ratio:"))
    res["recovery_factor"] = _num(_after(L, "Recovery Factor:"))
    res["trades"] = _num(_after(L, "Total Trades:"))
    bdd = _dd(_after(L, "Balance Drawdown Maximal:"))
    edd = _dd(_after(L, "Equity Drawdown Maximal:"))
    res["bal_dd_abs"], res["bal_dd_pct"] = bdd
    res["eq_dd_abs"], res["eq_dd_pct"] = edd
    res["init_deposit"] = _num(_after(L, "Initial Deposit:"))
    # dump per-trade data for the Monte Carlo simulator
    trades = extract_trades(rpt_htm)
    res["trades_extracted"] = len(trades)
    if trades:
        tdir = os.path.join(RESEARCH, "trades")
        os.makedirs(tdir, exist_ok=True)
        label = f"{expert}_{symbol}_{frm.replace('.', '')}_{to.replace('.', '')}"
        with open(os.path.join(tdir, label + ".csv"), "w") as f:
            f.write("time,profit,balance\n")
            for t, p, b in trades:
                f.write(f"{t},{p},{b}\n")
    return res


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 5:
        print(__doc__); sys.exit(1)
    kw = dict(zip(["deposit", "leverage", "model"], [int(x) for x in a[5:8]]))
    r = run_bt(a[0], a[1], a[2], a[3], a[4], **kw)
    print(json.dumps(r))
    with open(os.path.join(RESEARCH, "rankings.jsonl"), "a") as f:
        f.write(json.dumps(r) + "\n")
