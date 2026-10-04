"""Translate plugin — local deterministic MT via Meta NLLB-200 (CTranslate2).

English is the single source language; NLLB-200 fans it out to ~200 target
languages with **deterministic** output (same input → same output via greedy
decoding), the property a raw LLM lacks. Registered at priority 0 so it's
preferred over the always-available LLM translate fallback — but only when a
converted model is present, so the plugin is **dark until installed**:

    pip install ctranslate2 sentencepiece transformers
    ct2-transformers-converter --model facebook/nllb-200-distilled-600M \
        --output_dir %LOCALAPPDATA%/eos/models/nllb-200-distilled-600M-ct2 \
        --copy_files tokenizer.json sentencepiece.bpe.model special_tokens_map.json

    # emptyos.toml
    [plugins.translate]
    model_dir = "C:/Users/<you>/AppData/Local/eos/models/nllb-200-distilled-600M-ct2"
    device = "cpu"   # or "cuda" if you converted for GPU

The 600M-distilled model is the sweet spot (one ~1GB model, all 200 languages).
Heavy work runs in ``asyncio.to_thread`` — ctranslate2 is synchronous and would
otherwise wedge the event loop (see operator memory:
``middleware_sync_blocks_loop``).
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from pathlib import Path

from emptyos.capabilities import Provider
from emptyos.sdk import BasePlugin
from emptyos.sdk.i18n import nllb_code


class TranslatePlugin(BasePlugin):
    name = "translate"

    async def connect(self):
        section = self.kernel.config.get_section("plugins.translate") or {}
        provider = NLLBTranslateProvider(
            model_dir=section.get("model_dir", ""),
            device=section.get("device", "cpu"),
        )
        translate = self.kernel.capabilities.get("translate")
        translate.add_provider(provider, priority=0)


class NLLBTranslateProvider(Provider):
    """Meta NLLB-200 via CTranslate2 — local, offline, deterministic."""

    name = "nllb-200"

    def __init__(self, model_dir: str = "", device: str = "cpu"):
        self.model_dir = model_dir
        self.device = device or "cpu"
        self._translator = None
        self._tokenizer = None
        # Guards model load against a concurrent stampede: many to_thread
        # execute() calls arriving at once must not each load the 622MB model
        # (that would thrash the GIL during the Python-heavy tokenizer init and
        # wedge the event loop). Double-checked below.
        self._load_lock = threading.Lock()
        # Set when ctranslate2/transformers are installed but raise ImportError
        # (a wheel built for another platform, a missing extension module).
        # No retry fixes that without a reinstall, so the provider reports
        # itself unavailable until restart. An OSError during the import, or
        # any failure while building the model (a GPU out of memory, an
        # incomplete model_dir), is NOT recorded and is retried next call.
        self._load_error: str | None = None

    @property
    def is_cloud(self) -> bool:
        return False

    def _deps_ok(self) -> bool:
        # find_spec, never a real import: `import ctranslate2` pulls in torch
        # (about 2 s and several hundred MB resident in one measurement on the
        # dev box, 2026-09-24), and the health watchdog calls available() on
        # every provider once a minute, so importing here loaded torch into
        # every daemon that was never asked to translate. The real import
        # happens in _load(); a package that is present but broken is caught
        # there (_load_error).
        return all(
            importlib.util.find_spec(mod) is not None
            for mod in ("ctranslate2", "transformers")
        )

    def _model_ok(self) -> bool:
        return bool(self.model_dir) and Path(self.model_dir).is_dir()

    async def available(self) -> bool:
        return self._load_error is None and self._deps_ok() and self._model_ok()

    async def health(self) -> dict:
        if self._load_error is not None:
            return {
                "available": False,
                "reason": f"NLLB failed to load: {self._load_error}",
                "recovery": {
                    "kind": "service",
                    "id": "translate",
                    "url": "",
                    "hint": "reinstall ctranslate2 sentencepiece transformers, then restart",
                },
            }
        if not self._deps_ok():
            return {
                "available": False,
                "reason": "ctranslate2 / transformers not installed",
                "recovery": {
                    "kind": "service",
                    "id": "translate",
                    "url": "",
                    "hint": "pip install ctranslate2 sentencepiece transformers",
                },
            }
        if not self._model_ok():
            return {
                "available": False,
                "reason": "NLLB model_dir not configured or missing",
                "recovery": {
                    "kind": "config",
                    "path": "emptyos.toml",
                    "section": "[plugins.translate]",
                },
            }
        return {"available": True, "reason": None, "recovery": None}

    def _load(self):
        if self._translator is not None:
            return
        with self._load_lock:
            if self._translator is not None:  # another thread loaded it
                return
            if self._load_error is not None:
                # Calls already past available() must not each repeat a ~2 s
                # import that is known to fail.
                raise RuntimeError(f"nllb-200 unavailable: {self._load_error}")
            try:
                import ctranslate2
                import transformers
            except ImportError as e:
                # Only ImportError sticks. An OSError here can be transient —
                # on Windows torch's DLL load raises WinError 1455 when system
                # commit is exhausted — so it propagates and the next call
                # retries.
                self._load_error = f"{type(e).__name__}: {e}"
                raise
            self._build(ctranslate2, transformers)

    def _build(self, ctranslate2, transformers):
        """Build the tokenizer + translator. The caller holds ``_load_lock``."""
        import logging
        import warnings as _warnings

        # All sources are English in the EmptyOS "English is the source" model.
        # Silence the benign transformers `fix_mistral_regex` warning — it
        # targets a different tokenizer family; NLLB tokenizes correctly
        # (verified). Scoped to this one load (logger level + warnings both
        # restored) so other transformers diagnostics are unaffected. The
        # warning is emitted via the transformers logger, not warnings.warn,
        # so the logger bump is the load-bearing part; the filter is a belt.
        _tlog = logging.getLogger("transformers")
        _prev_level = _tlog.level
        _tlog.setLevel(logging.ERROR)
        try:
            with _warnings.catch_warnings():
                _warnings.filterwarnings("ignore", message=".*fix_mistral_regex.*")
                tokenizer = transformers.AutoTokenizer.from_pretrained(
                    self.model_dir, src_lang="eng_Latn"
                )
        finally:
            _tlog.setLevel(_prev_level)
        translator = ctranslate2.Translator(self.model_dir, device=self.device)
        # Publish both only after fully built, so the unlocked fast-path
        # check (`self._translator is not None`) is never true mid-load.
        self._tokenizer = tokenizer
        self._translator = translator

    def _translate_sync(self, texts: list[str], tgt_code: str) -> list[str]:
        self._load()
        tok = self._tokenizer
        sources = [tok.convert_ids_to_tokens(tok.encode(t)) for t in texts]
        results = self._translator.translate_batch(
            sources,
            target_prefix=[[tgt_code]] * len(sources),
            beam_size=1,  # greedy → deterministic
        )
        out: list[str] = []
        for r in results:
            hyp = r.hypotheses[0] if r.hypotheses else []
            if hyp and hyp[0] == tgt_code:
                hyp = hyp[1:]  # drop the target-language prefix token
            ids = tok.convert_tokens_to_ids(hyp)
            out.append(tok.decode(ids, skip_special_tokens=True))
        return out

    async def execute(self, *, texts, to: str, source: str = "en", **_) -> list:
        tgt = nllb_code(to)
        if not tgt:
            raise RuntimeError(f"nllb-200: unsupported target language '{to}'")
        items = list(texts)
        if not items:
            return []
        return await asyncio.to_thread(self._translate_sync, items, tgt)
