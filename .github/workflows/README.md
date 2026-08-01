cat > README.md << 'EOF'
# dbt-sentinel

**dbt tells you a test failed. It doesn't tell you why.** `dbt-sentinel` reads dbt's
run artifacts after a build, pulls the compiled SQL and a sample of the rows that
actually failed, and asks an LLM — grounded strictly in that evidence — for a root-cause
explanation and a concrete fix. It flags low-confidence answers instead of bluffing.

[![CI](https://github.com/qraza/dbt-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/qraza/dbt-sentinel/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## What this is

A data-quality companion for dbt. When `dbt build` reports a failing test, you normally
get a name and a row count — and then you go digging. `dbt-sentinel` closes that gap: it
joins `run_results.json` and `manifest.json` to find each failure, runs the test's own
compiled SQL against the warehouse to sample the offending rows, and sends that concrete
evidence to an LLM with a prompt engineered to stay grounded in it. The output is a
structured verdict — root cause, suggested fix, confidence, and the specific evidence
behind it — rendered to the terminal and, optionally, to markdown for a PR comment.

It reads what dbt already produced; it does not re-run your tests or mutate your data
(the warehouse is opened read-only).

<!-- TODO: drop a GIF of `sentinel analyze` here — this is the first thing a reader sees. -->

## Quickstart

```bash
uv sync
export ANTHROPIC_API_KEY="sk-ant-..."
export ANTHROPIC_MODEL="claude-sonnet-4-5"   # a model your key can call

# point it at any dbt project's target/ dir and its warehouse
uv run sentinel analyze \
  --target-dir path/to/dbt/target \
  --db path/to/warehouse.duckdb \
  --markdown report.md
```

If all tests pass, it says so and exits. If not, you get a diagnosis per failure.

## How it works

dbt build ─► target/run_results.json (which tests failed, row counts)
target/manifest.json (compiled SQL, guarded model/column, test type)
│
▼
parse.py join artifacts → FailingTest records
│
▼
context.py run the test's compiled SQL against DuckDB,
│ sample the offending rows (read-only, capped)
▼
analyze.py grounded prompt → LLM → {root_cause, fix,
│ confidence, evidence}
▼
report.py Rich terminal panel + markdown
(via cli.py: sentinel analyze)

The design choice that keeps it small and robust: **it reads dbt's artifacts rather than
re-running tests.** Everything the tool needs — the compiled SQL, the failing-row count,
the guarded model — is already in the JSON dbt writes on every build.

## Grounding is the whole point

An LLM asked "why did my dbt test fail?" with no context will invent a plausible-sounding
story. `dbt-sentinel` instead feeds the model only real evidence — the compiled SQL, the
column schema, and a sample of the actual failing rows — and instructs it to ground every
claim in that evidence or report low confidence.

The difference on a real failure (a taxi-trip speed test where `avg_speed_mph` was computed
with a `600` multiplier instead of `60`):

**Naive prompt — "this test failed, why?" (no evidence):**

> <!-- TODO: paste the vague, ungrounded answer from the naive run here -->

**Grounded (dbt-sentinel):**

> The `avg_speed_mph` formula uses a multiplier of 600 instead of 60 … a trip of 9.24
> miles in 52 minutes yields (9.24/52)×60 = 10.66 mph (plausible), but with the bug
> (9.24/52)×600 = 106.62 — which exactly matches the failing row. The same 10× inflation
> is confirmed across all sampled rows. **Confidence: high.**

The grounded answer cites the exact multiplier, recomputes a real row to prove it, and
commits to a confidence level. That's the difference between a guess and a diagnosis.

See [`docs/example-analysis.md`](docs/example-analysis.md) for the full output.

## Design decisions

**Read artifacts, don't re-run tests.** dbt writes `run_results.json` and `manifest.json`
on every build. Parsing them is faster, needs no dbt invocation, and means the tool works
against any already-completed run — including one from CI.

**Sample rows by running the test's compiled SQL.** Some models are materialized
*ephemeral* and don't exist as tables to query directly. The test's compiled SQL has that
logic inlined, so running it returns exactly the offending rows regardless of how the
guarded model is materialized.

**Ground the model, then trust but verify.** The prompt contains only evidence, and the
model is told to say "insufficient evidence" rather than speculate. Confidence is surfaced,
not hidden, so a low-confidence answer reads as low-confidence.

**Read-only, always.** The warehouse connection is opened read-only — the tool inspects,
it never mutates.

## Limitations

- Warehouse support is DuckDB today; the sampling step is where another warehouse would
  plug in.
- One dbt project per run.
- The quality of the diagnosis depends on the model and on how much signal the sampled
  rows carry; low-signal failures correctly come back as low confidence.

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run pytest -v
```

CI runs the same lint + tests on every push. All tests use committed fixtures and a mocked
API, so they need no warehouse and no API key.

---

Built by [Qamar Raza](https://github.com/qraza).
EOF