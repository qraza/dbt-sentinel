# dbt-sentinel

**dbt tells you a test failed. It doesn't tell you why.** `dbt-sentinel` reads dbt's
run artifacts after a build, pulls the compiled SQL and a sample of the rows that
actually failed, and asks an LLM — grounded strictly in that evidence — for a root-cause
explanation and a concrete fix. It flags low-confidence answers instead of bluffing.

[![CI](https://github.com/qraza/dbt-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/qraza/dbt-sentinel/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dbt-sentinel)](https://pypi.org/project/dbt-sentinel/)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/qraza/dbt-sentinel/blob/main/LICENSE)

![sentinel analyze demo](https://raw.githubusercontent.com/qraza/dbt-sentinel/main/docs/demo.gif)

Runs in CI too — on every pull request it posts its grounded diagnosis as a comment:

![PR comment](https://raw.githubusercontent.com/qraza/dbt-sentinel/main/docs/pr-comment.png)

> **See also [sentinel-mcp](https://github.com/qraza/sentinel-mcp)** — an MCP server built
> on this library, so an AI assistant can answer "what broke and why?" without you
> running a command.

## What this is

A data-quality companion for dbt. When `dbt build` reports a failing test, you normally
get a name and a row count — then you go digging. `dbt-sentinel` closes that gap: it joins
`run_results.json` and `manifest.json` to find each failure, runs the test's own compiled
SQL against the warehouse to sample the offending rows, and sends that concrete evidence to
an LLM with a prompt engineered to stay grounded in it. The output is a structured verdict —
root cause, suggested fix, confidence, and the evidence behind it — in the terminal and,
optionally, markdown for a PR comment. It reads what dbt already produced; it never re-runs
your tests or mutates data (the warehouse is opened read-only).

It also tracks failures across runs — each one is flagged **new**, **recurring**, or
**regressed**, and `sentinel history <test-id>` prints a test's full timeline, so you can
see whether something just broke or has been broken for weeks.

## Quickstart

```bash
uv sync
export ANTHROPIC_API_KEY="sk-ant-..."
export ANTHROPIC_MODEL="claude-opus-5"

uv run sentinel analyze \
  --target-dir path/to/dbt/target \
  --db path/to/warehouse.duckdb \
  --markdown report.md
```

If all tests pass, it says so and exits. If not, you get a diagnosis per failure.

## How it works
```
dbt build ─► run_results.json (which tests failed, row counts)
manifest.json (compiled SQL, guarded model/column, test type)
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
```
The design choice that keeps it small and robust: **it reads dbt's artifacts rather than
re-running tests.** Everything it needs — compiled SQL, failing-row count, guarded model —
is already in the JSON dbt writes on every build.

## Grounding is the whole point

An LLM asked "why did my dbt test fail?" with no context invents a plausible-sounding
story. `dbt-sentinel` feeds the model only real evidence — the compiled SQL, the column
schema, and a sample of the actual failing rows — and tells it to ground every claim in
that evidence or report low confidence.

The difference on a real failure (a taxi-trip speed test where `avg_speed_mph` was computed
with a `600` multiplier instead of `60`):

**Naive prompt — test name + row count only, no evidence:**

> Most likely: division by zero or NULL producing infinite/NULL speed. With 1.8M failing
> rows this suggests a systemic data issue. Run these diagnostic queries to pinpoint the
> cause… [three SQL queries checking null/zero durations and negative distances].

**Grounded (dbt-sentinel):**

> The `avg_speed_mph` formula uses a multiplier of 600 instead of 60. A trip of 9.24 miles
> in 52 minutes yields (9.24/52)×60 = 10.66 mph (plausible), but the bug computes
> (9.24/52)×600 = 106.62 — which exactly matches the failing row. The same 10× inflation
> holds across all sampled rows. **Confidence: high.**

The naive answer guesses the wrong cause and hands the work back to you as queries to run.
The grounded answer runs them, finds the real bug, and proves it against a row. See
[`docs/example-analysis.md`](https://github.com/qraza/dbt-sentinel/blob/main/docs/example-analysis.md) for the full output.

## Warehouses

Sampling runs behind a small adapter interface, so the engine is a flag, not a rewrite.
DuckDB works out of the box; BigQuery needs the optional extra and Application Default
Credentials.

```bash
# DuckDB (default)
uv run sentinel analyze --target-dir path/to/target --db warehouse.duckdb

# BigQuery
uv sync --group bq
gcloud auth application-default login
uv run sentinel analyze --target-dir path/to/target --bq-project my-project --bq-location EU
```

BigQuery works against the free sandbox — no billing account required. Credentials are
never handled by dbt-sentinel itself; the client reads them from ADC.

```bash
# Snowflake
uv sync --group sf
```

```python
open_warehouse(snowflake={
    "account": "ORG-ACCOUNT", "user": "ME",
    "private_key_path": "~/.snowflake/rsa_key.p8",
    "warehouse": "COMPUTE_WH", "database": "DB", "schema": "PUBLIC",
})
```

Snowflake uses **key-pair authentication**, not passwords. Snowflake is deprecating
single-factor password sign-ins through 2026 — human users need MFA and service users
must use key-pair, OAuth, PAT or WIF — so a password-based adapter would have been
obsolete on arrival. Key-pair is also the right fit for a non-interactive tool: no human
present, no password in the environment.

Snowflake support was built and verified end to end against a trial account (see
[`docs/snowflake-diagnosis.txt`](docs/snowflake-diagnosis.txt)). The adapter is covered by
mocked tests, but is not currently exercised against a live instance, since the trial has
a fixed lifetime. Testing against the real engine caught something mocks would not have:
Snowflake's cursor reports numeric type codes rather than type names, which would have put
`0` and `2` into the grounding prompt instead of `FIXED` and `TEXT`.

## Dashboard

A single-page Streamlit view over the recorded history — latest run, per-test failure
trend, and the stored root cause. It reads dbt-sentinel's own history database, so it
needs no warehouse connection and no API key.

```bash
uv sync --group ui
uv run --group ui streamlit run app/dashboard.py
```

![dashboard](https://raw.githubusercontent.com/qraza/dbt-sentinel/main/docs/dashboard.png)

## Design decisions

**Read artifacts, don't re-run tests.** dbt writes the artifacts on every build; parsing
them needs no dbt invocation and works against any completed run, including CI.

**Sample rows via the test's compiled SQL.** Some models are ephemeral and aren't queryable
as tables. The test's compiled SQL has that logic inlined, so it returns the offending rows
regardless of materialization.

**Ground, then surface confidence.** The prompt contains only evidence; the model is told to
say "insufficient evidence" rather than speculate, and confidence is shown, not hidden.

**Read-only, always.** The warehouse connection inspects; it never mutates.

## Limitations

- DuckDB, BigQuery and Snowflake supported; other engines need a new Warehouse adapter.
- One dbt project per run.
- Models with extended thinking can spend the whole token budget before emitting text,
  so `max_tokens` is set generously; too low a value yields an unparseable (low-confidence)
  result rather than an answer.
- Diagnosis quality depends on the model and on how much signal the sampled rows carry;
  low-signal failures correctly return low confidence.

## Related projects

- [taxi-analytics-pipeline](https://github.com/qraza/taxi-analytics-pipeline) — the dbt
  project these diagnoses run against.
- [sentinel-mcp](https://github.com/qraza/sentinel-mcp) — exposes this library's capabilities as MCP
  tools so an AI assistant can compose its own answers.

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run pytest -v
```

The dbt artifacts in `tests/fixtures/` are deliberately frozen snapshots, so they will
report as stale — that is intentional, and exercises the staleness guard.

CI runs the same lint + tests on every push, on Python 3.11 and 3.12. Tests use committed fixtures and a mocked API —
no warehouse, no API key needed.

---

Built by [Qamar Raza](https://github.com/qraza).


## Does the diagnosis actually work?

`evals/` runs dbt-sentinel against fixtures with deliberately planted defects and known
correct answers. Two fixture kinds:

- **strong signal** — the cause is inferable from the sampled rows (unit-conversion error,
  join fan-out, division by zero, inconsistent casing). A confident, correct answer is expected.
- **weak signal** — the cause is *not* determinable from the data (a stale allow-list, a
  revenue threshold that may or may not be wrong). Here a confident answer is a **failure**.

Without the weak fixtures you can only measure accuracy, and a model that is confident about
everything scores perfectly.

Each fixture runs 3 times, since LLM confidence is not deterministic.

| Metric | Best case | Worst case |
| --- | --- | --- |
| Accuracy (4 strong fixtures) | 1.00 | 0.75 |
| Calibration (2 weak fixtures) | 1.00 | 1.00 |
| **Overconfident errors** | **0 of 18 diagnoses** | |

The asymmetry matters more than the headline number. Every error was *under*-confidence —
one fixture returned "low" on one of three runs when it could have answered. It was never
confident and wrong. For a diagnostic tool that is the right direction to fail in: a missed
answer costs you the manual investigation you would have done anyway; a confident wrong
answer sends you chasing a bug that does not exist.

**Method and its limits.** Matching is keyword-based, so a right-idea-wrong-wording answer
scores as a miss — treat accuracy as a lower bound. Six fixtures is a smoke test, not a
statistic. Evals need an API key and are run manually, not in CI.

```bash
uv run --group evals python evals/run_evals.py     # EVAL_RUNS=5 for more passes
```

**This harness changed the tool.** The first weak fixture came back "high confidence" with a
plausible-sounding cause. The system prompt told the model to flag *insufficient* evidence but
said nothing about *ambiguous* evidence supporting several explanations. Adding that took
calibration from 0.00 to 1.00 with no loss of accuracy.
