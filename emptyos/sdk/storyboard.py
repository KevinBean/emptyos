"""Storyboard — a frame-by-frame editable plan for staged media generation.

The persisted, inspectable intermediate between "plan the scenes" and "render
the video": each frame carries its description, the prompt that generates its
still, its target duration, and — as a pipeline fills them in — the rendered
still + clip paths and a per-frame status. It is the artifact a ``stop_after``
preview shows the user before committing to the expensive render, and the
inter-stage carrier persisted as a run's ``storyboard.json``.

Borrowed in *shape* from AIDC-AI Pixelle-Video's ``models/storyboard.py`` (see
``30_Resources/Web-Clips/2026-06-08 AIDC-AI - Pixelle-Video…``); the
orchestration around it is EmptyOS's own ``emptyos.sdk.pipeline`` — Pixelle's
template-method engine is deliberately *not* borrowed. Pure dataclasses: no
kernel, no IO, JSON round-trippable.

First consumer: ``apps/personal/music-studio`` MV render. The shape generalises
to podcast visuals (the obvious second consumer). Per CLAUDE.md rule 9 it stays
a plain SDK model until that second consumer arrives.

A frame's ``scene`` dict (the app's own per-scene payload — scene number, mood,
the ``generate_image`` result, etc.) rides in :attr:`StoryboardFrame.scene`, so a
consumer can reconstruct the loose ``(results, stills, durations)`` triple its
existing render helpers already expect without changing those signatures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Frame lifecycle: planned → rendered (still ready) → clipped (segment ready);
# failed at any point. Strings (not an enum) so they JSON-round-trip trivially.
PLANNED = "planned"
RENDERED = "rendered"
CLIPPED = "clipped"
FAILED = "failed"


@dataclass
class StoryboardFrame:
    """One shot in the storyboard."""

    index: int
    description: str = ""
    image_prompt: str = ""
    duration: float = 0.0
    still_path: str = ""
    clip_path: str = ""
    status: str = PLANNED
    # The consumer's own per-scene payload (scene number, mood, no_character,
    # the generate_image result …). Opaque to the storyboard; round-tripped as-is.
    scene: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "description": self.description,
            "image_prompt": self.image_prompt,
            "duration": self.duration,
            "still_path": self.still_path,
            "clip_path": self.clip_path,
            "status": self.status,
            "scene": dict(self.scene or {}),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StoryboardFrame:
        d = d or {}
        return cls(
            index=int(d.get("index", 0)),
            description=str(d.get("description", "") or ""),
            image_prompt=str(d.get("image_prompt", "") or ""),
            duration=float(d.get("duration", 0.0) or 0.0),
            still_path=str(d.get("still_path", "") or ""),
            clip_path=str(d.get("clip_path", "") or ""),
            status=str(d.get("status", PLANNED) or PLANNED),
            scene=dict(d.get("scene") or {}),
        )


@dataclass
class Storyboard:
    """A complete frame-by-frame plan plus its render-level config."""

    song: str = ""
    mode: str = ""
    style: str = ""
    audio_path: str = ""
    srt_path: str = ""
    audio_duration: float = 0.0
    frames: list[StoryboardFrame] = field(default_factory=list)

    # --- derived ---

    @property
    def progress(self) -> float:
        """Fraction of frames whose clip is rendered (0.0–1.0)."""
        if not self.frames:
            return 0.0
        clipped = sum(1 for f in self.frames if f.status == CLIPPED)
        return clipped / len(self.frames)

    @property
    def is_complete(self) -> bool:
        return bool(self.frames) and all(f.status == CLIPPED for f in self.frames)

    # --- serialisation ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "song": self.song,
            "mode": self.mode,
            "style": self.style,
            "audio_path": self.audio_path,
            "srt_path": self.srt_path,
            "audio_duration": self.audio_duration,
            "frames": [f.to_dict() for f in self.frames],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Storyboard:
        d = d or {}
        return cls(
            song=str(d.get("song", "") or ""),
            mode=str(d.get("mode", "") or ""),
            style=str(d.get("style", "") or ""),
            audio_path=str(d.get("audio_path", "") or ""),
            srt_path=str(d.get("srt_path", "") or ""),
            audio_duration=float(d.get("audio_duration", 0.0) or 0.0),
            frames=[StoryboardFrame.from_dict(f) for f in (d.get("frames") or [])],
        )

    # --- consumer convenience ---

    @classmethod
    def from_scenes(
        cls,
        scenes: list[dict[str, Any]],
        durations: list[float],
        *,
        song: str = "",
        mode: str = "",
        style: str = "",
        audio_path: str = "",
        srt_path: str = "",
        audio_duration: float = 0.0,
    ) -> Storyboard:
        """Build a planned storyboard from a list of scene dicts + their durations.

        Each scene dict is stored verbatim on its frame's ``scene`` so the
        consumer's existing ``(results, stills, durations)`` helpers can be fed
        back the exact shape they expect (see :meth:`as_render_triple`)."""
        frames: list[StoryboardFrame] = []
        for i, scene in enumerate(scenes):
            scene = scene or {}
            frames.append(
                StoryboardFrame(
                    index=i,
                    description=str(scene.get("description", "") or ""),
                    image_prompt=str(scene.get("prompt", "") or ""),
                    duration=float(durations[i]) if i < len(durations) else 0.0,
                    scene=dict(scene),
                )
            )
        return cls(
            song=song, mode=mode, style=style, audio_path=audio_path,
            srt_path=srt_path, audio_duration=audio_duration, frames=frames,
        )

    def as_render_triple(self) -> tuple[list[dict[str, Any]], list[str], list[float]]:
        """Return ``(scenes, still_paths, durations)`` — the loose triple legacy
        render helpers consume. ``still_paths`` are strings (possibly empty);
        the caller wraps in ``Path`` as needed."""
        scenes = [dict(f.scene) for f in self.frames]
        stills = [f.still_path for f in self.frames]
        durations = [f.duration for f in self.frames]
        return scenes, stills, durations
