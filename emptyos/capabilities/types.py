"""Typed capability definitions — one per human verb."""

from emptyos.capabilities import Capability


class ThinkCapability(Capability):
    """Generate text responses. Human thinks, or LLM generates."""

    name = "think"

    async def execute(self, *, prompt: str, system: str = "", **kwargs):
        return await super().execute(prompt=prompt, system=system, **kwargs)


class ReadCapability(Capability):
    """Read content from a path. Human reads and pastes, or tools read directly."""

    name = "read"

    async def execute(self, *, path: str, **kwargs):
        return await super().execute(path=path, **kwargs)


class WriteCapability(Capability):
    """Write content to a path. Human opens editor, or tools write directly."""

    name = "write"

    async def execute(self, *, path: str, content: str, **kwargs):
        return await super().execute(path=path, content=content, **kwargs)


class SearchCapability(Capability):
    """Search for content. Human remembers/browses, or tools grep/index."""

    name = "search"

    async def execute(self, *, query: str, path: str = "", **kwargs):
        return await super().execute(query=query, path=path, **kwargs)


class SpeakCapability(Capability):
    """Generate speech from text. Human reads aloud, or TTS generates."""

    name = "speak"

    async def execute(self, *, text: str, domain: str | None = None, **kwargs):
        return await super().execute(text=text, domain=domain, **kwargs)


class ListenCapability(Capability):
    """Transcribe audio to text. Human types what they hear, or STT transcribes."""

    name = "listen"

    async def execute(self, *, audio, domain: str | None = None, **kwargs):
        return await super().execute(audio=audio, domain=domain, **kwargs)


class DrawCapability(Capability):
    """Generate images from text. Human draws/finds, or AI generates."""

    name = "draw"

    async def execute(self, *, prompt: str, domain: str | None = None, **kwargs):
        return await super().execute(prompt=prompt, domain=domain, **kwargs)


class AnimateCapability(Capability):
    """Generate a video clip from a prompt + optional reference image.

    Local providers (e.g. LTX-2 via ComfyUI) and cloud providers
    (Runway/Luma/Kling) both implement this; cloud providers are gated by
    the consent manager. Returns a local file path to the rendered clip.
    """

    name = "animate"

    async def execute(self, *, prompt: str, image: str = "", num_frames: int = 24, **kwargs):
        return await super().execute(prompt=prompt, image=image, num_frames=num_frames, **kwargs)


class ModelCapability(Capability):
    """Generate an articulated 3D model from a prompt.

    The verb is "produce a parametric 3D object with semantic parts + joints" —
    a sibling of `draw` and `animate` but in the CAD-shape domain. Local
    providers run an LLM agent loop that writes Python against the
    `engines.articulated` SDK, compiles via CadQuery (the `cadquery` plugin
    service), and returns a path to a record directory containing
    `model.py + output.urdf + meshes/`. Cloud providers (Articraft API,
    MeshyAI, etc.) plug in here later with no app changes.

    Domains: `articulated` (default — has joints) and `static_mesh` (single
    rigid body). Single-file HTML artifacts (Three.js scenes, mermaid
    diagrams, slide decks) belong to the sibling `artifact` capability,
    not this one — see `ArtifactCapability` below.

    Returns a string path to the record directory. The caller can then read
    `output.urdf` for the 3D structure, the `*.md` frontmatter for metadata.
    """

    name = "model"

    async def execute(self, *, prompt: str, domain: str = "articulated", **kwargs):
        return await super().execute(prompt=prompt, domain=domain, **kwargs)


class ArtifactCapability(Capability):
    """Generate a single-file HTML artifact from a prompt.

    Sibling of `model` but for visual / explanatory output rather than
    parametric CAD. The provider writes a complete standalone HTML file
    (Three.js scene, Mermaid diagram, SVG schematic, KaTeX-rendered
    derivation, reveal.js slide deck, etc.) and returns a vault-relative
    path. Correctness is judged by the human eye — no compile gate.

    Shapes: `3d-scene`, `svg-diagram`, `schematic`, `network-graph`, `chart`,
    `anim-explainer`, `slide-deck`, `math-explainer`, `mermaid`. Each shape
    is a system-prompt preset; the local provider (apps/viz) owns the
    preset registry.

    Returns a vault-relative path to the rendered `scene.html` (or empty
    string if no provider is reachable). The caller is expected to embed
    it in an iframe or hand the path to a publish pipeline.
    """

    name = "artifact"

    async def execute(self, *, prompt: str, shape: str = "3d-scene", **kwargs):
        return await super().execute(prompt=prompt, shape=shape, **kwargs)


