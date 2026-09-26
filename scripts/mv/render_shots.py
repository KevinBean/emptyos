"""Plan and render an edited MV picture from a shots spec, with the MV grade.

    python scripts/mv/render_shots.py plan   shots.json            # write the EDL, run nothing
    python scripts/mv/render_shots.py render shots.json out.mp4    # grade + assemble + mix

This is the finishing pass after the edit is locked, lifted from the
〈說得太急〉 rescue (rescue-v18). The planner does whole-frame arithmetic:

- Each shot fills its slot from its clean source range, at
  ``speed = source_frames / record_frames``, clamped to ``[min_speed, 1]``.
  Below 1.0 the shot is motion-interpolated. That is for near-static shots
  only; a face shot should be given enough source to play at 1.0.
- A hard cut concatenates. A dissolve is a ``dissolve_frames`` xfade centred on
  the cut, so the timeline length is unchanged.
- No clip framing is used twice, and no two shots overlap in source time
  (the "no footage reused" rule).
- The picture ends at ``end_picture`` with a fade to black, then holds black to
  the song's end.

Spec (paths relative to the spec file):

    {"song_seconds": 177.12, "fps": 24, "end_picture": 169.0,
     "dissolve_frames": 12, "min_speed": 0.72, "width": 1920, "height": 1080,
     "audio": "../audio.wav", "ambience": "audio/rain.wav",
     "clip_dirs": ["../motion/v16/raw"], "clip_paths": {"V17-M01-a1": "..."},
     "upscale_dir": "upscale", "living_master": "living-master",
     "grade": {"memory": [...], "keep_exposure": [...], "night_pull": [...],
               "letterbox": 2.39, "fade_in": 1.2, "fade_out": 3.0},
     "shots": [{"t": 0.0, "clip": "V16-01-a1", "in": 0.0, "out": 8.0,
                "transition": "cut" | "dissolve" | null, "why": "...",
                "crop": "w:h:x:y" | null}, ...]}

A shot with ``crop`` reads a pre-rendered full-frame insert made by
``upscale_crops.py`` (``<upscale_dir>/<clip>_<in>.mp4``), which starts at the
shot's source in-point.

``living_master`` (optional) names the MV's living-master folder. When set,
every clip must be a source a human approved there (matched by sha256), or
the render is refused before it starts; the output then gets a final sidecar,
so the release guard accepts it. An MV that has a living master must finish
this way — the guard refuses a file that does not trace back to one.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import post  # noqa: E402

CUT, DISSOLVE = "cut", "dissolve"
SLOW = "minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"


class PlanError(Exception):
    """The shots spec cannot be rendered as written."""


@dataclass
class Spec:
    raw: dict
    base: Path

    @classmethod
    def load(cls, path: Path) -> "Spec":
        return cls(json.loads(path.read_text(encoding="utf-8")), path.resolve().parent)

    def get(self, key, default=None):
        return self.raw.get(key, default)

    def path(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else self.base / q

    @property
    def fps(self) -> int:
        return int(self.raw.get("fps", 24))

    def clip_path(self, clip: str) -> Path:
        named = (self.raw.get("clip_paths") or {}).get(clip)
        if named:
            return self.path(named)
        for d in self.raw.get("clip_dirs") or []:
            cand = self.path(d) / f"{clip}.mp4"
            if cand.is_file():
                return cand
        raise PlanError(f"no source file for clip {clip}")

    def crop_path(self, clip: str, src_in: float) -> Path:
        return self.path(self.raw.get("upscale_dir", "upscale")) / f"{clip}_{src_in:.2f}.mp4"

    def grade(self) -> post.Grade:
        g = self.raw.get("grade") or {}
        return post.Grade(
            memory=set(g.get("memory") or []),
            keep_exposure=set(g.get("keep_exposure") or []),
            night_pull=set(g.get("night_pull") or []),
            **{k: g[k] for k in ("exposure_target", "gain_min", "gain_max", "memory_haze",
                                 "halation_night", "halation_memory") if k in g})


def plan(spec: Spec) -> list[dict]:
    """Whole-frame EDL for the spec's shots. Pure: reads no files."""
    shots = spec.get("shots") or []
    if not shots:
        raise PlanError("no shots")
    for key in ("end_picture", "song_seconds"):
        if spec.get(key) is None:
            raise PlanError(f"the spec needs {key}")
    fps = spec.fps
    dis_f = int(spec.get("dissolve_frames", 12))
    min_speed = float(spec.get("min_speed", 0.72))
    end_picture = float(spec.get("end_picture"))
    if dis_f < 0 or dis_f % 2:
        raise PlanError("dissolve_frames must be an even number of frames (half each side of the cut)")
    if float(spec.get("song_seconds")) < end_picture:
        raise PlanError("song_seconds is shorter than end_picture")
    fade_out = float((spec.get("grade") or {}).get("fade_out", 3.0))
    if not 0 <= fade_out <= end_picture:
        raise PlanError("fade_out must fit inside the picture")
    if shots[0]["t"] != 0:
        raise PlanError("the first shot must start at 0")
    width, height = int(spec.get("width", 1920)), int(spec.get("height", 1080))
    for s in shots:
        if s.get("transition") not in (None, CUT, DISSOLVE):
            raise PlanError(f"{s['clip']} at {s['t']}s: transition must be cut, dissolve or null")
        if s.get("crop"):
            try:
                cw, ch = (int(v) for v in str(s["crop"]).split(":")[:2])
            except ValueError as exc:
                raise PlanError(f"{s['clip']}: crop must be w:h:x:y") from exc
            if abs(cw / ch - width / height) > 0.01 * width / height:
                raise PlanError(f"{s['clip']}: crop {cw}:{ch} is not the frame's shape "
                                f"({width}:{height}) and would be stretched")

    def f(sec: float) -> int:
        return int(round(sec * fps))

    keys = [(s["clip"], s.get("crop")) for s in shots]
    if len(keys) != len(set(keys)):
        dup = sorted({k for k in keys if keys.count(k) > 1})
        raise PlanError(f"a clip framing is used twice: {dup}")
    spans: dict[str, list[tuple[float, float]]] = {}
    for s in shots:
        for a0, a1 in spans.get(s["clip"], []):
            if not (s["out"] <= a0 or s["in"] >= a1):
                raise PlanError(f"overlapping source time in {s['clip']}")
        spans.setdefault(s["clip"], []).append((s["in"], s["out"]))
    times = [s["t"] for s in shots] + [end_picture]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise PlanError("shot times must increase and end before end_picture")

    n, segs = len(shots), []
    for i, s in enumerate(shots):
        t_next = times[i + 1]
        h_in = dis_f // 2 if (i and s.get("transition") == DISSOLVE) else 0
        h_out = dis_f // 2 if (i + 1 < n and shots[i + 1].get("transition") == DISSOLVE) else 0
        rec = f(t_next) - f(s["t"]) + h_in + h_out
        if (h_in or h_out) and f(t_next) - f(s["t"]) < h_in + h_out:
            raise PlanError(f"{s['clip']} at {s['t']}s is shorter than its dissolves")
        avail = f(s["out"]) - f(s["in"])
        speed = min(1.0, avail / rec)
        if speed < min_speed:
            raise PlanError(f"{s['clip']} at {s['t']}s would play at {speed:.3f}x "
                            f"(minimum {min_speed}); give it more source or less slot")
        src_n = min(avail, int(rec * speed) + 3)
        segs.append(dict(i=i, t=s["t"], dur=round(t_next - s["t"], 3), clip=s["clip"],
                         src_in_f=f(s["in"]), src_out_f=f(s["in"]) + src_n, record_frames=rec,
                         speed=round(speed, 3), dissolve_in=bool(h_in), why=s.get("why", ""),
                         crop=s.get("crop")))
    total = segs[0]["record_frames"]
    for s in segs[1:]:
        total += s["record_frames"] - (dis_f if s["dissolve_in"] else 0)
    if total != f(end_picture):
        raise PlanError(f"picture is {total} frames, end_picture is {f(end_picture)}")
    return segs


