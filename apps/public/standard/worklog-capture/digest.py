"""Digest pipeline — ingest, OCR, correlate with evidence, draft worklog lines.

A plain linear function, NOT ``sdk/pipeline.py``: every stage is seconds-cheap
and the per-entry ``status`` gives resume for free (digest only touches ``new``
captures). Stages:

    1. ingest    move screenshots from the watch folder into queue entries
    2. ocr       ocr_image seam: ocrmac (Apple Vision) -> ocr plugin -> ""
    3. gather    read evidence sources (sessions, browser) over the window
    4. correlate (pure) bucket captures + evidence into time clusters
    5. draft     one worklog-line draft per cluster via the LOCAL think provider

``run_digest(app, draft=False)`` stops after correlate — the LLM-free test seam.
Per-cluster failures are captured (status="error") and never abort the run.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from emptyos.sdk.utils import parse_llm_json

from . import sources, store

_STATUS = ("complete", "in-progress", "todo", "next", "waiting", "review", "blocked")
_IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
_OCR_BUDGET = 4000          # chars of OCR text handed to the model per cluster
_WINDOW_CAP = timedelta(hours=48)
_DEFAULT_LOOKBACK = timedelta(hours=2)


# ── OCR seam (local-first, no cloud) ──────────────────────────────────────────
async def _ocr_ocrmac(path: str) -> str:
    """Apple Vision OCR via the optional ``ocrmac`` package. '' when unavailable."""
    def _run() -> str:
        try:
            from ocrmac import ocrmac  # lazy: macOS-only optional dep
        except Exception:
            return ""
        try:
            anns = ocrmac.OCR(path).recognize()
            return "\n".join(a[0] for a in anns if a and a[0])
        except Exception:
            return ""
    return await asyncio.to_thread(_run)


async def ocr_image(app, path: str) -> str:
    """Extract text from an image: ocrmac -> ocr plugin service -> "" (image-only)."""
    text = await _ocr_ocrmac(path)
    if text.strip():
        return text
    # Fall back to the ocr plugin (marker-pdf venv) if it's loaded.
    try:
        svc = app.kernel.services.get_optional("ocr")
        if svc is not None:
            out = await svc.ocr(path)
            if isinstance(out, str) and out.strip():
                return out
    except Exception:
        pass
    return ""


# ── ingest ────────────────────────────────────────────────────────────────────
def _ingest_folder_sync(watch: Path, queue: Path) -> list[str]:
    new_ids: list[str] = []
    for f in sorted(watch.iterdir()):
        if not f.is_file() or f.suffix.lower() not in _IMAGE_EXTS or f.name.startswith("."):
            continue
        try:
            meta = store.create_entry(queue, "folder", has_image=True, note="")
            dest = store.shot_path(queue, meta["id"])
            f.replace(dest)  # move — keep the inbox a true inbox
            new_ids.append(meta["id"])
        except Exception:
            continue
    return new_ids


async def ingest_folder(app) -> list[str]:
    """Move new screenshots from the watch folder into fresh queue entries."""
    watch = app.watch_dir()
    if not watch or not watch.exists():
        return []
    return await asyncio.to_thread(_ingest_folder_sync, watch, app.queue_dir())


# ── correlate (pure) ──────────────────────────────────────────────────────────
def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def correlate(captures: list[dict], evidence: list, window: timedelta) -> list[dict]:
    """Bucket captures + evidence into time clusters.

    ``captures``: dicts with ``id``, ``created_dt`` (datetime), ``app_name`` (+
    passthrough OCR/context fields). ``evidence``: ``sources.Evidence`` spans.
    Consecutive captures with the same app within ``window`` merge under a
    leader; overlapping evidence attaches to a cluster; leftover evidence groups
    into ``evidence_only`` clusters. Returns clusters newest-anchored first.
    """
    caps = sorted((c for c in captures if c.get("created_dt")), key=lambda c: c["created_dt"])
    clusters: list[dict] = []
    for c in caps:
        last = clusters[-1] if clusters else None
        if (last and last["kind"] == "capture"
                and last["app_name"] == c.get("app_name", "")
                and c["created_dt"] - last["end"] <= window):
            last["captures"].append(c)
            last["end"] = c["created_dt"]
        else:
            clusters.append({
                "kind": "capture",
                "leader": c["id"],
                "app_name": c.get("app_name", ""),
                "start": c["created_dt"],
                "end": c["created_dt"],
                "captures": [c],
                "evidence": [],
            })

    used = set()
    for cl in clusters:
        for i, ev in enumerate(evidence):
            if ev.start <= cl["end"] + window and ev.end >= cl["start"] - window:
                cl["evidence"].append(ev)
                used.add(i)

    leftover = [ev for i, ev in enumerate(evidence) if i not in used]
    leftover.sort(key=lambda e: e.start)
    for ev in leftover:
        last = clusters[-1] if clusters else None
        if (last and last["kind"] == "evidence_only"
                and ev.start - last["end"] <= window):
            last["evidence"].append(ev)
            last["end"] = max(last["end"], ev.end)
        else:
            clusters.append({
                "kind": "evidence_only",
                "leader": None,
                "app_name": "",
                "start": ev.start,
                "end": ev.end,
                "captures": [],
                "evidence": [ev],
            })
    return clusters


# ── context + draft ───────────────────────────────────────────────────────────
def _truncate(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "\n…[truncated]"


def build_context(cluster: dict, projects: list[str], employers: list[str], today: str) -> str:
    lines: list[str] = [f"Today is {today}."]
    caps = cluster.get("captures", [])
    if caps:
        apps = sorted({c.get("app_name", "") for c in caps if c.get("app_name")})
        titles = [c.get("window_title", "") for c in caps if c.get("window_title")]
        urls = [c.get("browser_url", "") for c in caps if c.get("browser_url")]
        sels = [c.get("selection", "") for c in caps if c.get("selection")]
        notes = [c.get("note", "") for c in caps if c.get("note")]
        span = f"{cluster['start']:%H:%M}–{cluster['end']:%H:%M}"
        lines.append(f"Active app(s): {', '.join(apps) or 'unknown'} ({span}, "
                     f"{len(caps)} capture(s)).")
        if titles:
            lines.append("Window title(s): " + " | ".join(titles[:4]))
        if urls:
            lines.append("Browser URL(s): " + " | ".join(urls[:4]))
        if notes:
            lines.append("User note(s): " + " | ".join(notes[:4]))
        if sels:
            lines.append("Selected text: " + _truncate(" / ".join(sels), 500))
        ocr = "\n".join(c.get("ocr", "") for c in caps if c.get("ocr"))
        if ocr.strip():
            lines.append("On-screen text (OCR):\n" + _truncate(ocr, _OCR_BUDGET))
    else:
        lines.append("No screenshot — inferred from the activity trail only "
                     "(mark confidence low).")
    ev = cluster.get("evidence", [])
    if ev:
        lines.append("Corroborating activity trail:")
        lines += [f"  - {e.summary}" for e in ev[:12]]
    lines.append("Known projects (choose ONLY from these, else 'General'): "
                 + (", ".join(projects[:40]) or "(none)"))
    lines.append("Known employers (choose ONLY from these, else ''): "
                 + (", ".join(employers) or "(none)"))
    return "\n".join(lines)


def _normalize_draft(parsed: dict, projects: list[str], employers: list[str],
                     today: str, fallback_text: str, low_conf: bool) -> dict:
    project = str(parsed.get("project") or "General").strip() or "General"
    if project != "General" and projects and project.lower() not in {p.lower() for p in projects}:
        project = "General"
    status = str(parsed.get("status") or "").strip().lower()
    if status not in _STATUS:
        status = "in-progress"
    employer = str(parsed.get("employer") or "").strip()
    if employer and employers and employer.lower() not in {e.lower() for e in employers}:
        employer = ""
    text = str(parsed.get("text") or "").strip() or fallback_text
    conf = str(parsed.get("confidence") or "").strip().lower()
    if conf not in ("high", "medium", "low"):
        conf = "medium"
    if low_conf and conf == "high":
        conf = "medium"
    return {"project": project, "text": text, "status": status,
            "date": today, "employer": employer, "confidence": conf}


async def _draft_cluster(app, cluster: dict, projects: list[str],
                         employers: list[str], today: str) -> dict:
    from .prompts import PROMPTS  # lazy: keeps pure functions path-loadable
    context = build_context(cluster, projects, employers, today)
    raw = await app.think(context, system=PROMPTS.capture_draft_system,
                          domain="text", temperature=0.1)
    parsed = parse_llm_json(raw, fallback={}) or {}
    caps = cluster.get("captures", [])
    fallback = (caps[0].get("app_name") if caps else "") or "Activity"
    return _normalize_draft(parsed, projects, employers, today,
                            fallback_text=f"Worked in {fallback}",
                            low_conf=cluster["kind"] == "evidence_only")


# ── state ─────────────────────────────────────────────────────────────────────
def _state_path(app) -> Path:
    return app.data_dir / "state.json"


def _read_last_digest(app) -> datetime | None:
    try:
        d = json.loads(_state_path(app).read_text(encoding="utf-8"))
        return datetime.fromisoformat(d["last_digest"])
    except Exception:
        return None


def _write_last_digest(app, when: datetime) -> None:
    try:
        _state_path(app).parent.mkdir(parents=True, exist_ok=True)
        _state_path(app).write_text(
            json.dumps({"last_digest": when.isoformat()}), encoding="utf-8")
    except Exception:
        pass


# ── orchestrator ──────────────────────────────────────────────────────────────
async def run_digest(app, *, draft: bool = True, now: datetime | None = None) -> dict:
    """Full digest pass. ``draft=False`` stops after correlate (test seam)."""
    now = now or datetime.now().astimezone()
    queue = app.queue_dir()

    ingested = await ingest_folder(app)

    # OCR every capture that hasn't been OCR'd yet.
    metas = [m for m in store.iter_metas(queue) if m.get("status") == store.STATUS_NEW]
    cap_dicts: list[dict] = []
    for m in metas:
        cid = m["id"]
        ocr = store.read_ocr(queue, cid)
        if not ocr and m.get("has_image") and store.shot_path(queue, cid).exists():
            ocr = await ocr_image(app, str(store.shot_path(queue, cid)))
            if ocr:
                store.ocr_path(queue, cid).write_text(ocr, encoding="utf-8")
        cap_dicts.append({
            "id": cid, "created_dt": _parse_dt(m.get("created", "")),
            "app_name": m.get("app_name", ""), "window_title": m.get("window_title", ""),
            "browser_url": m.get("browser_url", ""), "selection": m.get("selection", ""),
            "note": m.get("note", ""), "ocr": ocr,
        })

    # Evidence window: cover pending captures + since last digest, capped at 48h.
    last = _read_last_digest(app)
    cap_times = [c["created_dt"] for c in cap_dicts if c["created_dt"]]
    lo = min([t for t in cap_times] + [last or (now - _DEFAULT_LOOKBACK)])
    lo = max(lo, now - _WINDOW_CAP)
    evidence: list = []
    # Session scan + browser sqlite read are unbounded synchronous file I/O —
    # run them OFF the event loop so a big ~/.claude / History can't wedge the
    # daemon (CLAUDE.md dev-gotchas: sync work in async context).
    if app.cfg("source_sessions", True):
        evidence += await asyncio.to_thread(sources.source_sessions, lo, now)
    if app.cfg("source_browser", True):
        evidence += await asyncio.to_thread(sources.source_browser_history, lo, now)
    if app.cfg("source_calendar", True):
        tz = now.tzinfo or timezone.utc
        days: dict[str, list] = {}
        dd, end_d = lo.date(), now.date()
        while dd <= end_d:  # window is capped at 48h → at most 3 days
            ag, _err = await app.try_call_app("calendar", "get_agenda",
                                              target_date=dd.isoformat())
            if isinstance(ag, list):
                days[dd.isoformat()] = ag
            dd += timedelta(days=1)
        evidence += sources.build_calendar_evidence(days, tz)

    window = timedelta(minutes=int(app.cfg("group_window_min", 10) or 10))
    clusters = correlate(cap_dicts, evidence, window)

    result = {"ingested": len(ingested), "captures": len(cap_dicts),
              "evidence": len(evidence), "clusters": len(clusters),
              "drafted": 0, "errors": 0}
    if not draft:
        _write_last_digest(app, now)
        return result

    today = now.date().isoformat()
    ranked = await app.call_app("worklog", "project_names")
    projects = [n for n, _ in (ranked or [])][:60]
    emp_rows, _err = await app.try_call_app("worklog", "employer_names")
    employers = emp_rows if isinstance(emp_rows, list) else []

    for cl in clusters:
        # evidence_only clusters older than last digest were already surfaced.
        if cl["kind"] == "evidence_only" and last and cl["start"] < last:
            continue
        try:
            d = await _draft_cluster(app, cl, projects, employers, today)
        except Exception as e:
            if cl["kind"] == "capture":
                m = store.read_meta(queue, cl["leader"]) or {}
                m["status"] = store.STATUS_ERROR
                m["error"] = str(e)[:300]
                store.write_meta(queue, cl["leader"], m)
                result["errors"] += 1
            continue
        _persist_cluster(app, queue, cl, d, now)
        result["drafted"] += 1

    await asyncio.to_thread(store.sweep_retention, queue,
                            int(app.cfg("retention_days", 30) or 30), now=now)
    _write_last_digest(app, now)
    await app.emit("worklog-capture:digested", dict(result))
    return result


def _persist_cluster(app, queue: Path, cluster: dict, draft: dict, now: datetime) -> None:
    ev_dicts = [e.as_dict() for e in cluster.get("evidence", [])]
    if cluster["kind"] == "capture":
        leader = cluster["leader"]
        members = [c["id"] for c in cluster["captures"][1:]]
        m = store.read_meta(queue, leader) or {}
        m.update({"status": store.STATUS_DRAFTED, "draft": draft,
                  "members": members, "evidence": ev_dicts, "group_leader": None})
        store.write_meta(queue, leader, m)
        for mid in members:  # fold members under the leader
            mm = store.read_meta(queue, mid) or {}
            mm.update({"status": store.STATUS_MERGED, "group_leader": leader})
            store.write_meta(queue, mid, mm)
    else:  # evidence_only — synthesize an image-less trail entry
        meta = store.create_entry(queue, "trail", now=now, has_image=False)
        meta.update({"status": store.STATUS_DRAFTED, "draft": draft,
                     "evidence": ev_dicts, "evidence_only": True,
                     "window_title": cluster["evidence"][0].summary if cluster["evidence"] else ""})
        store.write_meta(queue, meta["id"], meta)
