"""Web app and API: deployment history, each rollout's stages and evidence, and new deploys."""

from __future__ import annotations

import html
from datetime import datetime
from statistics import median
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .fleet import ENDPOINTS, REGIONS, Regression
from .platform import SERVICES, Deploy, Pipeline
from .policy import Change

e = html.escape

CSS = """
:root{--bg:#0c1016;--panel:#141b25;--line:#253042;--ink:#e8edf4;--ink2:#97a3b6;--ok:#3ccf8e;--bad:#ff6b6b;--mid:#f5b54a;
--new:#8b7bff;--old:#7d8aa0}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 Inter,system-ui,sans-serif}a{color:inherit}header{padding:14px 26px;border-bottom:1px solid var(--line);
display:flex;gap:18px;align-items:baseline}header b{font-size:17px}header span{color:var(--ink2)}main{padding:20px 26px 48px;
max-width:1280px;margin:0 auto}.tiles{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:18px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}.tile b{display:block;
font-size:26px}.tile span{color:var(--ink2);font-size:12.5px}table{width:100%;border-collapse:collapse;
background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}th,td{padding:8px 12px;
border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--ink2);font-weight:500;font-size:12px}
.b{display:inline-block;padding:1px 9px;border-radius:99px;font-size:11.5px;font-weight:600}.promoted{background:#3ccf8e22;
color:var(--ok)}.rolled{background:#ff6b6b22;color:var(--bad)}.blocked{background:#f5b54a22;color:var(--mid)}
.muted{color:var(--ink2)}h1{font-size:22px;margin:0 0 2px}h2{font-size:13px;color:var(--ink2);text-transform:uppercase;
letter-spacing:.05em;margin:22px 0 8px}.sub{color:var(--ink2);margin:0 0 14px}.stages{display:flex;gap:8px;flex-wrap:wrap}
.st{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-width:110px}
.st b{display:block}.st.ok{border-color:#3ccf8e66}.st.fail{border-color:#ff6b6b99;background:#ff6b6b14}
.st.skip{opacity:.45}.box{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px}
.cols{display:grid;grid-template-columns:1.4fr 1fr;gap:18px}svg text{fill:var(--ink2);font-size:11px}
.legend{display:flex;gap:16px;color:var(--ink2);font-size:12.5px;margin:6px 0}.sw{display:inline-block;width:12px;
height:3px;vertical-align:middle;margin-right:6px}code{background:#0f141c;padding:1px 6px;border-radius:5px}
@media(max-width:900px){.cols,.tiles{grid-template-columns:1fr}}
"""


class NewDeploy(BaseModel):
    service: str
    title: str
    kind: str = "feature"
    modules: list[str] = []
    author: str = "api"
    approver: str | None = None
    emergency: bool = False
    regression: str = "none"  # simulation only: the hidden defect, if any
    region: str | None = None
    endpoint: str | None = None
    factor: float = 3.0


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
        f"initial-scale=1'><title>{e(title)}</title><style>{CSS}</style><header><b>Delivery</b><span>progressive "
        f"rollouts with automatic rollback</span><a href='/' class='muted'>Deployments</a></header><main>{body}"
        "</main></html>"
    )


def _badge(d: Deploy) -> str:
    cls = {"promoted": "promoted", "rolled back": "rolled", "blocked": "blocked"}[d.status]
    return f"<span class='b {cls}'>{d.status}</span>"


def _rolling(counts: list[tuple[int, int, int, int]], new: bool, width: int = 10) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(counts)):
        win = counts[max(0, i - width + 1) : i + 1]
        n = sum(c[2] if new else c[0] for c in win)
        err = sum(c[3] if new else c[1] for c in win)
        out.append(err / n * 100 if n >= 300 else None)  # too few requests: leave a gap
    return out


