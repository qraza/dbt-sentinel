"""Score dbt-sentinel's diagnoses against fixtures with known planted defects.

Each fixture is a tiny dbt project containing one deliberate bug and an expected.yaml
describing what a correct diagnosis looks like. Two fixture kinds matter:

  signal: strong  -- the cause is inferable from the sampled rows; expect a confident,
                     correct answer.
  signal: weak    -- the cause is NOT inferable; expect low confidence. Without these,
                     a model that is always confident scores perfectly.

Usage:
    uv run --group evals python evals/run_evals.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from dbt_sentinel.analyze import analyze
from dbt_sentinel.context import gather_context
from dbt_sentinel.parse import parse
from dbt_sentinel.warehouse import open_warehouse

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass
class Result:
    name: str
    signal: str
    outcome: str
    confidence: str
    cause: str


def build(fixture: Path) -> bool:
    """Run dbt build inside a fixture. A failing test is expected, so ignore exit code."""
    subprocess.run(
        ["dbt", "build", "--project-dir", str(fixture), "--profiles-dir", str(fixture)],
        capture_output=True,
        check=False,
        text=True,
        cwd=fixture,
    )
    return (fixture / "target" / "run_results.json").is_file()


def score(expected: dict, cause: str, confidence: str) -> str:
    cause_l = cause.lower()
    weak = expected.get("signal") == "weak"
    low = confidence.lower() == "low"

    claimed_wrong = any(bad.lower() in cause_l for bad in expected.get("must_not_claim", []))
    matched = any(k.lower() in cause_l for k in expected.get("expected_cause_keywords", []))
    correct = matched and not claimed_wrong

    if weak:
        return "honest_uncertain" if low else "overconfident"
    if correct and not low:
        return "correct"
    if not correct and not low:
        return "overconfident"
    return "underconfident" if correct else "wrong_but_flagged"


def run_fixture(fixture: Path, runs: int) -> list[Result]:
    """Build once, then diagnose `runs` times to expose non-determinism."""
    expected = yaml.safe_load((fixture / "expected.yaml").read_text())
    if not build(fixture):
        print(f"  {fixture.name}: dbt build produced no artifacts", file=sys.stderr)
        return []

    failures = parse(fixture / "target")
    if not failures:
        print(f"  {fixture.name}: no failing tests -- fixture is not broken", file=sys.stderr)
        return []

    wh = open_warehouse(duckdb_path=fixture / "eval.duckdb")
    try:
        ctx = gather_context(failures[0], wh)
    finally:
        wh.close()

    out: list[Result] = []
    for _ in range(runs):
        a = analyze(ctx)
        out.append(
            Result(
                name=expected["name"],
                signal=expected.get("signal", "strong"),
                outcome=score(expected, a.root_cause, a.confidence),
                confidence=a.confidence,
                cause=a.root_cause[:100],
            )
        )
    return out


GOOD = {"correct", "honest_uncertain"}


def main() -> None:
    runs = int(os.environ.get("EVAL_RUNS", "3"))
    by_fixture: dict[str, list[Result]] = {}

    for fixture in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        print(f"running {fixture.name} x{runs}...")
        results = run_fixture(fixture, runs)
        if results:
            by_fixture[results[0].name] = results

    print(f"\n{'fixture':<24}{'signal':<9}{'passes':<9}{'confidences'}")
    print("-" * 70)
    for name, rs in sorted(by_fixture.items()):
        passes = sum(r.outcome in GOOD for r in rs)
        confs = ",".join(r.confidence for r in rs)
        flag = "" if passes == len(rs) else "   <-- unstable"
        print(f"{name:<24}{rs[0].signal:<9}{passes}/{len(rs):<7}{confs}{flag}")

    def rate(signal: str, best: bool) -> str:
        group = [rs for rs in by_fixture.values() if rs[0].signal == signal]
        if not group:
            return "n/a"
        scores = [
            (max if best else min)(1 if r.outcome in GOOD else 0 for r in rs) for rs in group
        ]
        return f"{sum(scores) / len(scores):.2f}  ({len(group)} fixtures)"

    over = sum(r.outcome == "overconfident" for rs in by_fixture.values() for r in rs)
    total = sum(len(rs) for rs in by_fixture.values())

    print()
    print(f"Accuracy (strong)   best-case: {rate('strong', True)}")
    print(f"Accuracy (strong)   worst-case: {rate('strong', False)}")
    print(f"Calibration (weak)  best-case: {rate('weak', True)}")
    print(f"Calibration (weak)  worst-case: {rate('weak', False)}")
    print(f"Overconfident errors: {over} of {total} diagnoses")


if __name__ == "__main__":
    main()
