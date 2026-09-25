"""MarkItDown runner — executes INSIDE the markitdown user-home venv.

Reads an input document path + an output path from argv, converts the document
to markdown via ``MarkItDown.convert_local()``, writes the markdown (utf-8) to
the output path, and prints a one-line JSON status to stdout. The markdown
travels via a file (not stdout) so Windows cp1252 stdout can't mangle non-ASCII
content.

100% local: ``MarkItDown(enable_plugins=False)`` — no ``docintel_endpoint``, no
Azure Content Understanding, no cloud ``llm_client``. See
``plugins/markitdown/plugin.py`` for why conversion runs in a separate
interpreter (onnxruntime version conflict with the daemon env).
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "usage: runner.py <input> <output>"}))
        return 2
    in_path, out_path = sys.argv[1], sys.argv[2]
    try:
        from markitdown import MarkItDown

        # enable_plugins=False keeps this hermetic; no docintel_endpoint and no
        # llm_client means zero cloud egress (CLAUDE.md rules 18/19).
        md = MarkItDown(enable_plugins=False)
        result = md.convert_local(in_path)
        text = getattr(result, "text_content", None)
        if text is None:
            text = getattr(result, "markdown", "") or ""
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(
            json.dumps(
                {"ok": True, "chars": len(text), "title": getattr(result, "title", "") or ""}
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 — surface any failure as JSON for the parent
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
