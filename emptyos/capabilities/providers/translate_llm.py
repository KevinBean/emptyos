"""LLM-backed translation provider — the universal `translate` fallback.

Translates by asking the ``think`` capability for a strict JSON array. It is the
fallback *under* any local MT provider a plugin adds (the `translate` plugin's
NLLB-200 sits at priority 0), and it works on any deployment that has a ``think``
provider — covering every language the model knows, including ones NLLB lacks.

``is_cloud`` is **False** deliberately: the actual cloud routing + consent
happen *inside* the downstream ``think`` call, so gating here too would
double-prompt the user. Determinism comes from the cache layer
(``emptyos.sdk.i18n.translate_cached``) plus ``temperature=0`` — once a string
is translated it is frozen, so it never re-translates "randomly".
"""

from __future__ import annotations

import json

from emptyos.capabilities import Provider

TRANSLATE_SYSTEM = (
    "You are a precise UI-string translator. You receive a JSON array of short "
    "English interface strings (button labels, headers, menu items, tooltips). "
    "Translate each into {lang}. Rules:\n"
    "- Preserve placeholders verbatim: {name}, %s, {0}, :count, etc.\n"
    "- Keep it concise — these are UI labels, not prose.\n"
    "- Do NOT translate: code, identifiers, file paths, URLs, brand/product "
    "names, or text already in {lang}.\n"
    "- Match the original's capitalization style.\n"
    "Return ONLY a JSON array of strings, same length and order as the input, "
    "no markdown fences, no commentary."
)


class LLMTranslateProvider(Provider):
    """Translate via the think capability. Always available when think is."""

    name = "llm-translate"

    def __init__(self, kernel=None):
        self.kernel = kernel

    @property
    def is_cloud(self) -> bool:
        # The downstream think() call applies its own cloud-consent gate; do
        # not gate again here or the user is prompted twice.
        return False

    async def available(self) -> bool:
        if self.kernel is None:
            return False
        try:
            think = self.kernel.capability("think")
        except Exception:
            return False
        try:
            for p in think.providers:
                if getattr(p, "name", "") != "human" and await p.available():
                    return True
        except Exception:
            return False
        return False

    async def health(self) -> dict:
        ok = await self.available()
        return {
            "available": ok,
            "reason": None if ok else "no think provider available for translation",
            "recovery": None
            if ok
            else {
                "kind": "config",
                "path": "emptyos.toml",
                "section": "[capabilities.think]",
            },
        }

    async def execute(self, *, texts, to: str, source: str = "en", **_) -> list:
        from emptyos.sdk.i18n import lang_name
        from emptyos.sdk.utils import parse_llm_json

        items = list(texts)
        if not items:
            return []

        system = TRANSLATE_SYSTEM.replace("{lang}", lang_name(to))
        prompt = json.dumps(items, ensure_ascii=False)
        result = await self.kernel.capability("think").execute(
            prompt=prompt,
            system=system,
            domain="text",
            task_shape="parse-json",
            temperature=0,
        )
        raw = getattr(result, "value", "") or ""
        parsed = parse_llm_json(raw, fallback=None)
        if not isinstance(parsed, list) or len(parsed) != len(items):
            raise RuntimeError("translation: malformed LLM response")
        return [str(x) for x in parsed]
