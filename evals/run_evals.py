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
    outcome: str
    confidence: str
    cause: str


def build(fixture: Path) -> bool:
    """Run dbt build inside a fixture. A failing test is expected, so ignore exit code."""
    subprocess.run(
        ["dbt", "build", "--project-dir", str(fixture), "--profiles-dir", str(fixture)],
        capture_output=True,
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


def run_fixture(fixture: Path) -> Result | None:
    expected = yaml.safe_load((fixture / "expected.yaml").read_text())
    if not build(fixture):
        print(f"  {fixture.name}: dbt build produced no artifacts", file=sys.stderr)
        return None

    failures = parse(fixture / "target")
    if not failures:
        print(f"  {fixture.name}: no failing tests -- fixture is not broken", file=sys.stderr)
        return None

    wh = open_warehouse(duckdb_path=fixture / "eval.duckdb")
    try:
        ctx = gather_context(failures[0], wh)
    finally:
        wh.close()

    a = analyze(ctx)
    return Result(
        name=expected["name"],
        outcome=score(expected, a.root_cause, a.confidence),
        confidence=a.confidence,
        cause=a.root_cause[:100],
    )


def main() -> None:
    results: list[Result] = []
    for fixture in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        print(f"running {fixture.name}...")
        r = run_fixture(fixture)
        if r:
            results.append(r)

    print("\n{:<22} {:<18} {:<12}".format("fixture", "outcome", "confidence"))
    print("-" * 54)
    for r in results:
        print(f"{r.name:<22} {r.outcome:<18} {r.confidence:<12}")

    strong = [r for r in results if r.outcome in {"correct", "overconfident", "underconfident"}]
    weak = [r for r in results if r.outcome in {"honest_uncertain", "wrong_but_flagged"}]
    over = [r for r in results if r.outcome == "overconfident"]

    print()
    if strong:
        acc = sum(r.outcome == "correct" for r in strong) / len(strong)
        print(f"Accuracy (strong signal):    {acc:.2f}  ({len(strong)} fixtures)")
    if weak:
        cal = sum(r.outcome == "honest_uncertain" for r in weak) / len(weak)
        print(f"Calibration (weak signal):   {cal:.2f}  ({len(weak)} fixtures)")
    print(f"Overconfident errors:        {len(over)}")


if __name__ == "__main__":
    main()
