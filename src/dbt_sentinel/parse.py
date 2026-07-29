"""Parse dbt artifacts into structured failing-test records.

After a `dbt build`/`dbt test`, dbt writes two files into its ``target/`` directory:

* ``run_results.json`` -- what happened this run: per-node status, timing, and
  (for tests) the number of failing rows.
* ``manifest.json`` -- what the project *is*: every node, its compiled SQL, its
  dependencies, and (for tests) which column/model it guards and what kind of
  test it is.

This module joins the two on ``unique_id`` and returns a list of
:class:`FailingTest` objects -- everything downstream (context gathering, the
LLM analysis) needs, and nothing it doesn't.

It intentionally does *not* connect to a warehouse or run dbt; it only reads the
JSON dbt already produced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# dbt marks a node failed with one of these statuses in run_results.json.
# "warn" is a soft failure (a test set to severity: warn); include it so the
# tool can surface warnings too, but callers can filter on `.status` if they
# only want hard failures.
FAILURE_STATUSES = {"fail", "error", "warn"}


@dataclass
class FailingTest:
    """A single dbt test that did not pass, enriched with manifest context."""

    unique_id: str
    test_name: str
    status: str  # "fail" | "error" | "warn"
    failure_count: int | None  # rows returned by the test; None for errors
    message: str | None  # dbt's own error/failure message, if any

    test_type: str | None  # e.g. "not_null", "accepted_values"; None = singular
    test_kwargs: dict[str, Any] = field(default_factory=dict)

    model_unique_id: str | None = None  # the model this test guards
    model_name: str | None = None
    column_name: str | None = None
    relation: str | None = None  # "schema.identifier" for querying the warehouse

    compiled_sql: str | None = None  # the actual SQL dbt ran for this test

    @property
    def is_generic(self) -> bool:
        """True for schema tests (not_null etc.), False for singular .sql tests."""
        return self.test_type is not None


def _load_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Expected dbt artifact at {p}, but it does not exist.")
    with p.open(encoding="utf-8") as fh:
        return json.load(fh)


def _compiled_sql(node: dict[str, Any]) -> str | None:
    """dbt renamed this key across versions; try both."""
    return node.get("compiled_code") or node.get("compiled_sql")


def _relation(node: dict[str, Any]) -> str | None:
    """Build a "schema.identifier" string from a model node for later querying."""
    schema = node.get("schema")
    identifier = node.get("alias") or node.get("name")
    if schema and identifier:
        return f"{schema}.{identifier}"
    return identifier


def _guarded_model(
    test_node: dict[str, Any], nodes: dict[str, Any]
) -> dict[str, Any] | None:
    """Return the model node a test depends on, if any.

    A generic test depends on exactly one model (plus macros); pick the first
    dependency that resolves to a model/seed/snapshot node in the manifest.
    """
    for dep_id in test_node.get("depends_on", {}).get("nodes", []):
        dep = nodes.get(dep_id)
        if dep and dep.get("resource_type") in {"model", "seed", "snapshot"}:
            return dep
    return None


def parse(target_dir: str | Path) -> list[FailingTest]:
    """Parse a dbt ``target/`` directory into a list of failing tests.

    Args:
        target_dir: path to dbt's ``target`` folder (contains run_results.json
            and manifest.json).

    Returns:
        One :class:`FailingTest` per test whose status is in FAILURE_STATUSES,
        ordered as they appear in run_results.json. Empty list if all passed.
    """
    target = Path(target_dir)
    run_results = _load_json(target / "run_results.json")
    manifest = _load_json(target / "manifest.json")
    nodes: dict[str, Any] = manifest.get("nodes", {})

    failures: list[FailingTest] = []

    for result in run_results.get("results", []):
        status = result.get("status")
        if status not in FAILURE_STATUSES:
            continue

        unique_id = result.get("unique_id", "")
        node = nodes.get(unique_id, {})

        # Only tests are interesting here; a failed *model* build is a different
        # concern. dbt test unique_ids start with "test.".
        if node.get("resource_type") and node["resource_type"] != "test":
            continue

        test_meta = node.get("test_metadata") or {}
        model_node = _guarded_model(node, nodes)

        failures.append(
            FailingTest(
                unique_id=unique_id,
                test_name=node.get("name") or unique_id,
                status=status,
                failure_count=result.get("failures"),
                message=result.get("message"),
                test_type=test_meta.get("name"),
                test_kwargs=test_meta.get("kwargs", {}) or {},
                model_unique_id=model_node.get("unique_id") if model_node else None,
                model_name=model_node.get("name") if model_node else None,
                column_name=node.get("column_name"),
                relation=_relation(model_node) if model_node else None,
                compiled_sql=_compiled_sql(node),
            )
        )

    return failures


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import sys

    target_arg = sys.argv[1] if len(sys.argv) > 1 else "target"
    for ft in parse(target_arg):
        print(f"{ft.status.upper():5}  {ft.test_name}")
        print(f"       guards: {ft.relation}.{ft.column_name}  ({ft.test_type or 'singular'})")
        print(f"       rows:   {ft.failure_count}")
        print()
