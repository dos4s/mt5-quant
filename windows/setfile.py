"""MT5 tester `.set` files — UTF-16LE, one param per line.

Plain:  Name=Value
Swept:  Name=Value||start||step||stop||enabled   (enabled: Y/N or 1/0)
The optimizer sweeps Name from start..stop step step when enabled.
"""
import os


def read_set(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-16", errors="replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") \
        else raw.decode("utf-8", errors="replace")
    params = {}
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith(";") or "=" not in line:
            continue
        name, rest = line.split("=", 1)
        parts = rest.split("||")
        entry = {"value": parts[0]}
        if len(parts) >= 5:
            entry.update({"start": parts[1], "step": parts[2], "stop": parts[3],
                          "optimize": parts[4] in ("Y", "1", "true", "True")})
        params[name.strip()] = entry
    return params


def write_set(path, params):
    lines = []
    for name, e in params.items():
        if isinstance(e, dict):
            if "start" in e:
                flag = "Y" if e.get("optimize") else "N"
                lines.append(f"{name}={e['value']}||{e['start']}||{e['step']}||{e['stop']}||{flag}")
            else:
                lines.append(f"{name}={e['value']}")
        else:
            lines.append(f"{name}={e}")
    data = ("\r\n".join(lines) + "\r\n").encode("utf-16-le")
    with open(path, "wb") as fh:
        fh.write(b"\xff\xfe" + data)


def patch_set(path, updates):
    """Update specific params in place; return the diff. `updates` = {name: new_value}."""
    params = read_set(path)
    diff = {}
    for name, new_val in updates.items():
        old = params.get(name, {}).get("value") if isinstance(params.get(name), dict) else None
        if name not in params:
            params[name] = {"value": str(new_val)}
        else:
            params[name]["value"] = str(new_val)
        diff[name] = {"old": old, "new": str(new_val)}
    write_set(path, params)
    return diff


def describe_sweep(path):
    params = read_set(path)
    swept = {}
    combos = 1
    for name, e in params.items():
        if isinstance(e, dict) and e.get("optimize") and "start" in e:
            try:
                start, step, stop = float(e["start"]), float(e["step"]), float(e["stop"])
                n = int((stop - start) / step) + 1 if step else 1
            except (ValueError, ZeroDivisionError):
                n = 1
            swept[name] = {"start": e["start"], "step": e["step"], "stop": e["stop"], "values": max(n, 1)}
            combos *= max(n, 1)
    return {"swept_params": swept, "total_combinations": combos if swept else 0}


def diff_set(path_a, path_b):
    a, b = read_set(path_a), read_set(path_b)
    changed = {}
    for name in set(a) | set(b):
        va = a.get(name, {}).get("value") if isinstance(a.get(name), dict) else None
        vb = b.get(name, {}).get("value") if isinstance(b.get(name), dict) else None
        if va != vb:
            changed[name] = {"a": va, "b": vb}
    return changed
