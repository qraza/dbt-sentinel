"""dbt-sentinel — AI-grounded root-cause analysis for failing dbt tests.

Typical library use:

    from dbt_sentinel import parse, open_warehouse, gather_context, analyze

    for test in parse("target"):
        ctx = gather_context(test, open_warehouse(duckdb_path="wh.duckdb"))
        print(analyze(ctx).root_cause)
"""

from .analyze import Analysis, analyze, build_prompt
from .context import FailureContext, gather_context
from .parse import FailingTest, parse
from .report import AnalyzedFailure, build_markdown, render_terminal
from .warehouse import (
    BigQueryWarehouse,
    DuckDBWarehouse,
    QueryResult,
    Warehouse,
    open_warehouse,
)

__version__ = "0.7.2"

__all__ = [
    "Analysis",
    "AnalyzedFailure",
    "BigQueryWarehouse",
    "DuckDBWarehouse",
    "FailingTest",
    "FailureContext",
    "QueryResult",
    "Warehouse",
    "__version__",
    "analyze",
    "build_markdown",
    "build_prompt",
    "gather_context",
    "open_warehouse",
    "parse",
    "render_terminal",
]
