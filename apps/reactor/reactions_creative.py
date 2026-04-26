"""Creative reactions — music, podcast, studio, fiction, 3D, ComfyUI."""

from __future__ import annotations

from emptyos.sdk import on_event


class CreativeReactionsMixin:

    @on_event("music:composed")
    async def on_compose(self, event):
        self._log_action("music:composed", "new track")
        await self._notify("New music track generated!")
        await self._journal_ripple("🎵", "Generated a new music track")

    @on_event("music:lyrics_created")
    async def on_lyrics(self, event):
        self._log_action("music:lyrics_created", "new lyrics")
        await self._journal_ripple("✍️", "Wrote new lyrics")

    @on_event("music:mv_generated")
    async def on_mv(self, event):
        self._log_action("music:mv_generated", "new music video")

    @on_event("music:updated")
    async def on_music_updated(self, event):
        self._log_action("music:updated", event.data.get("file", "")[:40])

    @on_event("podcast:generated")
    async def on_podcast(self, event):
        self._log_action("podcast:generated", "new episode")
        await self._journal_ripple("🎙️", "Generated a podcast episode")

    @on_event("studio:generated")
    async def on_studio(self, event):
        self._log_action("studio:generated", "new image")
        await self._journal_ripple("🎨", "Generated an AI image")

    @on_event("fiction:drafted")
    async def on_fiction_drafted(self, event):
        scene = event.data.get("scene", "")
        words = event.data.get("words", 0)
        self._log_action("fiction:drafted", f"scene {scene}: {words}w")

    @on_event("fiction:story_created")
    async def on_fiction_created(self, event):
        self._log_action("fiction:story_created", f"new story: {event.data.get('title', '')}")

    @on_event("comfyui:workflow_completed")
    async def on_comfyui(self, event):
        prompt = event.data.get("prompt", "")[:40]
        self._log_action("comfyui:workflow_completed", f"image: {prompt}")

    @on_event("3d-studio:render_complete")
    async def on_3d_render_done(self, event):
        self._log_action("3d-studio:render_complete", "3D render complete")

    @on_event("3d-studio:render_failed")
    async def on_3d_render_fail(self, event):
        self._log_action("3d-studio:render_failed", "3D render failed")

    @on_event("tts:generated")
    async def on_tts_generated(self, event):
        self._log_action("tts:generated", event.data.get("text", "")[:40])

    @on_event("canvas:board_saved")
    async def on_canvas_saved(self, event):
        board_id = event.data.get("board_id", "")
        nodes = event.data.get("nodes", 0)
        edges = event.data.get("edges", 0)
        self._log_action("canvas:board_saved", f"{board_id}: {nodes}n/{edges}e")
        await self._journal_ripple("🎨", f"Canvas: updated `{board_id}` ({nodes} cards)")

    @on_event("canvas:board_deleted")
    async def on_canvas_deleted(self, event):
        board_id = event.data.get("board_id", "")
        self._log_action("canvas:board_deleted", board_id)
        await self._journal_ripple("🎨", f"Canvas: deleted `{board_id}`")
