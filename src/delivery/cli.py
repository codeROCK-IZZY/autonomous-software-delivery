"""Command line: simulate a rollout, serve the app, tune the canary analysis, run the evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deliver", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rollout", help="roll a version out into the simulated service and print what happened")
    r.add_argument("--policy", default="progressive", choices=["progressive", "threshold_canary", "big_bang"])
    r.add_argument("--regression", default="none")
    r.add_argument("--factor", type=float, default=3.0)
    r.add_argument("--region")
    r.add_argument("--endpoint")
    r.add_argument("--seed", type=int, default=7)
    s = sub.add_parser("serve", help="the app with a week of demo deploys")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    t = sub.add_parser("tune", help="the grid search on the tuning worlds that chose the canary settings")
    t.add_argument("--deploys", type=int, default=200)
    e = sub.add_parser("evaluate", help="three rollout policies on three banks, and test selection")
    e.add_argument("--deploys", type=int, default=200)
    e.add_argument("--out", default="eval/reports")
    e.add_argument("--gate", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "evaluate":
        from .evaluate import run_all, save

        result = run_all(args.deploys)
        save(result, Path(args.out))
        print((Path(args.out) / "report.md").read_text(encoding="utf-8"))
        return 1 if args.gate and not result["ok"] else 0
    if args.cmd == "tune":
        from .canary import Settings
        from .evaluate import tune

        grid = [Settings(family_alpha=a) for a in (0.01, 0.05, 0.2)]
        for row in tune(args.deploys, grid):
            print(json.dumps(row))
        return 0
    if args.cmd == "serve":
        import uvicorn

        from .app import demo_app

        uvicorn.run(demo_app(), host=args.host, port=args.port)
        return 0
    from .fleet import Regression
    from .notes import plain_note
    from .rollout import run

    o = run(args.seed, Regression(args.regression, args.factor, args.region, args.endpoint), args.policy)
    print(
        json.dumps(
            {
                "rolled_back": o.rolled_back,
                "rollback_minute": o.rollback_minute,
                "max_fraction": o.max_fraction,
                "promoted_minute": o.promoted_minute,
                "requests_harmed": round(o.harm),
                "reason": o.reason,
                "note": plain_note(o).summary if o.rolled_back else None,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