def build_command(spec: Spec, segs: list[dict], out: Path, *, gains: dict[int, float]) -> list[str]:
    """The full ffmpeg command for the graded picture plus the audio mix."""
    fps, grade = spec.fps, spec.grade()
    width, height = int(spec.get("width", 1920)), int(spec.get("height", 1080))
    dis_f = int(spec.get("dissolve_frames", 12))
    shots = spec.get("shots")
    g = spec.get("grade") or {}
    inputs, parts = [], []
    for s in segs:
        src_in_f, src_out_f = s["src_in_f"], s["src_out_f"]
        if s["crop"]:
            up = spec.crop_path(s["clip"], shots[s["i"]]["in"])
            if not up.is_file():
                raise PlanError(f"run upscale_crops.py first ({up.name})")
            inputs += ["-i", str(up)]
            src_out_f -= src_in_f
            src_in_f = 0
        else:
            inputs += ["-i", str(spec.clip_path(s["clip"]))]
        night = post.NIGHT_PULL if s["clip"] in grade.night_pull else ""
        slow = f"setpts=PTS/{s['speed']},{SLOW.format(fps=fps)}," if s["speed"] < 0.999 else ""
        parts.append(f"[{s['i']}:v]trim=start_frame={src_in_f}:end_frame={src_out_f},"
                     f"setpts=PTS-STARTPTS,{slow}trim=end_frame={s['record_frames']},"
                     f"setpts=PTS-STARTPTS,{night}null[pre{s['i']}]")
        parts.append(post.look(s["i"], s["clip"], gains[s["i"]], grade,
                               width=width, height=height, fps=fps))
    chain, length = "v0", segs[0]["record_frames"]
    for s in segs[1:]:
        ln, o = s["record_frames"], f"x{s['i']}"
        if s["dissolve_in"]:
            off = length - dis_f
            parts.append(f"[{chain}][v{s['i']}]xfade=transition=fade:duration={dis_f / fps:.6f}"
                         f":offset={off / fps:.6f}[{o}]")
            length = off + ln
        else:
            parts.append(f"[{chain}][v{s['i']}]concat=n=2:v=1:a=0,fps={fps}[{o}]")
            length += ln
        chain = o
    song = float(spec.get("song_seconds"))
    end_picture = float(spec.get("end_picture"))
    pad = int(round(song * fps)) - length + 2
    ratio = g.get("letterbox", 2.39)
    box = f"{post.letterbox(width, height, ratio)}," if ratio else ""
    fade_in, fade_out = float(g.get("fade_in", 1.2)), float(g.get("fade_out", 3.0))
    parts.append(f"[{chain}]{box}fade=t=in:st=0:d={fade_in},"
                 f"fade=t=out:st={end_picture - fade_out:.3f}:d={fade_out},"
                 f"tpad=stop_mode=add:stop={pad}:color=black[vout]")
    audio = ["-i", str(spec.path(spec.get("audio")))]
    ai = len(segs)
    if spec.get("ambience"):
        audio += ["-i", str(spec.path(spec.get("ambience")))]
        # normalize=0 so the song itself stays at exactly its mastered level
        parts.append(f"[{ai}:a][{ai + 1}:a]amix=inputs=2:normalize=0:duration=first[aout]")
        amap = "[aout]"
    else:
        amap = f"{ai}:a"
    return ["ffmpeg", "-v", "error", "-y", *inputs, *audio,
            "-filter_complex", ";".join(parts), "-map", "[vout]", "-map", amap,
            "-r", str(fps), "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-t", f"{song:.3f}".rstrip("0").rstrip("."),
            "-movflags", "+faststart", str(out)]


