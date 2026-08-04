"""dbt-sentinel command line interface.

    sentinel analyze --target-dir <dbt target> --db <duckdb file> [--markdown out.md]

Wires the pipeline together: parse the artifacts, gather grounding context for
each failing test from the warehouse, ask the LLM for a grounded diagnosis, and
render the results to the terminal (and optionally a markdown file).
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from .analyze import analyze
from .context import connect, gather_context
from .parse import parse
from .report import AnalyzedFailure, build_markdown, render_terminal
from dotenv import load_dotenv
load_dotenv()

console = Console()


@click.group()
@click.version_option()
def main() -> None:
    """AI-grounded data-quality companion for dbt."""


@main.command()
@click.option(
    "--target-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="dbt target/ directory containing run_results.json and manifest.json.",
)
@click.option(
    "--db",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to the DuckDB warehouse file to sample offending rows from.",
)
@click.option(
    "--markdown",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Also write a markdown report to this path.",
)
@click.option(
    "--sample-limit",
    type=int,
    default=20,
    show_default=True,
    help="Max offending rows to sample per failing test.",
)
@click.option(
    "--model",
    default=None,
    help="Anthropic model string (defaults to $ANTHROPIC_MODEL).",
)
def analyze_cmd(
    target_dir: Path,
    db: Path,
    markdown: Path | None,
    sample_limit: int,
    model: str | None,
) -> None:
    """Analyze failing dbt tests and explain the likely root cause."""
    failures = parse(target_dir)
    if not failures:
        console.print("[bold green]All tests passed — nothing to analyze.[/]")
        return

    console.print(f"Analyzing [bold]{len(failures)}[/] failing test(s)...\n")

    con = connect(db)
    results: list[AnalyzedFailure] = []
    with console.status("Gathering context and asking the model..."):
        for test in failures:
            ctx = gather_context(test, con, sample_limit=sample_limit)
            try:
                analysis = analyze(ctx, model=model or _default_model())
            except RuntimeError as exc:
                console.print(f"[red]{exc}[/]")
                raise SystemExit(1) from exc
            results.append(AnalyzedFailure(test=test, analysis=analysis))

    render_terminal(results, console=console)

    if markdown:
        markdown.write_text(build_markdown(results), encoding="utf-8")
        console.print(f"\n[dim]Markdown report written to {markdown}[/]")


def _default_model() -> str:
    import os

    return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")


# Register under the name "analyze" (the function is analyze_cmd to avoid
# shadowing the imported analyze()).
main.add_command(analyze_cmd, name="analyze")


if __name__ == "__main__":  # pragma: no cover
    main()