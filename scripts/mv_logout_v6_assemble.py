"""Encode the revised v6 shots and assemble the Log Out art master."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encode_sequence(ffmpeg: str, frames: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg,
            "-y",
            "-framerate",
            "24",
            "-start_number",
            "1",
            "-i",
            str(frames / "frame-%04d.png"),
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )


def probe(ffprobe: str, path: Path) -> dict:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def assemble(ffmpeg: str, sources: list[Path], audio_master: Path, output: Path) -> None:
    graph = (
        "[0:v]trim=start=0:end=4,setpts=PTS-STARTPTS,"
        "eq=saturation=0.70:contrast=1.00:brightness=-0.026:gamma=1.015,"
        "fps=24,format=yuv420p[v0];"
        "[1:v]trim=start=0:end=4,setpts=PTS-STARTPTS,"
        "eq=saturation=0.76:contrast=1.01:brightness=-0.025:gamma=1.01,"
        "fps=24,format=yuv420p[v1];"
        "[2:v]tpad=stop_mode=clone:stop_duration=0.2,trim=start=0:end=4,"
        "setpts=PTS-STARTPTS,"
        "eq=saturation=0.88:contrast=1.02:brightness=-0.008:gamma=1.0,"
        "fps=24,format=yuv420p[v2];"
        "[3:v]trim=start=0:end=4,setpts=PTS-STARTPTS,"
        "eq=saturation=0.98:contrast=1.035:brightness=0.0:gamma=0.995,"
        "fps=24,format=yuv420p[v3];"
        "[4:v]trim=start=0:end=4,setpts=PTS-STARTPTS,"
        "eq=saturation=0.93:contrast=1.02:brightness=-0.004:gamma=1.005,"
        "fps=24,format=yuv420p[v4];"
        "[5:v]trim=start=0:end=4,setpts=PTS-STARTPTS,"
        "eq=saturation=0.78:contrast=0.96:brightness=-0.010:gamma=1.025,"
        "fps=24,format=yuv420p[v5];"
        "[6:v]trim=start=0:end=4,setpts=PTS-STARTPTS,"
        "eq=saturation=0.65:contrast=0.91:brightness=-0.022:gamma=1.03,"
        "fps=24,format=yuv420p[v6];"
        "[7:v]trim=start=0:end=10.4,setpts=PTS-STARTPTS,"
        "eq=saturation=0.50:contrast=0.90:brightness=-0.034:gamma=1.04,"
        "fps=24,format=yuv420p[v7];"
        "[v0][v1][v2][v3][v4][v5][v6][v7]concat=n=8:v=1:a=0,"
        "noise=alls=2:allf=t+u,fade=t=out:st=37.90:d=0.50:color=black[v]"
    )
    command = [ffmpeg, "-y"]
    for source in sources:
        command.extend(["-i", str(source)])
    command.extend(["-i", str(audio_master)])
    command.extend(
        [
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-map",
            "8:a:0",
            "-t",
            "38.4",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            "-metadata",
            "title=Log Out - Director's Cut v6",
            str(output),
        ]
    )
    run(command)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--song-root", type=Path, required=True)
    parser.add_argument("--v4-root", type=Path, required=True)
    parser.add_argument("--v6-root", type=Path, required=True)
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe must be available")

    song_root = args.song_root.resolve()
    v4_root = args.v4_root.resolve()
    v6_root = args.v6_root.resolve()
    revised = {
        "opening": v6_root / "scene-opening" / "clip.mp4",
        "leaf": v6_root / "scene-leaf" / "clip.mp4",
        "water": v6_root / "scene-water" / "clip.mp4",
        "vacancy": v6_root / "scene-vacancy" / "clip.mp4",
    }
    frame_sources = {
        # The opening deliberately reuses the clean vacancy composition. The
        # rejected imported character never enters the picture.
        "opening": v6_root / "scene-vacancy" / "frames",
        "leaf": v6_root / "scene-leaf" / "frames",
        "water": v6_root / "scene-water" / "frames",
        "vacancy": v6_root / "scene-vacancy" / "frames",
    }
    for name, clip in revised.items():
        encode_sequence(ffmpeg, frame_sources[name], clip)

    sources = [
        revised["opening"],
        v4_root / "scene-01" / "clip.mp4",
        v4_root / "scene-02" / "clip.mp4",
        v4_root / "scene-03" / "clip.mp4",
        revised["leaf"],
        revised["water"],
        v4_root / "scene-07" / "clip.mp4",
        revised["vacancy"],
    ]
    missing = [str(path) for path in sources if not path.exists()]
    if missing:
        raise FileNotFoundError("missing v6 source files:\n" + "\n".join(missing))

    audio_master = (
        song_root
        / "mv-blender-directors-cut-v5"
        / "log-out-directors-cut-v5-master.mp4"
    )
    output = v6_root / "log-out-directors-cut-v6-master.mp4"
    assemble(ffmpeg, sources, audio_master, output)

    edit = [
        {"start": 0.0, "end": 4.0, "source": "v6 clean threshold", "function": "held absence"},
        {"start": 4.0, "end": 8.0, "source": "scene-01", "function": "inhabited world"},
        {"start": 8.0, "end": 12.0, "source": "scene-02", "function": "warmth enters"},
        {"start": 12.0, "end": 16.0, "source": "scene-03", "function": "machine embodiment"},
        {"start": 16.0, "end": 20.0, "source": "v6 scanned leaf", "function": "organic release"},
        {"start": 20.0, "end": 24.0, "source": "v6 off-screen impact", "function": "absorption"},
        {"start": 24.0, "end": 28.0, "source": "scene-07", "function": "suspension"},
        {"start": 28.0, "end": 38.4, "source": "v6 vacancy", "function": "withdrawal to zero"},
    ]
    report = {
        "schema_version": 1,
        "status": "assembled_pending_art_review",
        "master": str(output),
        "master_sha256": sha256(output),
        "master_bytes": output.stat().st_size,
        "probe": probe(ffprobe, output),
        "edit": edit,
        "sources": [
            {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}
            for path in sources
        ],
        "removed_from_v5": [
            {"source": "scene-08", "reason": "literal power_box_01 read as unexplained electrical cabinet"},
            {"source": "v5 full-body Rain", "reason": "random doll/mannequin read; no visible character asset retained"},
            {"source": "v4 rigid fern fronds", "reason": "rigid anatomy and transform-only fall"},
            {"source": "v3 visible droplet", "reason": "false teardrop silhouette"},
            {"source": "v3 ripple", "reason": "graphic rings on an unreadable surface"},
        ],
        "director_contract": "director-revision-v6.md",
    }
    (v6_root / "production-v6.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