def source_errors(spec: Spec, segs: list[dict]) -> list[str]:
    """Checks that need the files: rate, length, placeholder renders, stale inserts."""
    import asyncio

    import living_master
    from emptyos.sdk.media.edl import probe_video_stream

    shots, errors = spec.get("shots"), []
    approved = None
    if spec.get("living_master"):
        try:
            approved = living_master.approved_source_shas(spec.path(spec.get("living_master")))
        except living_master.TimelineError as exc:
            errors.append(f"living_master: {exc}")
    for s in segs:
        if approved is not None:
            clip = spec.clip_path(s["clip"])
            if living_master.sha256_file(clip) not in approved:
                errors.append(f"{s['clip']} is not an approved source in the living master")
        if s["crop"]:
            src = spec.crop_path(s["clip"], shots[s["i"]]["in"])
            need = s["src_out_f"] - s["src_in_f"]
            want = crop_stamp(spec, shots[s["i"]])
            if read_stamp(src) != want:
                errors.append(f"{src.name} was made for different settings — delete it and rerun upscale_crops.py")
        else:
            src, need = spec.clip_path(s["clip"]), s["src_out_f"]
        blocked = living_master.release_block_reason(src)
        if blocked:
            errors.append(f"{s['clip']}: {blocked}")
        rate = living_master.probe_fps(src)
        if rate is None or abs(rate - spec.fps) > 0.01:
            errors.append(f"{src.name} runs at {rate} fps, the spec at {spec.fps}")
        have = asyncio.run(probe_video_stream(src))[1]
        if have < need:
            errors.append(f"{src.name} has {have} frames; shot {s['i']} reads to {need}")
    return errors


