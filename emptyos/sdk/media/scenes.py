"""Scene planning — split content into visual scenes via LLM or mechanically."""

from __future__ import annotations

import json


async def plan_scenes(
    think_fn,
    script: list[dict],
    timings: list[dict],
    topic: str,
    num_scenes: int = 5,
) -> list[dict]:
    """LLM plans visual scenes based on narrative shifts.

    Args:
        think_fn: async callable (prompt, **kwargs) → str (e.g. app.think)
        script: list of {speaker, text}
        timings: list of {start_ms, end_ms, speaker, text}
        topic: podcast/video topic
        num_scenes: target number of scenes

    Returns: list of {start_ms, end_ms, summary, visual}
    """
    lines = [f"[{i}] {s['speaker']}: {s['text']}" for i, s in enumerate(script)]
    prompt = (
        f"You are a visual director for \"{topic}\".\n"
        f"Script:\n" + "\n".join(lines) + f"\n\n"
        f"Split into exactly {num_scenes} visual scenes based on narrative shifts.\n"
        f"For each scene, write a vivid IMAGE DESCRIPTION (setting, objects, mood, colors). No text in image.\n"
        f"Output ONLY valid JSON:\n"
        f'{{"scenes": [{{"start_seg": 0, "end_seg": 3, "visual": "A dimly lit library..."}}, ...]}}\n'
        f"Cover segments [0] to [{len(script)-1}] completely."
    )
    try:
        raw = await think_fn(prompt, domain="text", temperature=0.5)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        planned = data.get("scenes", [])
        if len(planned) < 2:
            return mechanical_scenes(script, timings, num_scenes)
        scenes = []
        for sc in planned[:num_scenes]:  # cap at requested count
            s_idx = max(0, min(sc.get("start_seg", 0), len(timings) - 1))
            e_idx = max(0, min(sc.get("end_seg", s_idx), len(timings) - 1))
            scenes.append({
                "start_ms": timings[s_idx]["start_ms"],
                "end_ms": timings[e_idx]["end_ms"],
                "summary": " ".join(script[j]["text"] for j in range(s_idx, e_idx + 1))[:300],
                "visual": sc.get("visual", ""),
            })
        return scenes
    except Exception:
        return mechanical_scenes(script, timings, num_scenes)


def mechanical_scenes(script: list[dict], timings: list[dict], num_scenes: int = 5) -> list[dict]:
    """Fallback: evenly split segments into scenes."""
    total = len(timings)
    per = max(1, total // num_scenes)
    scenes = []
    for i in range(0, total, per):
        chunk_t = timings[i:i + per]
        chunk_s = script[i:i + per]
        scenes.append({
            "start_ms": chunk_t[0]["start_ms"],
            "end_ms": chunk_t[-1]["end_ms"],
            "summary": " ".join(s["text"] for s in chunk_s)[:300],
            "visual": "",
        })
    return scenes[:num_scenes]
