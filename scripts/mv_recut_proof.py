"""Build a bounded offline proof for Music Studio's montage EDL.

This is a diagnostic/acceptance tool, not a second MV production pipeline.
It consumes already-rendered delivery clips, ignores their inherited source
timestamps, reconstructs every EDL range at native 24 fps, and writes outside
the persisted Music Studio run.  Normal production remains owned by the Music
Studio state machine.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "apps" / "personal" / "music-studio"
AUDIO_SUFFIXES = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg")


def _load_app_module(name: str):
    """Import one music-studio module by file path.

    The app is gitignored and not importable as a package from a standalone
    script, so every module this tool borrows comes through here.

    Borrowing rather than reimplementing is the point. This tool used to carry
    its own trim/concat filtergraph beside the app's; the two diverged within
    hours of both existing — the delivery path letterboxes a shot that does not
    fill the frame while this one stretched it, and the Windows 32767-character
    argv ceiling was fixed on the delivery side only, leaving the proof unable
    to render past ~110 shots. A proof that renders differently from delivery
    is not a proof.
    """
    spec = importlib.util.spec_from_file_location(
        f"music_studio_recut_{name}", APP_DIR / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _probe_decoded_frames(path: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stream = json.loads(result.stdout)["streams"][0]
    frames = int(stream["nb_read_frames"])
    if frames <= 0:
        raise ValueError(f"{path.name} contains no decoded video frames")
    return frames


def _find_audio(song_dir: Path) -> Path:
    candidates = sorted(
        (
            path
            for path in song_dir.iterdir()
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
        ),
        key=lambda path: path.stat().st_size,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"no source audio found in {song_dir}")
    return candidates[0]


def _find_sources(clip_dir: Path) -> list[Path]:
    """Prefer canonical clip names, while accepting normalized shot exports."""
    for pattern in ("clip-*.mp4", "shot-*.mp4"):
        paths = sorted(clip_dir.glob(pattern))
        if paths:
            return paths
    raise FileNotFoundError(
        f"no clip-*.mp4 or shot-*.mp4 files found in {clip_dir}",
    )


def _parse_srt_onsets(path: Path | None) -> list[float]:
    if path is None or not path.is_file():
        return []
    return _load_app_module("editing").srt_onsets(
        path.read_text(encoding="utf-8-sig"),
    )


def _timing_inputs(
    analysis: dict,
    source_frames: list[dict],
    *,
    fps: int,
    run_state_path: Path | None,
) -> tuple[list[float], list[dict]]:
    """Use run-authored scene order when a matching state file is supplied."""
    if run_state_path is None:
        return (
            list(analysis.get("scene_cuts") or []),
            list(analysis.get("sections") or []),
        )

    scenes = _scene_meta(run_state_path)
    if len(scenes) != len(source_frames):
        raise ValueError(
            f"run state has {len(scenes)} scenes but source set has "
            f"{len(source_frames)} clips",
        )

    _duration, cuts, sections = _load_app_module("editing").source_aligned_timing(
        scenes,
        source_frames,
        fps=fps,
    )
    return cuts, sections


def _scene_meta(run_state_path: Path | None) -> list[dict]:
    """Per-scene motif annotation from a plan file, when one is supplied.

    Accepts a run.json or a plan/draft, so an annotated plan can carry
    ``visual_motif`` / ``motif_state`` / ``edit_motif_allowed`` straight into
    the proof without a separate flag per scene.
    """
    if run_state_path is None:
        return []
    data = json.loads(run_state_path.read_text(encoding="utf-8"))
    scenes = data.get("scenes") or (data.get("inputs") or {}).get("scenes") or []
    return list(scenes)


def build_proof(
    song_dir: Path,
    clip_dir: Path,
    output: Path,
    *,
    subtitle_path: Path | None = None,
    run_state_path: Path | None = None,
    source_quality_path: Path | None = None,
    motif_sources: set[int] | None = None,
    bars_per_shot: dict[str, int] | None = None,
    reframe_sources: set[int] | None = None,
    min_shot_seconds: float = 2.0,
    lyric_stride: dict[str, int] | None = None,
    pace: str = "",
    chorus_half_phrase: bool = False,
) -> tuple[Path, Path, Path]:
    editing = _load_app_module("editing")
    source_quality = _load_app_module("source_quality")
    analysis_path = song_dir / "audio_analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    clip_paths = _find_sources(clip_dir)
    quality_path = (
        source_quality_path
        if source_quality_path is not None
        else clip_dir / source_quality.MANIFEST_FILENAME
    )
    if not quality_path.is_file():
        raise source_quality.SourceQualityError(
            "offline recut requires edit-source-quality.json; pass "
            "--source-quality so rejected footage cannot re-enter by filename",
        )
    motif_sources = set(motif_sources or ())
    invalid_motif_sources = sorted(
        number
        for number in motif_sources
        if number < 1 or number > len(clip_paths)
    )
    if invalid_motif_sources:
        raise ValueError(
            "motif source numbers are outside the source set: "
            f"{invalid_motif_sources}",
        )
    reframe_sources = set(reframe_sources or ())
    invalid_reframe_sources = sorted(
        number
        for number in reframe_sources
        if number < 1 or number > len(clip_paths)
    )
    if invalid_reframe_sources:
        raise ValueError(
            "reframe source numbers are outside the source set: "
            f"{invalid_reframe_sources}",
        )
    scene_meta = _scene_meta(run_state_path)

    def annotation(index: int) -> dict:
        """This clip's authored scene, or {} when no plan was supplied."""
        return scene_meta[index] if index < len(scene_meta) else {}

    default_frames = [
        {
            "scene": index + 1,
            "index": index,
            "frames": _probe_decoded_frames(path),
            "path": path,
            "source_kind": "video",
        }
        for index, path in enumerate(clip_paths)
    ]
    resolved_sources = source_quality.resolve_sources(
        default_frames,
        source_quality.load_manifest(quality_path),
        manifest_dir=quality_path.parent,
        require_complete=True,
    )
    clip_paths = [item.path for item in resolved_sources]
    source_frames = [
        {
            "index": index,
            "frames": item.frames,
            "label": path.name,
            "source_kind": item.source_kind,
            "head_guard_frames": item.head_guard_frames,
            "tail_guard_frames": item.tail_guard_frames,
            # Fail closed. A generated action is never reusable merely because
            # it passed technical review; motif reuse needs an editorial
            # decision that the content can safely recur.
            "motif_allowed": (
                index + 1 in motif_sources
                or bool(annotation(index).get("edit_motif_allowed"))
            ) and item.source_kind == "video" and item.motif_reuse_allowed,
            "reframe_allowed": index + 1 in reframe_sources or bool(
                annotation(index).get("edit_reframe_allowed"),
            ),
            # A return must share the host's visual language and may not reach
            # ahead of it on the motif's arc.
            "motif_key": str(annotation(index).get("visual_motif") or ""),
            "motif_state": str(annotation(index).get("motif_state") or ""),
        }
        for index, (path, item) in enumerate(
            zip(clip_paths, resolved_sources, strict=True),
        )
    ]
    scene_cuts, sections = _timing_inputs(
        analysis,
        source_frames,
        fps=24,
        run_state_path=run_state_path,
    )
    lyric_cuts = _parse_srt_onsets(subtitle_path)
    target_seconds = None
    if pace:
        floor = editing.phrase_floor(analysis.get("beats") or [], lyric_cuts)
        if floor > 0:
            target_seconds, min_shot_seconds = editing.derive_cadence(
                floor, pace, chorus_half_phrase=chorus_half_phrase,
            )
    edl = editing.build_edl(
        duration_seconds=float(analysis["duration"]),
        beats=analysis.get("beats") or [],
        sections=sections,
        scene_cuts=scene_cuts,
        source_frames=source_frames,
        fps=24,
        bars_per_shot=bars_per_shot,
        target_seconds=target_seconds,
        min_shot_seconds=min_shot_seconds,
        lyric_cuts=lyric_cuts,
        protect_lyric_phrases=False,
        # Match production. Without this the proof takes every sung line as a
        # cut, which pins verses and choruses to the same line rate and makes
        # the proof disagree with the cut it is meant to be proving.
        lyric_stride=lyric_stride or editing.LYRIC_FIRST_STRIDE,
    )
    stats = editing.edl_stats(edl)
    stats["temporal_motion_audit"] = source_quality.audit_edl_temporal_motion(
        resolved_sources,
        edl.shots,
        fps=edl.fps,
    )
    framing_plan = [shot.framing for shot in edl.shots]
    stats["source_policy"] = {
        "primary_windows": "timeline-aligned-forward",
        "motif_default": "deny",
        "motif_sources": sorted(motif_sources),
        "bars_per_shot": dict(bars_per_shot or {}),
        "minimum_shot_seconds": min_shot_seconds,
        "lyric_stride": dict(lyric_stride or {}),
        "lyric_cut_onsets": lyric_cuts,
        "reframe_sources": sorted(reframe_sources),
        "reframe_style": "alternating-centered-punch-in-106",
        "source_quality_manifest": str(quality_path),
        "source_quality": [item.to_dict() for item in resolved_sources],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    edl_path = output.with_suffix(".edl.json")
    stats_path = output.with_suffix(".stats.json")
    edl_payload = edl.to_dict()
    for shot, framing in zip(
        edl_payload["shots"],
        framing_plan,
        strict=True,
    ):
        shot["framing"] = framing
    edl_path.write_text(
        json.dumps(edl_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    audio_path = _find_audio(song_dir)
    assembler = _load_app_module("assembler")
    edits = [
        {
            "source": clip_paths[shot.source_index],
            "source_kind": resolved_sources[
                shot.source_index
            ].source_kind,
            "start_frame": shot.src_in_frame,
            "end_frame": shot.src_out_frame,
            "framing": framing,
        }
        for shot, framing in zip(edl.shots, framing_plan, strict=True)
    ]
    silent = output.with_name(f"_{output.stem}-silent.mp4")
    if not asyncio.run(assembler.assemble_frame_native_edl(
        edits, silent, width=1280, height=720, fps=edl.fps,
    )):
        raise RuntimeError("frame-native assembly failed")
    try:
        if not asyncio.run(assembler.mux_audio(
            silent, audio_path, output, subtitles=subtitle_path,
        )):
            raise RuntimeError("audio mux failed")
    finally:
        silent.unlink(missing_ok=True)
    return output, edl_path, stats_path


def _parse_shot_ladder(values: list[str]) -> dict[str, int]:
    result = {}
    for value in values:
        section, separator, bars = value.partition("=")
        if not separator or not section.strip():
            raise ValueError(
                f"invalid shot ladder {value!r}; expected SECTION=BARS",
            )
        try:
            count = int(bars)
        except ValueError as exc:
            raise ValueError(
                f"invalid shot ladder bar count in {value!r}",
            ) from exc
        if count <= 0:
            raise ValueError("shot ladder bar counts must be positive")
        result[section.strip()] = count
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("song_dir", type=Path)
    parser.add_argument("clip_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--subtitles",
        type=Path,
        help="Optional SRT file to burn into the diagnostic proof",
    )
    parser.add_argument(
        "--run-state",
        type=Path,
        help="Optional Music Studio run.json for authored scene order/sections",
    )
    parser.add_argument(
        "--source-quality",
        type=Path,
        help=(
            "Complete edit-source-quality.json binding every scene to exact "
            "approved bytes; defaults to CLIP_DIR/edit-source-quality.json"
        ),
    )
    parser.add_argument(
        "--motif-source",
        action="append",
        default=[],
        type=int,
        metavar="N",
        help=(
            "Explicitly allow one 1-based source clip to recur as a motif; "
            "repeat the option for multiple reviewed ambient sources"
        ),
    )
    parser.add_argument(
        "--shot-ladder",
        action="append",
        default=[],
        metavar="SECTION=BARS",
        help="Override edit density for one section type",
    )
    parser.add_argument(
        "--reframe-source",
        action="append",
        default=[],
        type=int,
        metavar="N",
        help=(
            "Allow a subtle alternate crop on later cuts within one 1-based "
            "source scene; no frames are repeated or reordered"
        ),
    )
    parser.add_argument("--pace", default="",
                        choices=["", "held", "flowing", "driving"],
                        help="Director pace band; multiplies the measured phrase floor")
    parser.add_argument("--chorus-half-phrase", action="store_true",
                        help="Offer choruses a half-phrase grid (motif-gated)")
    parser.add_argument(
        "--lyric-stride",
        action="append",
        default=[],
        metavar="SECTION=LINES",
        help=(
            "How many sung lines one shot may span, per section "
            "(default: chorus=1, others=2)"
        ),
    )
    parser.add_argument(
        "--min-shot-seconds",
        type=float,
        default=2.0,
        help="Minimum distance between accepted scene/lyric/rhythm cuts",
    )
    args = parser.parse_args()
    if args.min_shot_seconds <= 0:
        parser.error("--min-shot-seconds must be positive")
    shot_ladder = _parse_shot_ladder(args.shot_ladder)
    paths = build_proof(
        args.song_dir.resolve(),
        args.clip_dir.resolve(),
        args.output.resolve(),
        subtitle_path=args.subtitles.resolve() if args.subtitles else None,
        run_state_path=args.run_state.resolve() if args.run_state else None,
        source_quality_path=(
            args.source_quality.resolve() if args.source_quality else None
        ),
        motif_sources=set(args.motif_source),
        bars_per_shot=shot_ladder,
        reframe_sources=set(args.reframe_source),
        min_shot_seconds=args.min_shot_seconds,
        lyric_stride=_parse_shot_ladder(args.lyric_stride) or None,
        pace=args.pace,
        chorus_half_phrase=args.chorus_half_phrase,
    )
    print(json.dumps({"proof": str(paths[0]), "edl": str(paths[1]), "stats": str(paths[2])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
