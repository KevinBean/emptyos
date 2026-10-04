"""Assemble the Log Out v5 director's cut from approved Blender sources.

This is an edit/grade pass over the existing v4 technical master plus the
single re-rendered human bookend.  It deliberately removes the unrelated
analog-meter shot and assigns each retained shot one emotional function.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encode_scene09(ffmpeg: str, frames: Path, output: Path) -> None:
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


def assemble(
    ffmpeg: str,
    scene09: Path,
    v4_root: Path,
    song_root: Path,
    output: Path,
) -> None:
    sources = [
        scene09,
        v4_root / "scene-01" / "clip.mp4",
        v4_root / "scene-02" / "clip.mp4",
        v4_root / "scene-03" / "clip.mp4",
        v4_root / "scene-04" / "clip.mp4",
        song_root
        / "mv-blender-hybrid-realism-v3-final"
        / "scene-05-physical-v3"
        / "clip.mp4",
        v4_root / "scene-07" / "clip.mp4",
        v4_root / "scene-08" / "clip.mp4",
        v4_root / "log-out-realism-v4-master.mp4",
    ]
    missing = [str(path) for path in sources if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing v5 source files:\n" + "\n".join(missing))

    # Eight four-second states plus a 6.4-second withdrawal: 38.4 seconds.
    # The beginning and ending share the same composition on purpose, turning
    # the corrected character shot into a bookend rather than a late asset
    # introduction.
    graph = (
        "[0:v]trim=start=0:end=4,setpts=PTS-STARTPTS,"
        "eq=saturation=0.68:contrast=1.02:brightness=-0.035:gamma=1.01,"
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
        "eq=saturation=1.03:contrast=1.045:brightness=0.002:gamma=0.995,"
        "fps=24,format=yuv420p[v4];"
        "[5:v]trim=start=0:end=4,setpts=PTS-STARTPTS,"
        "eq=saturation=1.08:contrast=1.04:brightness=0.0:gamma=0.995,"
        "fps=24,format=yuv420p[v5];"
        "[6:v]trim=start=0:end=4,setpts=PTS-STARTPTS,"
        "eq=saturation=0.69:contrast=0.92:brightness=-0.018:gamma=1.025,"
        "fps=24,format=yuv420p[v6];"
        "[7:v]trim=start=0:end=4,setpts=PTS-STARTPTS,"
        "eq=saturation=0.52:contrast=0.87:brightness=-0.035:gamma=1.045,"
        "fps=24,format=yuv420p[v7];"
        "[0:v]trim=start=0:end=5.875,setpts=1.0893617021*(PTS-STARTPTS),"
        "eq=saturation=0.42:contrast=0.94:brightness=-0.042:gamma=1.025,"
        "fade=t=out:st=4.6:d=1.8:color=black,"
        "trim=start=0:end=6.4,fps=24,format=yuv420p[v8];"
        "[v0][v1][v2][v3][v4][v5][v6][v7][v8]"
        "concat=n=9:v=1:a=0[v]"
    )

    command = [ffmpeg, "-y"]
    for source in sources:
        command.extend(["-i", str(source)])
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
            "title=Log Out - Director's Cut v5",
            str(output),
        ]
    )
    run(command)


def probe(ffprobe: str, output: Path) -> dict:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--song-root", type=Path, required=True)
    parser.add_argument("--v4-root", type=Path, required=True)
    parser.add_argument("--v5-root", type=Path, required=True)
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe must be available on PATH")

    v5_root = args.v5_root.resolve()
    v5_root.mkdir(parents=True, exist_ok=True)
    scene09_clip = v5_root / "scene-09-bookend-keyed" / "clip.mp4"
    master = v5_root / "log-out-directors-cut-v5-master.mp4"
    encode_scene09(
        ffmpeg,
        v5_root / "scene-09-bookend-keyed" / "frames",
        scene09_clip,
    )
    assemble(
        ffmpeg,
        scene09_clip,
        args.v4_root.resolve(),
        args.song_root.resolve(),
        master,
    )
    report = {
        "schema_version": 1,
        "status": "assembled_pending_art_review",
        "master": str(master),
        "master_sha256": sha256(master),
        "master_bytes": master.stat().st_size,
        "probe": probe(ffprobe, master),
        "edit": [
            {"start": 0.0, "end": 4.0, "source": "scene-09-bookend", "function": "human presence"},
            {"start": 4.0, "end": 8.0, "source": "scene-01", "function": "world after presence"},
            {"start": 8.0, "end": 12.0, "source": "scene-02", "function": "warmth enters"},
            {"start": 12.0, "end": 16.0, "source": "scene-03", "function": "machine gains body"},
            {"start": 16.0, "end": 20.0, "source": "scene-04", "function": "last organic release"},
            {"start": 20.0, "end": 24.0, "source": "scene-05", "function": "world absorbs residue"},
            {"start": 24.0, "end": 28.0, "source": "scene-07", "function": "frictionless suspension"},
            {"start": 28.0, "end": 32.0, "source": "scene-08", "function": "clearance"},
            {"start": 32.0, "end": 38.4, "source": "scene-09-bookend", "function": "return and absence"},
        ],
        "removed": [
            {
                "source": "scene-06",
                "reason": "literal analog meter broke the continuous nocturnal-world contract",
            }
        ],
    }
    (v5_root / "production-v5.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