def write_final_sidecar(spec: Spec, out: Path) -> None:
    import living_master
    from datetime import datetime, timezone

    from emptyos.runtime.atomic_io import atomic_write_text

    atomic_write_text(living_master.sidecar_for(out), json.dumps({
        "schema_version": living_master.SCHEMA_VERSION, "master": out.name,
        "master_sha256": living_master.sha256_file(out), "placeholder_slots": 0, "final": True,
        "derived_from": str(spec.get("living_master")), "derived_by": "render_shots (approved clips)",
        "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2))


def crop_stamp(spec: Spec, shot: dict, *, blend: float | None = None, model: str | None = None) -> dict:
    """What a crop insert was made from; a different stamp means a stale insert."""
    stamp = {"clip": shot["clip"], "in": shot["in"], "out": shot["out"], "crop": shot["crop"],
             "fps": spec.fps, "width": int(spec.get("width", 1920)),
             "height": int(spec.get("height", 1080))}
    up = spec.get("upscale") or {}
    stamp["blend"] = blend if blend is not None else up.get("blend", 0.3)
    stamp["model"] = model if model is not None else up.get("model", "4xRealisticrescaler_100000G.pt")
    return stamp


def read_stamp(insert: Path) -> dict | None:
    p = insert.with_name(insert.name + ".json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def compute_gains(spec: Spec, segs: list[dict]) -> dict[int, float]:
    grade, shots, gains = spec.grade(), spec.get("shots"), {}
    for s in segs:
        if s["clip"] in grade.keep_exposure:
            gains[s["i"]] = 1.0
            continue
        if s["crop"]:
            src, a, b = spec.crop_path(s["clip"], shots[s["i"]]["in"]), 0, s["src_out_f"] - s["src_in_f"]
        else:
            src, a, b = spec.clip_path(s["clip"]), s["src_in_f"], s["src_out_f"]
        gains[s["i"]] = post.shot_gain(str(src), (a + b) // 2, grade)
    return gains


def write_edl(spec: Spec, segs: list[dict], gains: dict[int, float] | None, path: Path) -> None:
    rows = [{**s, **({"gain": round(gains[s["i"]], 3)} if gains else {})} for s in segs]
    dis_f = int(spec.get("dissolve_frames", 12))
    total = sum(s["record_frames"] - (dis_f if s["dissolve_in"] else 0) for s in segs)
    path.write_text(json.dumps(dict(song=spec.get("song_seconds"), fps=spec.fps,
                                    end_picture=spec.get("end_picture"), picture_frames=total,
                                    shots=rows), ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("spec", type=Path)
    p.add_argument("--edl", type=Path, help="where to write the EDL (default: <spec>.edl.json)")
    p = sub.add_parser("render")
    p.add_argument("spec", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--edl", type=Path, help="where to write the EDL (default: <spec>.edl.json)")
    args = ap.parse_args(argv)
    spec = Spec.load(args.spec)
    edl = args.edl or args.spec.with_suffix(".edl.json")
    try:
        segs = plan(spec)
        if args.cmd == "plan":
            write_edl(spec, segs, None, edl)
            for s in segs:
                print(f"{s['t']:7.2f} {s['clip']:14s} x{s['speed']:.2f}")
            print(f"EDL: {edl}")
            return 0
        errors = source_errors(spec, segs)
        if errors:
            raise PlanError("; ".join(errors))
        gains = compute_gains(spec, segs)
        r = subprocess.run(build_command(spec, segs, args.out, gains=gains),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise PlanError(f"ffmpeg failed: {r.stderr[-1500:]}")
        write_edl(spec, segs, gains, edl)
        print(f"rendered {args.out}")
        if spec.get("living_master"):
            write_final_sidecar(spec, args.out)
            print("  lineage: every clip approved in the living master (sidecar written)")
    except (PlanError, ValueError, OSError) as exc:
        print(f"render_shots: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
