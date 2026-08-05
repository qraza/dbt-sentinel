"""Turn a failing test's gathered context into a grounded, root-cause analysis.

This is the part that makes dbt-sentinel more than a wrapper around "ask an LLM
why my test failed". The model is given *only* concrete evidence -- the test
definition, the compiled SQL, the column schema, and a capped sample of the
actual offending rows -- and is instructed to ground every claim in that
evidence and to report low confidence (rather than invent a cause) when the
sample doesn't support a conclusion.

The LLM is asked to return strict JSON so the result is structured and testable:
    {"root_cause": ..., "suggested_fix": ..., "confidence": "high|medium|low",
     "evidence": ...}

The Anthropic call is a thin httpx POST, matching the taxi repo's llm helper, so
it's trivially mockable in tests -- no network in CI.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from .context import FailureContext

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
# Set ANTHROPIC_MODEL to whatever model your key can use. Check the exact string
# your taxi repo's cli/llm.py already uses -- that's the source of truth for your
# setup -- rather than trusting this default.
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
API_VERSION = "2023-06-01"

SYSTEM_PROMPT = (
    "You are a senior analytics engineer reviewing a failed dbt data-quality "
    "test. You are given the test definition, the compiled SQL that ran, the "
    "column schema, and a sample of the rows that FAILED the test. "
    "Diagnose the most likely root cause and propose a concrete fix. "
    "Ground every statement in the evidence provided. If the evidence is "
    "insufficient to be sure, say so plainly and set confidence to 'low' rather "
    "than guessing or inventing tables, columns, or values that are not shown. "
    "Respond with ONLY a JSON object with keys: root_cause (string), "
    "suggested_fix (string), confidence (one of 'high', 'medium', 'low'), "
    "evidence (string: which specific columns/values led you to the conclusion)."
)


@dataclass
class Analysis:
    """A structured, grounded verdict on a single failing test."""

    root_cause: str
    suggested_fix: str
    confidence: str  # "high" | "medium" | "low"
    evidence: str
    raw: str = ""  # the model's raw text, kept for debugging / display

    @property
    def is_confident(self) -> bool:
        return self.confidence.lower() in {"high", "medium"}


def build_prompt(ctx: FailureContext) -> str:
    """Assemble the grounded user prompt from gathered context.

    Deliberately includes only evidence -- no external knowledge, no model name
    guessing. Everything here is something dbt or the warehouse actually
    produced for this specific failure.
    """
    t = ctx.test
    columns = ", ".join(f"{name} ({dtype})" for name, dtype in ctx.columns) or "(unknown)"
    sample = json.dumps(ctx.sample_rows, indent=2, default=str)

    kind = t.test_type or "singular (hand-written) test"
    kwargs = json.dumps(t.test_kwargs) if t.test_kwargs else "(none)"

    return (
        f"A dbt test failed.\n\n"
        f"Test name: {t.test_name}\n"
        f"Test type: {kind}\n"
        f"Test arguments: {kwargs}\n"
        f"Guarded model: {t.model_name}\n"
        f"Guarded column: {t.column_name or '(whole-row / singular test)'}\n"
        f"Failing row count: {t.failure_count}\n"
        f"dbt message: {t.message or '(none)'}\n\n"
        f"Columns in the failing rows: {columns}\n\n"
        f"Sample of the rows that FAILED the test (capped):\n{sample}\n\n"
        f"Compiled SQL that dbt executed for this test:\n{t.compiled_sql}\n\n"
        f"Sampling note: {ctx.note}\n"
    )


def _parse_response(text: str) -> Analysis:
    """Parse the model's JSON reply, tolerating stray prose around the object."""
    raw = text.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data: dict[str, Any] = json.loads(raw[start : end + 1])
            return Analysis(
                root_cause=str(data.get("root_cause", "")).strip(),
                suggested_fix=str(data.get("suggested_fix", "")).strip(),
                confidence=str(data.get("confidence", "low")).strip().lower(),
                evidence=str(data.get("evidence", "")).strip(),
                raw=raw,
            )
        except json.JSONDecodeError:
            pass
    # Model didn't return usable JSON -- fail safe as low confidence, not a crash.
    return Analysis(
        root_cause="Could not parse a structured analysis from the model response.",
        suggested_fix="",
        confidence="low",
        evidence="",
        raw=raw,
    )


def analyze(
    ctx: FailureContext,
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 8192,
    client: httpx.Client | None = None,
) -> Analysis:
    """Send grounded context to the Anthropic API and return a structured verdict.

    Args:
        ctx: the gathered failure context (test + sample offending rows).
        api_key: Anthropic API key; falls back to ANTHROPIC_API_KEY env var.
        model: model string to call.
        client: optional httpx.Client, mainly for injection in tests.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "No Anthropic API key. Set ANTHROPIC_API_KEY or pass api_key=."
        )

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": build_prompt(ctx)}],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }

    owns_client = client is None
    client = client or httpx.Client(timeout=60)
    try:
        resp = client.post(ANTHROPIC_URL, headers=headers, json=payload)
        resp.raise_for_status()
        body = resp.json()
    finally:
        if owns_client:
            client.close()
    # Anthropic returns {"content": [{"type": "text", "text": "..."}], ...}
    text = "".join(
        block.get("text", "")
        for block in body.get("content", [])
        if block.get("type") == "text"
    )
    return _parse_response(text)