def chart(d: Deploy) -> str:
    """Error rate of each version (5-minute rolling) above the new version's share of traffic, same time axis."""
    o = d.outcome
    assert o is not None
    w, h1, h2, left, top = 760, 200, 70, 46, 10
    n = len(o.counts)
    new, old = _rolling(o.counts, True), _rolling(o.counts, False)
    vals = [v for v in new + old if v is not None]
    ymax = max(max(vals, default=1.0) * 1.15, 0.5)

    def x(i: int) -> float:
        return left + (w - left - 10) * i / max(n - 1, 1)

    def y(v: float) -> float:
        return top + h1 - h1 * v / ymax

    def path(series: list[float | None]) -> str:
        parts, pen = [], False
        for i, v in enumerate(series):
            if v is None:
                pen = False
                continue
            parts.append(f"{'L' if pen else 'M'}{x(i):.1f},{y(v):.1f}")
            pen = True
        return " ".join(parts)

    grid = "".join(
        f"<line x1='{left}' x2='{w - 10}' y1='{y(t):.1f}' y2='{y(t):.1f}' stroke='#253042'/>"
        f"<text x='{left - 6}' y='{y(t) + 4:.1f}' text-anchor='end'>{t:.1f}%</text>"
        for t in (ymax * k / 4 for k in range(5))
    )
    rb = ""
    if o.rollback_minute is not None:
        rx = x(o.rollback_minute)
        rb = (
            f"<line x1='{rx:.1f}' x2='{rx:.1f}' y1='{top}' y2='{top + h1 + h2 + 20}' stroke='#ff6b6b' "
            f"stroke-dasharray='4 3'/><text x='{rx + 4:.1f}' y='{top + 12}' style='fill:#ff6b6b'>rollback</text>"
        )
    base2 = top + h1 + 20
    frac_path = "M" + " L".join(f"{x(i):.1f},{base2 + h2 - h2 * f:.1f}" for i, f in enumerate(o.fraction))
    frac_area = f"{frac_path} L{x(n - 1):.1f},{base2 + h2} L{x(0):.1f},{base2 + h2} Z"
    steps = "".join(
        f"<text x='{x(i) + 3:.1f}' y='{base2 + h2 - h2 * f - 4:.1f}' style='fill:#b9aefc'>{f:.0%}</text>"
        for i, f in enumerate(o.fraction)
        if f > 0 and (i == 0 or o.fraction[i - 1] != f)
    )
    ticks = "".join(
        f"<text x='{x(t):.1f}' y='{base2 + h2 + 16}' text-anchor='middle'>{t}m</text>" for t in range(0, n, 15)
    )
    return (
        f"<svg viewBox='0 0 {w} {base2 + h2 + 24}' width='100%' role='img' aria-label='Error rate of the new and "
        f"old versions, and the new version share of traffic'>{grid}"
        f"<path d='{path(old)}' fill='none' stroke='#7d8aa0' stroke-width='2'/>"
        f"<path d='{path(new)}' fill='none' stroke='#8b7bff' stroke-width='2'/>"
        f"<path d='{frac_area}' fill='#8b7bff33' stroke='#8b7bff' stroke-width='1.5'/>"
        f"<text x='{left - 6}' y='{base2 + 10}' text-anchor='end'>100%</text>"
        f"<text x='{left - 6}' y='{base2 + h2}' text-anchor='end'>0%</text>{steps}{ticks}{rb}</svg>"
    )


