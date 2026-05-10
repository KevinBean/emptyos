"""Task dependency resolver for project tasks.

Walks a task list, parses `depends_on` / `blocks` metadata refs, matches them
to other tasks (case-insensitive substring), and annotates each task with
ready/blocked_by state.
"""

from __future__ import annotations


def resolve_dependencies(task_list: list[dict]) -> list[dict]:
    """Resolve depends_on/blocks references between tasks.

    Matches by case-insensitive substring of task text.
    Annotates each task with: depends_on, blocks, ready, blocked_by.
    """
    text_map: dict[str, int] = {}
    for idx, t in enumerate(task_list):
        text_map[t["text"].lower().strip()] = idx

    def _find_task(ref: str) -> int | None:
        ref_lower = ref.strip().lower()
        if ref_lower in text_map:
            return text_map[ref_lower]
        for text, idx in text_map.items():
            if ref_lower in text or text in ref_lower:
                return idx
        return None

    for t in task_list:
        t["depends_on"] = []
        t["blocks"] = []
        t["blocked_by"] = []
        for m in t.get("meta", []):
            if m["type"] == "depends_on":
                for ref in m["value"].split(","):
                    ref = ref.strip()
                    if not ref:
                        continue
                    target = _find_task(ref)
                    if target is not None:
                        t["depends_on"].append({
                            "text": task_list[target]["text"],
                            "line": task_list[target]["line"],
                            "done": task_list[target]["done"],
                        })
                    else:
                        t["depends_on"].append({"text": ref, "line": -1, "done": False})
            elif m["type"] == "blocks":
                for ref in m["value"].split(","):
                    ref = ref.strip()
                    if not ref:
                        continue
                    target = _find_task(ref)
                    if target is not None:
                        t["blocks"].append({
                            "text": task_list[target]["text"],
                            "line": task_list[target]["line"],
                        })

    for t in task_list:
        if t["done"]:
            t["ready"] = True
            continue
        unmet = [d for d in t["depends_on"] if not d["done"]]
        if unmet:
            t["ready"] = False
            t["blocked_by"] = [d["text"] for d in unmet]
        else:
            t["ready"] = True

    return task_list
