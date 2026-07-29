"""MT5 backtest report + deals parsing and analytics — the Windows-native port of
mt5-quant's extract.rs + analytics/analyze.rs. Operates on the `.htm`/`.htm.xml` report the
Strategy Tester writes (no Wine). Pure data processing, so every analytic works on any report
without re-running a backtest.
"""
import re
import os
from collections import defaultdict
from datetime import datetime

_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(r"(?s)<tr[^>]*>(.*?)</tr>", re.I)
_CELL = re.compile(r"(?s)<td[^>]*>(.*?)</td>", re.I)

# MT5 HTML deal column order (extract.rs):
# Time|Deal|Symbol|Type|Direction|Volume|Price|Order|Commission|Swap|Profit|Balance|Comment|Magic
_COLS = ["time", "deal", "symbol", "type", "entry", "volume", "price", "order",
         "commission", "swap", "profit", "balance", "comment", "magic"]
_FLOATS = {"volume", "price", "commission", "swap", "profit", "balance"}


def _read_text(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _strip(html):
    return _TAG.sub("", html).replace("&nbsp;", " ").strip()


def parse_report(path):
    """Return (metrics: dict, deals: list[dict]) from an MT5 tester .htm/.xml report."""
    text = _read_text(path)
    metrics = parse_metrics(text)
    deals = parse_deals(text)
    return metrics, deals


def parse_deals(text):
    rows = [m.group(0) for m in _ROW.finditer(text)]
    # Find the deals header row (contains Deal + Symbol).
    start = None
    for i, row in enumerate(rows):
        low = row.lower()
        if (">deal<" in low or ">deal </" in low) and (">symbol<" in low or ">symbol </" in low):
            start = i + 1
            break
    if start is None:
        for i, row in enumerate(rows):
            low = row.lower()
            if ">time<" in low and ">volume<" in low and ">profit<" in low:
                start = i + 1
                break
    if start is None:
        return []

    deals = []
    for row in rows[start:]:
        cells = [_strip(c).replace(",", "") for c in _CELL.findall(row)]
        if len(cells) < 3 or not cells[0].strip():
            continue
        tcell = cells[3].strip().lower() if len(cells) > 3 else ""
        if tcell in ("balance", "credit", "type"):
            continue
        if len(cells) > 1 and cells[1].strip().lower() == "deal":
            continue
        if len(cells) < 2 or not cells[1].strip():
            continue
        d = {}
        for idx, name in enumerate(_COLS):
            val = cells[idx] if idx < len(cells) else ""
            if name in _FLOATS:
                try:
                    val = float(val)
                except ValueError:
                    val = 0.0
            d[name] = val
        deals.append(d)
    return deals


# Metric labels in the MT5 HTML report → our keys.
_METRIC_LABELS = {
    "total net profit": "net_profit",
    "gross profit": "gross_profit",
    "gross loss": "gross_loss",
    "profit factor": "profit_factor",
    "expected payoff": "expected_payoff",
    "recovery factor": "recovery_factor",
    "sharpe ratio": "sharpe_ratio",
    "balance drawdown maximal": "max_dd",
    "equity drawdown maximal": "max_equity_dd",
    "total trades": "total_trades",
    "total deals": "total_deals",
}


def parse_metrics(text):
    """Best-effort scrape of the summary metrics from the report header table."""
    out = {}
    # The report lists "Label:</td><td>value</td>" pairs (values may carry % or currency).
    flat = re.sub(r"(?s)<[^>]+>", "\t", text)
    flat = flat.replace("&nbsp;", " ")
    lines = [ln.strip() for ln in flat.split("\t") if ln.strip()]
    joined = " \t ".join(lines).lower()
    for label, key in _METRIC_LABELS.items():
        m = re.search(re.escape(label) + r"[:\s\t]+([-\d][\d\s.,%()]*)", joined)
        if m:
            raw = m.group(1).split("(")[0].replace("%", "").replace(" ", "").replace(",", "")
            try:
                out[key] = float(raw)
            except ValueError:
                pass
    return out


# ── analytics over the deals list ────────────────────────────────────────────

def _dt(s):
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _closed(deals):
    """Deals that realised P/L (exit deals). MT5 marks entry='out'/'in/out'."""
    res = [d for d in deals if str(d.get("entry", "")).lower() in ("out", "in/out", "inout")]
    return res or deals  # fall back to all if the report has no entry column


def summary(deals):
    cl = _closed(deals)
    profits = [d["profit"] + d["commission"] + d["swap"] for d in cl]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    net = sum(profits)
    gp, gl = sum(wins), sum(losses)
    return {
        "deals": len(deals), "closed": len(cl),
        "net_profit": round(net, 2),
        "gross_profit": round(gp, 2), "gross_loss": round(gl, 2),
        "profit_factor": round(gp / abs(gl), 3) if gl else None,
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(cl), 1) if cl else 0,
        "avg_win": round(gp / len(wins), 2) if wins else 0,
        "avg_loss": round(gl / len(losses), 2) if losses else 0,
    }