def create_app(pipeline: Pipeline) -> FastAPI:
    app = FastAPI(title="Autonomous software delivery")

    def get(did: str) -> Deploy:
        d = pipeline.deploys.get(did)
        if d is None:
            raise HTTPException(404, "no such deploy")
        return d

    def as_dict(d: Deploy) -> dict[str, Any]:
        o = d.outcome
        return {
            "id": d.id,
            "service": d.change.service,
            "title": d.change.title,
            "kind": d.change.kind,
            "author": d.change.author,
            "status": d.status,
            "risk": d.decision.risk,
            "guardrails": d.decision.reasons,
            "tests_selected": d.tests_selected,
            "tests_total": d.tests_total,
            "max_fraction": o.max_fraction if o else 0.0,
            "rollback_minute": o.rollback_minute if o else None,
            "findings": [f.describe() for f in o.findings] if o else [],
            "note": None
            if d.note is None
            else {
                "summary": d.note.summary,
                "affected": d.note.affected,
                "next_steps": d.note.next_steps,
                "source": d.note.source,
            },
        }

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "deploys": len(pipeline.deploys), "claude": pipeline.writer is not None}

    @app.get("/api/deploys")
    def api_list() -> list[dict[str, Any]]:
        return [as_dict(d) for d in pipeline.deploys.values()]

    @app.get("/api/deploys/{did}")
    def api_one(did: str) -> dict[str, Any]:
        return as_dict(get(did))

    @app.post("/api/deploys")
    def api_ship(body: NewDeploy) -> dict[str, Any]:
        if body.service not in SERVICES:
            raise HTTPException(422, f"service is one of {', '.join(SERVICES)}")
        if body.region is not None and body.region not in REGIONS:
            raise HTTPException(422, f"region is one of {', '.join(REGIONS)}")
        if body.endpoint is not None and body.endpoint not in ENDPOINTS:
            raise HTTPException(422, f"endpoint is one of {', '.join(ENDPOINTS)}")
        change = Change(
            body.service,
            body.title,
            body.kind,
            tuple(body.modules),
            body.author,
            datetime.now(),
            body.emergency,
            body.approver,
        )
        reg = Regression(body.regression, body.factor, body.region, body.endpoint)
        return as_dict(pipeline.ship(change, reg, seed=len(pipeline.deploys) + 900))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        ds = list(pipeline.deploys.values())
        done = [
            d.outcome.promoted_minute
            for d in ds
            if d.outcome and not d.outcome.rolled_back and d.outcome.promoted_minute is not None
        ]
        tiles = [
            (str(len(ds)), "deploys this week"),
            (str(sum(d.status == "promoted" for d in ds)), "promoted to everyone"),
            (str(sum(d.status == "rolled back" for d in ds)), "rolled back automatically"),
            (str(sum(d.status == "blocked" for d in ds)), "stopped by a guardrail"),
            (f"{median(done):.0f} min" if done else "—", "median time to full rollout"),
        ]
        rows = ""
        for d in reversed(ds):
            o = d.outcome
            reach = "—" if o is None else f"{o.max_fraction:.0%}"
            why = (d.note.summary if d.note else "; ".join(d.decision.reasons)) or "all checks passed"
            rows += (
                f"<tr><td><a href='/deploys/{d.id}'>{e(d.id)}</a><div class='muted'>{d.started:%a %d %b %H:%M}</div>"
                f"</td><td>{e(d.change.service)}</td><td>{e(d.change.title)}<div class='muted'>{e(d.change.kind)} · "
                f"{e(d.change.author)}</div></td><td>{_badge(d)}</td><td>{reach}</td><td>{d.tests_selected} of "
                f"{d.tests_total}</td><td class='muted' style='max-width:380px'>{e(why)}</td></tr>"
            )
        body = (
            "<div class='tiles'>"
            + "".join(f"<div class='tile'><b>{e(v)}</b><span>{e(t)}</span></div>" for v, t in tiles)
            + "</div><table><tr><th>Deploy</th><th>Service</th>"
            "<th>Change</th><th>Outcome</th><th>Peak traffic</th><th>Tests run</th><th>Why</th></tr>"
            f"{rows}</table>"
        )
        return _page("Deployments", body)

    @app.get("/deploys/{did}", response_class=HTMLResponse)
    def deploy(did: str) -> str:
        d = get(did)
        o = d.outcome
        stages = [("Guardrails", "ok" if d.decision.allowed else "fail", "passed" if d.decision.allowed else "blocked")]
        stages.append(("Build", "ok" if o else "skip", "done" if o else "—"))
        stages.append(("Tests", "ok" if o else "skip", f"{d.tests_selected} of {d.tests_total}"))
        if o:
            plan = d.decision.plan
            reached = sorted({f for f in o.fraction if f > 0})
            for frac, bake in plan:
                if o.rolled_back and frac == o.max_fraction:
                    stages.append((f"{frac:.0%}", "fail", "rolled back"))
                elif frac in reached:
                    stages.append((f"{frac:.0%}", "ok", f"{bake} min"))
                else:
                    stages.append((f"{frac:.0%}", "skip", "—"))
            stages.append(
                (
                    "100%",
                    "ok" if not o.rolled_back else "skip",
                    f"at {o.promoted_minute} min" if not o.rolled_back else "—",
                )
            )
        st = "".join(f"<div class='st {c}'><b>{e(n)}</b><span class='muted'>{e(t)}</span></div>" for n, c, t in stages)
        guard = "".join(f"<li>{e(r)}</li>" for r in d.decision.reasons) or "<li>no guardrail applied</li>"
        main = ""
        if o:
            findings = "".join(
                f"<tr><td>{e(f.segment)}</td><td>{e(f.metric)}</td><td>{e(f.describe())}</td></tr>" for f in o.findings
            )
            main = (
                f"<h2>Canary vs control</h2><div class='box'><div class='legend'><span><i class='sw' "
                "style='background:#8b7bff'></i>new version error rate (10-min)</span><span><i class='sw' "
                "style='background:#7d8aa0'></i>old version error rate</span><span><i class='sw' "
                "style='background:#8b7bff55;height:10px'></i>new version share of traffic</span></div>"
                f"{chart(d)}</div>"
            )
            if findings:
                main += (
                    "<h2>What the analysis found</h2><table><tr><th>Where</th><th>Metric</th><th>Evidence</th>"
                    f"</tr>{findings}</table>"
                )
        note = ""
        if d.note:
            steps = "".join(f"<li>{e(s)}</li>" for s in d.note.next_steps)
            src = "Claude, checked against the findings" if d.note.source == "claude" else "built from the findings"
            note = (
                f"<h2>Rollback note <span class='muted' style='text-transform:none'>({src})</span></h2><div "
                f"class='box'><p>{e(d.note.summary)}</p><ul>{steps}</ul></div>"
            )
        mods = ", ".join(f"<code>{e(m)}</code>" for m in d.change.modules)
        body = (
            f"<h1>{e(d.change.title)} {_badge(d)}</h1><p class='sub'>{e(d.id)} · {e(d.change.service)} · "
            f"{e(d.change.kind)} by {e(d.change.author)} · risk {e(d.decision.risk)} · {mods}</p>"
            f"<div class='stages'>{st}</div><div class='cols'><div>{main}</div><div><h2>Guardrails</h2>"
            f"<div class='box'><ul>{guard}</ul></div>{note}</div></div>"
        )
        return _page(d.change.title, body)

    return app


def demo_app() -> FastAPI:
    import os

    from .notes import Writer
    from .platform import demo

    writer = Writer() if os.environ.get("ANTHROPIC_API_KEY") else None
    return create_app(demo(writer))
