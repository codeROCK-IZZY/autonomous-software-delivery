"""Markdown and HTML versions of the evaluation."""

from __future__ import annotations

import html
from typing import Any

POLICY = {
    "big_bang": "Big-bang deploy + service alert",
    "threshold_canary": "Canary vs pre-deploy thresholds",
    "progressive": "Progressive delivery (this platform)",
}
BANK = {
    "dev": "Tuning worlds",
    "heldout": "Held-out worlds",
    "fresh": "Held-back regression kinds",
}
KIND = {
    "errors": "Errors everywhere",
    "region_errors": "Errors in one region",
    "endpoint_latency": "One endpoint slower",
    "tail_latency": "Stalls on one endpoint",
    "leak": "Errors growing (leak)",
    "cell_errors": "Errors in one cell",
    "bursty_errors": "Errors in bursts",
}
STRATEGY = {
    "run_all": "Every test",
    "same_directory": "Tests in the changed directories",
    "impact": "Impact analysis (this platform)",
}


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _test_banks(r: dict[str, Any]) -> list[str]:
    return [b for b in BANK if b != "dev" and b in r["banks"]]


def _combined(r: dict[str, Any], policy: str) -> dict[str, Any]:
    rows = [r["banks"][b][policy] for b in _test_banks(r)]
    bad = sum(x["bad"] for x in rows)
    good = sum(x["good"] for x in rows)
    return {
        "bad": bad,
        "good": good,
        "caught": sum(x["caught"] for x in rows),
        "early": sum(x["caught_by_5pct"] for x in rows),
        "harm": sum(x["harm"] for x in rows),
        "false": sum(x["false_rollbacks"] for x in rows),
        "minutes": rows[0]["minutes_to_full"],
    }