def direction_bias(deals):
    by = defaultdict(lambda: {"count": 0, "profit": 0.0})
    for d in _closed(deals):
        k = str(d.get("type", "")).lower() or "?"
        by[k]["count"] += 1
        by[k]["profit"] += d["profit"] + d["commission"] + d["swap"]
    return {k: {"count": v["count"], "profit": round(v["profit"], 2)} for k, v in by.items()}


def streaks(deals):
    cur = 0
    best_win = best_loss = 0
    for d in _closed(deals):
        p = d["profit"] + d["commission"] + d["swap"]
        if p > 0:
            cur = cur + 1 if cur > 0 else 1
            best_win = max(best_win, cur)
        elif p < 0:
            cur = cur - 1 if cur < 0 else -1
            best_loss = min(best_loss, cur)
    return {"max_win_streak": best_win, "max_loss_streak": -best_loss}


def top_losses(deals, n=10):
    cl = sorted(_closed(deals), key=lambda d: d["profit"] + d["commission"] + d["swap"])
    return [{"time": d["time"], "symbol": d["symbol"], "type": d["type"], "volume": d["volume"],
             "net": round(d["profit"] + d["commission"] + d["swap"], 2), "comment": d["comment"]}
            for d in cl[:n]]


def monthly_pnl(deals):
    by = defaultdict(float)
    for d in _closed(deals):
        dt = _dt(d["time"])
        if dt:
            by[dt.strftime("%Y-%m")] += d["profit"] + d["commission"] + d["swap"]
    return {k: round(v, 2) for k, v in sorted(by.items())}


def time_performance(deals):
    hour = defaultdict(float)
    dow = defaultdict(float)
    for d in _closed(deals):
        dt = _dt(d["time"])
        if dt:
            net = d["profit"] + d["commission"] + d["swap"]
            hour[dt.hour] += net
            dow[dt.strftime("%a")] += net
    return {"by_hour": {h: round(v, 2) for h, v in sorted(hour.items())},
            "by_weekday": {k: round(v, 2) for k, v in dow.items()}}


def costs(deals):
    comm = sum(d["commission"] for d in deals)
    swap = sum(d["swap"] for d in deals)
    gross = sum(d["profit"] for d in _closed(deals))
    return {"commission": round(comm, 2), "swap": round(swap, 2),
            "total_costs": round(comm + swap, 2), "gross_profit": round(gross, 2),
            "cost_pct_of_gross": round(100 * (abs(comm) + abs(swap)) / abs(gross), 1) if gross else None}


def profit_distribution(deals):
    buckets = {"big_loss": 0, "med_loss": 0, "small_loss": 0,
               "small_win": 0, "med_win": 0, "big_win": 0}
    for d in _closed(deals):
        p = d["profit"] + d["commission"] + d["swap"]
        a = abs(p)
        tier = "small" if a < 50 else ("med" if a < 200 else "big")
        if p >= 0:
            buckets[f"{tier}_win"] += 1
        else:
            buckets[f"{tier}_loss"] += 1
    return buckets


def volume_vs_profit(deals):
    by = defaultdict(lambda: {"count": 0, "profit": 0.0})
    for d in _closed(deals):
        k = f"{d['volume']:.2f}"
        by[k]["count"] += 1
        by[k]["profit"] += d["profit"] + d["commission"] + d["swap"]
    return {k: {"count": v["count"], "profit": round(v["profit"], 2)} for k, v in sorted(by.items())}


def layer_performance(deals):
    """Grid/martingale layer analysis from comments (e.g. 'Layer #3')."""
    by = defaultdict(lambda: {"count": 0, "profit": 0.0})
    for d in _closed(deals):
        m = re.search(r"(?:layer|level|grid|l)\s*#?\s*(\d+)", str(d["comment"]), re.I)
        k = m.group(1) if m else "none"
        by[k]["count"] += 1
        by[k]["profit"] += d["profit"] + d["commission"] + d["swap"]
    return {k: {"count": v["count"], "profit": round(v["profit"], 2)} for k, v in sorted(by.items())}


def loss_sequences(deals, min_len=3):
    seqs = []
    run = []
    for d in _closed(deals):
        p = d["profit"] + d["commission"] + d["swap"]
        if p < 0:
            run.append(d)
        else:
            if len(run) >= min_len:
                seqs.append({"length": len(run), "start": run[0]["time"], "end": run[-1]["time"],
                             "total": round(sum(x["profit"] + x["commission"] + x["swap"] for x in run), 2)})
            run = []
    if len(run) >= min_len:
        seqs.append({"length": len(run), "start": run[0]["time"], "end": run[-1]["time"],
                     "total": round(sum(x["profit"] + x["commission"] + x["swap"] for x in run), 2)})
    return seqs
