"""JSON reducer — preserve schema + top-level counts + small scalar fields.

Deterministic + query-independent. Reduces a single large JSON value to its
shape: keys + value types for an object, length + element-schema for an array.
Small scalar fields are kept verbatim. Unparseable input returns verbatim with
a warning (never raises).
"""

from __future__ import annotations

import json

from ..budget import est_tokens
from ..blocks import ReducerResult

_SCALAR_KEEP_CHARS = 120  # keep short scalars inline; summarize larger values


def _typename(v: object) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return f"list[{len(v)}]"
    if isinstance(v, dict):
        return f"object[{len(v)} keys]"
    if v is None:
        return "null"
    return type(v).__name__


def _schema_lines(obj: dict) -> list[str]:
    out: list[str] = []
    for k, v in obj.items():
        if isinstance(v, (str, int, float, bool)) and len(str(v)) <= _SCALAR_KEEP_CHARS:
            out.append(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            out.append(f"  {k}: <{_typename(v)}>")
    return out


def reduce(text: str) -> ReducerResult:
    original_tokens = est_tokens(text)
    try:
        data = json.loads(text)
    except ValueError:
        return ReducerResult(
            text=text,
            original_tokens=original_tokens,
            packed_tokens=original_tokens,
            warnings=["json: not parseable, returned verbatim"],
        )

    lines: list[str]
    if isinstance(data, dict):
        lines = [f"[json summary: object, {len(data)} top-level keys]"]
        lines += _schema_lines(data)
    elif isinstance(data, list):
        lines = [f"[json summary: array, {len(data)} items]"]
        if data:
            first = data[0]
            if isinstance(first, dict):
                lines.append("element schema (from first item):")
                lines += _schema_lines(first)
            else:
                lines.append(f"element type: {_typename(first)}")
            lines.append("first: " + json.dumps(data[0], ensure_ascii=False)[:400])
            lines.append("last: " + json.dumps(data[-1], ensure_ascii=False)[:400])
    else:
        # Scalar at top level — nothing to reduce.
        return ReducerResult(
            text=text, original_tokens=original_tokens, packed_tokens=original_tokens
        )

    summary = "\n".join(lines)
    return ReducerResult(
        text=summary,
        original_tokens=original_tokens,
        packed_tokens=est_tokens(summary),
        warnings=[],
    )
