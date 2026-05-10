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


class SeeCapability(Capability):
    """Capture an image from a camera. Human uploads a file, or a webcam grabs a frame."""

    name = "see"

    async def execute(self, *, mode: str = "snapshot", domain: str | None = None, **kwargs):
        return await super().execute(mode=mode, domain=domain, **kwargs)