def markdown(r: dict[str, Any]) -> str:
    out = [
        "# Autonomous software delivery: evaluation",
        "",
        f"{r['deploys_per_bank']} deploys per bank, about half of them bad. Canary settings were tuned on the tuning "
        "worlds only; the held-out worlds and the held-back regression kinds were run once, after the settings were "
        "frozen.",
        "",
        "## Held-out worlds and held-back kinds together",
        "",
        "| Policy | Bad versions caught | Caught at 5% of traffic or less | Requests harmed | Healthy versions rolled "
        "back | Minutes to full rollout |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for p, label in POLICY.items():
        c = _combined(r, p)
        out.append(
            f"| {label} | {c['caught']} of {c['bad']} ({_pct(c['caught'] / c['bad'])}) | {c['early']} | "
            f"{c['harm']:,} | {c['false']} of {c['good']} | {c['minutes']:.0f} |"
        )
    out += ["", "## By bank", "", "| Bank | Policy | Caught | Harm | False rollbacks |", "|---|---|---:|---:|---:|"]
    for b, bl in BANK.items():
        for p, label in POLICY.items():
            s = r["banks"][b][p]
            out.append(f"| {bl} | {label} | {_pct(s['caught_share'])} | {s['harm']:,} | {s['false_rollbacks']} |")
    out += [
        "",
        "## Bad versions caught, by kind (held-out and held-back)",
        "",
        "| Regression | " + " | ".join(POLICY.values()) + " |",
        "|---|" + "---:|" * len(POLICY),
    ]
    kinds: dict[str, dict[str, list[int]]] = {}
    for b in _test_banks(r):
        for p in POLICY:
            for k, v in r["banks"][b][p]["by_kind"].items():
                kinds.setdefault(k, {}).setdefault(p, [0, 0])
                kinds[k][p][0] += v["caught"]
                kinds[k][p][1] += v["deploys"]
    for k, per in kinds.items():
        out.append(f"| {KIND.get(k, k)} | " + " | ".join(f"{per[p][0]} of {per[p][1]}" for p in POLICY) + " |")
    sel = r["selection"]
    out += [
        "",
        f"## Test selection ({sel['repos']} repositories, {sel['changes']:,} changes, "
        f"{sel['catchable_by_full_run']:,} bugs a full run catches)",
        "",
        "| Strategy | Tests run | Bugs caught | Waiting for the nightly run | Red without a bug (retry known "
        "flaky / no retry) |",
        "|---|---:|---:|---:|---:|",
    ]
    for s, label in STRATEGY.items():
        x = sel[s]
        out.append(
            f"| {label} | {_pct(x['tests_run_share'])} | {_pct(x['bugs_caught_share'])} | "
            f"{x['waiting_for_nightly']} | {_pct(x['false_red_share'])} / {_pct(x['false_red_no_retry_share'])} |"
        )
    out += ["", "## Gate (regression floors, not targets)", ""]
    for k, v in r["checks"].items():
        out.append(f"- {'PASS' if r['passed'][k] else 'FAIL'} {k}: {v} (limit {r['gate'][k]})")
    return "\n".join(out) + "\n"


CSS = """
:root{--bg:#0c1016;--panel:#141b25;--line:#253042;--ink:#e8edf4;--ink2:#97a3b6;--ok:#3ccf8e;--bad:#ff6b6b;--mid:#f5b54a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);line-height:1.5;font-family:Inter,system-ui,
sans-serif}main{max-width:1180px;margin:0 auto;padding:34px 28px 56px}h1{font-size:28px;margin:0 0 4px}h2{font-size:18px;
margin:28px 0 12px}.sub{color:var(--ink2);margin:0 0 20px}.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px}.tile b{display:block;
font-size:28px}.tile span{color:var(--ink2);font-size:13px}table{width:100%;border-collapse:collapse;background:var(--panel);
border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:13.5px}th,td{padding:8px 12px;
border-bottom:1px solid var(--line);text-align:right}th:first-child,td:first-child{text-align:left}th{color:var(--ink2);
font-weight:500;font-size:12px}td:not(:first-child){white-space:nowrap}.good{color:var(--ok);font-weight:600}
.bad{color:var(--bad);font-weight:600}.mid{color:var(--mid);font-weight:600}.cols{display:grid;grid-template-columns:1fr 1fr;
gap:18px}@media(max-width:800px){.tiles,.cols{grid-template-columns:1fr 1fr}}
"""


def _c(x: float) -> str:
    return "good" if x >= 0.95 else ("mid" if x >= 0.8 else "bad")


def html_report(r: dict[str, Any]) -> str:
    e = html.escape
    ours, bb, th = _combined(r, "progressive"), _combined(r, "big_bang"), _combined(r, "threshold_canary")
    sel = r["selection"]
    tiles = [
        (
            _pct(ours["caught"] / ours["bad"]),
            f"of bad versions rolled back (big-bang + alert: {_pct(bb['caught'] / bb['bad'])})",
        ),
        (f"{ours['false']} of {ours['good']}", f"healthy versions rolled back (threshold canary: {th['false']})"),
        (_pct(ours["harm"] / max(bb["harm"], 1)), "of the requests big-bang deploys harmed"),
        (
            _pct(sel["impact"]["tests_run_share"]),
            f"of tests run per change, catching {_pct(sel['impact']['bugs_caught_share'])} of bugs",
        ),
    ]
    out = [
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
        f"initial-scale=1'><title>Software delivery evaluation</title><style>{CSS}</style><main>"
        "<h1>Autonomous software delivery: evaluation</h1><p class='sub'>Deploys of good and bad versions into a "
        "simulated service with background incidents. Canary settings tuned on separate worlds; held-out worlds and "
        "held-back regression kinds below.</p><div class='tiles'>"
    ]
    out += [f"<div class='tile'><b>{e(v)}</b><span>{e(t)}</span></div>" for v, t in tiles]
    out.append(
        "</div><h2>Rollout policies (held-out + held-back)</h2><table><tr><th>Policy</th><th>Bad caught</th>"
        "<th>Caught at ≤5%</th><th>Requests harmed</th><th>Healthy rolled back</th><th>Minutes to 100%</th></tr>"
    )
    for p, label in POLICY.items():
        c = _combined(r, p)
        share = c["caught"] / c["bad"]
        out.append(
            f"<tr><td>{e(label)}</td><td class='{_c(share)}'>{_pct(share)}</td><td>{c['early']}</td>"
            f"<td>{c['harm']:,}</td><td class='{'good' if c['false'] <= 2 else 'bad'}'>{c['false']} of "
            f"{c['good']}</td><td>{c['minutes']:.0f}</td></tr>"
        )
    out.append(
        "</table><div class='cols'><div><h2>Caught by kind</h2><table><tr><th>Regression</th><th>Big-bang</th>"
        "<th>Threshold</th><th>Progressive</th></tr>"
    )
    kinds: dict[str, dict[str, list[int]]] = {}
    for b in _test_banks(r):
        for p in POLICY:
            for k, v in r["banks"][b][p]["by_kind"].items():
                kinds.setdefault(k, {}).setdefault(p, [0, 0])
                kinds[k][p][0] += v["caught"]
                kinds[k][p][1] += v["deploys"]
    for k, per in kinds.items():
        cells = "".join(f"<td class='{_c(per[p][0] / max(per[p][1], 1))}'>{per[p][0]}/{per[p][1]}</td>" for p in POLICY)
        out.append(f"<tr><td>{e(KIND.get(k, k))}</td>{cells}</tr>")
    out.append(
        "</table></div><div><h2>Test selection</h2><table><tr><th>Strategy</th><th>Tests run</th>"
        "<th>Bugs caught</th><th>Red, no bug</th></tr>"
    )
    for s, label in STRATEGY.items():
        x = sel[s]
        out.append(
            f"<tr><td>{e(label)}</td><td>{_pct(x['tests_run_share'])}</td><td class='{_c(x['bugs_caught_share'])}'>"
            f"{_pct(x['bugs_caught_share'])}</td><td>{_pct(x['false_red_share'])}</td></tr>"
        )
    out.append(
        f"</table><p class='sub' style='margin-top:10px'>{sel['repos']} generated repositories, "
        f"{sel['changes']:,} changes; known-flaky tests retried once for every strategy.</p></div></div>"
        "<h2>Gate (regression limits)</h2><table><tr><th>Check</th><th>Value</th><th>Limit</th><th>Result</th></tr>"
    )
    for k, v in r["checks"].items():
        ok = r["passed"][k]
        out.append(
            f"<tr><td>{e(k)}</td><td>{e(str(v))}</td><td>{e(str(r['gate'][k]))}</td>"
            f"<td class='{'good' if ok else 'bad'}'>{'pass' if ok else 'fail'}</td></tr>"
        )
    out.append("</table></main></html>")
    return "".join(out) + "\n"