class SeeCapability(Capability):
    """Capture an image from a camera. Human uploads a file, or a webcam grabs a frame."""

    name = "see"

    async def execute(self, *, mode: str = "snapshot", domain: str | None = None, **kwargs):
        return await super().execute(mode=mode, domain=domain, **kwargs)


class PronounceCapability(Capability):
    """Score a learner's pronunciation against a reference text.

    Returns a structured dict: per-phone alignment (match/sub/del),
    word-level scores, weak-phone roll-up, model + device metadata.
    Different verb from `listen` — listen returns text, pronounce
    returns scored phones. Providers are local-first by design.
    """

    name = "pronounce"

    async def execute(self, *, audio, reference_text: str, language: str = "en-us", **kwargs):
        return await super().execute(
            audio=audio, reference_text=reference_text, language=language, **kwargs
        )


class SendCapability(Capability):
    """Deliver an outbound message to an external recipient.

    The verb is "send a message to someone" — email is the first channel,
    SMS / Resend / SES become additional providers with no app changes.
    Distinct from the `notifications` plugin, which only pushes to the single
    owner (their Telegram chat + vault inbox) and can't address an arbitrary
    recipient. Because a `send` always leaves the machine for a third party,
    every provider is outbound: cloud-classified providers pass through the
    consent gate + outbound leak-scan automatically.

    Providers return a dict: {"ok": bool, "provider": str, "detail": str}.
    No provider wired → raises; apps should catch and degrade (e.g. show an
    on-screen confirmation instead of emailing).
    """

    name = "send"

    async def execute(
        self, *, to: str, subject: str = "", body: str = "", **kwargs
    ):
        return await super().execute(to=to, subject=subject, body=body, **kwargs)


class BrowseCapability(Capability):
    """Drive a browser provider. `action` is the verb; provider interprets kwargs.

    Verbs (provider contract):
        navigate(url, wait=...)            → {"url": str, "title": str}
        click(selector, [context_id])      → {"ok": True}
        fill(selector, value, [context_id])→ {"ok": True}
        screenshot([selector], [full_page])→ {"path": str}
        snapshot([selector])               → {"text": str, "html": str}
        eval(expression, [context_id])     → {"value": <jsonable>}
        wait_for(selector, [state], [timeout]) → {"ok": True}
        close([context_id])                → {"ok": True}

    `context_id` is opaque — providers use it to keep a persistent browser
    context (cookies, page) across calls. Apps pass the same id for stateful
    sessions; omit for one-shot.
    """

    name = "browse"

    async def execute(self, *, action: str, **kwargs):
        return await super().execute(action=action, **kwargs)


class FootageCapability(Capability):
    """Fetch a royalty-free stock video clip matching a search term.

    The verb is "find me a clip of X" — the one primitive the music-video /
    slideshow pipelines need that no other capability provides (it's the piece
    MoneyPrinterTurbo had — `material.py` — and EmptyOS lacked). Cloud
    providers (Pexels, Pixabay) search their library, download the best match,
    and return a local path; because their host is `api.pexels.com` etc. they
    are cloud-classified and pass the consent gate automatically (Dev Rule 18).
    The human fallback lets the user hand over their own clip in interactive
    mode; in daemon mode with no provider the chain raises and the calling app
    catches it and skips the visual (audio-only / cover-only).

    Returns a dict::

        {"path": str, "provider": str, "url": str,
         "duration": float, "width": int, "height": int}

    or ``{"path": ""}`` when nothing matched. Apps read ``["path"]`` and
    tolerate ``""``. ``orientation`` is ``landscape`` | ``portrait`` |
    ``square``; ``min_duration`` filters out clips shorter than N seconds.
    """

    name = "footage"

    async def execute(
        self, *, query: str, orientation: str = "landscape",
        min_duration: float = 0.0, **kwargs,
    ):
        return await super().execute(
            query=query, orientation=orientation, min_duration=min_duration, **kwargs
        )


class TranslateCapability(Capability):
    """Translate strings from a source language into a target language.

    English is the single authored language in EmptyOS; every other language is
    *derived* via this capability. Providers translate a **batch** of strings at
    once (NLLB-200 batches natively; the LLM provider sends one JSON array), so
    `texts` is a list and the return is a same-length, same-order list. Local
    providers (the `translate` plugin's NLLB-200) are deterministic; the LLM
    fallback (`llm-translate`) covers any language the local model lacks. No
    human fallback — a "translate this UI" call has no sensible interactive
    answer, so an un-provided chain raises and the caller degrades to English
    (see `emptyos.sdk.i18n.translate_cached`, which never raises).

    Returns ``list[str]`` aligned with ``texts``.
    """

    name = "translate"

    async def execute(self, *, texts: list[str], to: str, source: str = "en", **kwargs):
        return await super().execute(texts=texts, to=to, source=source, **kwargs)
