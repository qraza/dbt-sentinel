"""Throwaway smoke test: parse -> context -> analyze on the real failing fixture.

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    export ANTHROPIC_MODEL="<the model string your taxi repo uses>"
    uv run python smoke_analyze.py \
        tests/fixtures/failing \
        ~/development/capstone-data-tool/data/capstone.duckdb

Delete this file once analyze.py is wired into the real CLI (Phase 5).
"""

import sys

from dbt_sentinel.analyze import analyze
from dbt_sentinel.context import connect, gather_context
from dbt_sentinel.parse import parse

target_dir = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/failing"
db_path = sys.argv[2] if len(sys.argv) > 2 else "data/capstone.duckdb"

failures = parse(target_dir)
if not failures:
    print("No failing tests found in", target_dir)
    raise SystemExit(0)

test = failures[0]
print(f"Analyzing: {test.test_name}  ({test.failure_count} failing rows)\n")

con = connect(db_path)
ctx = gather_context(test, con)
analysis = analyze(ctx)

print("ROOT CAUSE")
print(" ", analysis.root_cause, "\n")
print("SUGGESTED FIX")
print(" ", analysis.suggested_fix, "\n")
print(f"CONFIDENCE: {analysis.confidence}")
print("EVIDENCE")
print(" ", analysis.evidence)