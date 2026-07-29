"""Render analyzed failures as a terminal report and/or a markdown file.

Takes the list of (FailingTest, Analysis) pairs the CLI produces and presents
them two ways: a Rich-formatted terminal summary for interactive use, and a
plain-markdown document suitable for a PR comment or a committed artifact.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .analyze import Analysis
from .parse import FailingTest

# Confidence -> (rich colour, emoji-free label). Kept boring on purpose.
_CONF_STYLE = {"high": "green", "medium": "yellow", "low": "red"}


@dataclass
class AnalyzedFailure:
    test: FailingTest
    analysis: Analysis


def _conf_style(confidence: str) -> str:
    return _CONF_STYLE.get(confidence.lower(), "white")


def render_terminal(results: list[AnalyzedFailure], console: Console | None = None) -> None:
    """Print a Rich summary table plus a detail panel per failure."""
    console = console or Console()

    if not results:
        console.print("[bold green]All tests passed — nothing to analyze.[/]")
        return

    table = Table(title=f"{len(results)} failing test(s)")
    table.add_column("Test", overflow="fold")
    table.add_column("Guards", overflow="fold")
    table.add_column("Rows", justify="right")
    table.add_column("Confidence")
    for r in results:
        guards = f"{r.test.relation or r.test.model_name or '?'}"
        if r.test.column_name:
            guards += f".{r.test.column_name}"
        conf = r.analysis.confidence
        table.add_row(
            r.test.test_name,
            guards,
            str(r.test.failure_count if r.test.failure_count is not None else "—"),
            f"[{_conf_style(conf)}]{conf}[/]",
        )
    console.print(table)

    for r in results:
        conf = r.analysis.confidence
        body = (
            f"[bold]Root cause[/]\n{r.analysis.root_cause}\n\n"
            f"[bold]Suggested fix[/]\n{r.analysis.suggested_fix}\n\n"
            f"[bold]Evidence[/]\n{r.analysis.evidence}"
        )
        console.print(
            Panel(
                body,
                title=r.test.test_name,
                subtitle=f"confidence: {conf}",
                border_style=_conf_style(conf),
            )
        )


def build_markdown(results: list[AnalyzedFailure]) -> str:
    """Return a markdown report — good for PR comments or a committed file."""
    if not results:
        return "# dbt-sentinel\n\nAll tests passed — nothing to analyze.\n"

    lines = ["# dbt-sentinel report", "", f"**{len(results)} failing test(s)**", ""]
    for r in results:
        guards = r.test.relation or r.test.model_name or "?"
        if r.test.column_name:
            guards += f".{r.test.column_name}"
        lines += [
            f"## {r.test.test_name}",
            "",
            f"- **Guards:** `{guards}`",
            f"- **Failing rows:** {r.test.failure_count}",
            f"- **Confidence:** {r.analysis.confidence}",
            "",
            "**Root cause**",
            "",
            r.analysis.root_cause,
            "",
            "**Suggested fix**",
            "",
            r.analysis.suggested_fix,
            "",
            "**Evidence**",
            "",
            r.analysis.evidence,
            "",
        ]
    return "\n".join(lines)