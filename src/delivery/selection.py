"""Which tests a change needs, and what skipping the others costs.

A synthetic monorepo: 150 modules in four layers (core, libraries, services, apps), each depending on a few modules
below it, with 3-8 tests per module. Tests exercise their module and everything it depends on at runtime. Some of
those runtime dependencies are not declared in the build graph (configuration, plugins, reflection): about 6% of
modules have one. 1.5% of tests are flaky. A bug either breaks the changed module's own behaviour (its tests and its
users' tests can see it) or, 40% of the time, only its contract with the modules that use it (only their tests can).

Strategies compared on the same changes:
- `run_all`: every test, every change;
- `same_directory`: tests of modules in the directories the change touched (a common shortcut);
- `impact` (this platform): tests of every module that depends on a changed module, directly or transitively, by
  the declared build graph; all tests when a core module changes.

Every strategy retries tests known to be flaky (seen failing on unchanged code) once before failing the pipeline;
false reds are reported with and without that retry.

Undeclared dependencies are where impact analysis is blind; the nightly full run is what catches those, and the
evaluation counts how many bugs wait for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

LAYERS = (("core", 10), ("libs", 40), ("services", 60), ("apps", 40))
HIDDEN_SHARE = 0.06
FLAKY_SHARE = 0.015
FLAKE_RATE = 0.05
DETECT = 0.5  # chance that one test exercising a buggy module hits the bug
BUG_SHARE = 0.3
INTEGRATION_SHARE = 0.4  # bugs that only break the modules using the changed one (a contract change)
STRATEGIES = ("run_all", "same_directory", "impact")


@dataclass
class Repo:
    seed: int
    modules: list[str] = field(default_factory=list)
    layer: list[int] = field(default_factory=list)
    declared: list[set[int]] = field(default_factory=list)  # build-graph dependencies
    actual: list[set[int]] = field(default_factory=list)  # runtime dependencies (declared + hidden)
    tests: list[int] = field(default_factory=list)  # test -> module it belongs to
    flaky: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        g = np.random.default_rng(self.seed)
        for li, (name, count) in enumerate(LAYERS):
            groups = max(count // 5, 1)
            for i in range(count):
                self.modules.append(f"{name}/{name[:-1] if name.endswith('s') else name}{i % groups}/m{i}")
                self.layer.append(li)
        n = len(self.modules)
        below = [[j for j in range(n) if self.layer[j] < self.layer[i]] for i in range(n)]
        for i in range(n):
            k = 0 if not below[i] else int(g.integers(1, 5))
            deps = set(int(x) for x in g.choice(below[i], size=min(k, len(below[i])), replace=False)) if k else set()
            self.declared.append(deps)
        self.actual = [set(d) for d in self.declared]
        for i in range(n):
            if below[i] and g.random() < HIDDEN_SHARE:
                self.actual[i].add(int(g.choice(below[i])))
        for i in range(n):
            self.tests += [i] * int(g.integers(3, 9))
        self.flaky = {t for t in range(len(self.tests)) if g.random() < FLAKY_SHARE}

    def closure(self, graph: list[set[int]], i: int) -> set[int]:
        seen, stack = {i}, [i]
        while stack:
            for d in graph[stack.pop()]:
                if d not in seen:
                    seen.add(d)
                    stack.append(d)
        return seen

    def exercised(self) -> list[set[int]]:
        """For each test, the modules it actually runs."""
        cl = [self.closure(self.actual, i) for i in range(len(self.modules))]
        return [cl[m] for m in self.tests]

    def dependents(self, changed: set[int]) -> set[int]:
        """Modules that depend on any changed module, by the declared graph (including the changed ones)."""
        rev: list[set[int]] = [set() for _ in self.modules]
        for i, deps in enumerate(self.declared):
            for d in deps:
                rev[d].add(i)
        return set().union(*(self.closure(rev, c) for c in changed)) if changed else set()

    def directory(self, i: int) -> str:
        return self.modules[i].rsplit("/", 1)[0]


def known_flaky(repo: Repo, history_runs: int, g: np.random.Generator) -> set[int]:
    """Tests seen failing on unchanged code in past runs."""
    flagged = set()
    for _ in range(history_runs):
        for t in repo.flaky:
            if g.random() < FLAKE_RATE:
                flagged.add(t)
    return flagged


def select(repo: Repo, changed: set[int], strategy: str) -> list[int]:
    if strategy == "run_all" or (strategy == "impact" and any(repo.layer[c] == 0 for c in changed)):
        return list(range(len(repo.tests)))
    if strategy == "same_directory":
        dirs = {repo.directory(c) for c in changed}
        return [t for t, m in enumerate(repo.tests) if repo.directory(m) in dirs]
    impacted = repo.dependents(changed)
    return [t for t, m in enumerate(repo.tests) if m in impacted]


def evaluate(seed: int = 21, changes: int = 2000, history_runs: int = 20) -> dict[str, object]:
    repo = Repo(seed)
    g = np.random.default_rng(seed + 1)
    flagged = known_flaky(repo, history_runs, g)
    exercised = repo.exercised()
    upper = [3, 2, 1, 0]  # changes land mostly in apps and services
    weights = np.array([upper[repo.layer[i]] + 0.3 for i in range(len(repo.modules))], dtype=float)
    weights /= weights.sum()
    stats: dict[str, dict[str, float]] = {
        s: {"tests": 0, "bugs_caught": 0, "false_red": 0, "false_red_no_retry": 0} for s in STRATEGIES
    }
    bugs = catchable = 0
    for _ in range(changes):
        changed = {int(x) for x in g.choice(len(repo.modules), size=int(g.integers(1, 5)), replace=False, p=weights)}
        buggy = int(g.choice(sorted(changed))) if g.random() < BUG_SHARE else None
        integration = g.random() < INTEGRATION_SHARE
        # one draw per test: would it hit the bug, would it flake (a retry draws again)
        hits = g.random(len(repo.tests)) < DETECT
        flake = g.random(len(repo.tests)) < FLAKE_RATE
        retry = g.random(len(repo.tests)) < FLAKE_RATE
        failing_on_bug = {
            t
            for t in range(len(repo.tests))
            if buggy is not None and buggy in exercised[t] and hits[t] and not (integration and repo.tests[t] == buggy)
        }
        if buggy is not None:
            bugs += 1
            catchable += int(bool(failing_on_bug))
        for s in STRATEGIES:
            chosen = select(repo, changed, s)
            stats[s]["tests"] += len(chosen)
            caught = any(t in failing_on_bug for t in chosen)
            flaked = [t for t in chosen if t in repo.flaky and flake[t]]
            red_after_retry = any(t not in flagged or retry[t] for t in flaked)
            stats[s]["bugs_caught"] += int(caught)
            stats[s]["false_red"] += int(red_after_retry and not caught)
            stats[s]["false_red_no_retry"] += int(bool(flaked) and not caught)
    total = len(repo.tests) * changes
    out: dict[str, object] = {
        "modules": len(repo.modules),
        "tests": len(repo.tests),
        "changes": changes,
        "bugs": bugs,
        "catchable_by_full_run": catchable,
        "hidden_dependencies": sum(len(a - d) for a, d in zip(repo.actual, repo.declared, strict=True)),
        "flaky_tests": len(repo.flaky),
        "flaky_known": len(flagged & repo.flaky),
    }
    for s, v in stats.items():
        out[s] = {
            "tests_run_share": round(v["tests"] / total, 4),
            "bugs_caught": int(v["bugs_caught"]),
            "bugs_caught_share": round(v["bugs_caught"] / max(catchable, 1), 4),
            "waiting_for_nightly": catchable - int(v["bugs_caught"]),
            "false_red": int(v["false_red"]),
            "false_red_share": round(v["false_red"] / changes, 4),
            "false_red_no_retry_share": round(v["false_red_no_retry"] / changes, 4),
        }
    return out


REPO_SEEDS = (21, 22, 23)


def evaluate_repos(seeds: tuple[int, ...] = REPO_SEEDS) -> dict[str, object]:
    """Three generated repositories, each with 2,000 changes; totals across them."""
    runs = [evaluate(s) for s in seeds]
    total: dict[str, object] = {"repos": len(runs), "changes": sum(int(str(r["changes"])) for r in runs)}
    catchable = sum(int(str(r["catchable_by_full_run"])) for r in runs)
    total["bugs"] = sum(int(str(r["bugs"])) for r in runs)
    total["catchable_by_full_run"] = catchable
    total["hidden_dependencies"] = sum(int(str(r["hidden_dependencies"])) for r in runs)
    for st in STRATEGIES:
        rows = [r[st] for r in runs]
        assert all(isinstance(x, dict) for x in rows)
        caught = sum(int(x["bugs_caught"]) for x in rows if isinstance(x, dict))
        total[st] = {
            "tests_run_share": round(
                sum(float(x["tests_run_share"]) for x in rows if isinstance(x, dict)) / len(rows), 4
            ),
            "bugs_caught": caught,
            "bugs_caught_share": round(caught / max(catchable, 1), 4),
            "waiting_for_nightly": catchable - caught,
            "false_red_share": round(
                sum(float(x["false_red_share"]) for x in rows if isinstance(x, dict)) / len(rows), 4
            ),
            "false_red_no_retry_share": round(
                sum(float(x["false_red_no_retry_share"]) for x in rows if isinstance(x, dict)) / len(rows), 4
            ),
        }
    total["per_repo"] = runs
    return total
