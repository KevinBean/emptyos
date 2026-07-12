"""eos rooms — terminal REPL streaming a conversation against any rooms agent.

A thin shell over apps/rooms — every feature the web rooms UI (/rooms/) has
is reachable from here. Picks up the last-used room by default, lists/switches
rooms with slash commands, surfaces the responder name for group rooms, and
renders pending [DO:] actions as Apply/Reject cards.

Distinct from `eos chat` (provided by apps/agent), which is the autonomous
tool-use REPL — single session, Claude-Code-style permission gating, runs
the agent_loop with bundled Read/Edit/Write/Bash/etc. tools. This client is
the chat-shape paradigm: multi-participant rooms, persona-driven, [DO:]-token
review gate, journal-style logging. Different UX shapes, both useful.

POST /rooms/api/chat/stream emits NDJSON chunks. We consume them and write
text tokens straight to stdout for low-latency streaming. [DO:] tokens land
as pending_action chunks at the end of the turn (after the rooms gate
parses them); we surface each as a Rich panel and prompt Apply/Reject/Skip.

Daemon-bound: rooms requires the live FastAPI server. No local-kernel
fallback.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import aiohttp
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from emptyos.cli._common import probe_health as _probe_daemon

DEFAULT_AGENT_ID = "cli-chat"
DEFAULT_NAME = "CLI Chat"
DEFAULT_SYSTEM = (
    "You are a helpful EmptyOS assistant in a terminal REPL. Keep "
    "responses concise — the user is reading in a small terminal pane. "
    "When you want to mutate state (add a task, capture a note, send "
    "a message, etc.) emit a [DO:app.method({\"arg\":\"value\"})] token "
    "inline; the user reviews each as a card and applies or rejects. "
    "Never describe an action and skip the token — the user only sees "
    "what you emit."
)

console = Console()


# ─────────────────────────── Local state ─────────────────────────────

def _state_path() -> Path:
    p = Path.home() / ".config" / "emptyos"
    p.mkdir(parents=True, exist_ok=True)
    return p / "chat-state.json"


def _load_state() -> dict:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _state_path().write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # Best-effort; lose last-room if write fails.


# ─────────────────────────── NDJSON streaming ────────────────────────

async def _stream_ndjson(
    session: aiohttp.ClientSession, url: str, payload: dict,
) -> AsyncIterator[dict]:
    """POST payload, yield each NDJSON line as a parsed dict."""
    async with session.post(url, json=payload) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
        buffer = b""
        async for chunk in resp.content.iter_any():
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        tail = buffer.strip()
        if tail:
            try:
                yield json.loads(tail)
            except json.JSONDecodeError:
                pass


# ─────────────────────────── Room / agent fetchers ───────────────────

async def _list_all_rooms(
    session: aiohttp.ClientSession, base_url: str,
) -> list[dict]:
    """All addressable rooms — 1:1 agent rooms + group rooms, deduped by id.

    `/api/agents` returns every 1:1 agent (each is its own room).
    `/api/rooms` includes group rooms; in some deployments it also returns
    1:1s — we union and dedupe.
    """
    rooms: dict[str, dict] = {}
    async with session.get(f"{base_url}/rooms/api/agents") as r:
        if r.status == 200:
            data = await r.json()
            if isinstance(data, list):
                for a in data:
                    if isinstance(a, dict) and a.get("id"):
                        rooms[a["id"]] = a
    async with session.get(f"{base_url}/rooms/api/rooms") as r:
        if r.status == 200:
            data = await r.json()
            if isinstance(data, list):
                for a in data:
                    if isinstance(a, dict) and a.get("id"):
                        # Group rooms have richer participant info — overwrite.
                        rooms[a["id"]] = a
    return list(rooms.values())


async def _ensure_default_agent(
    session: aiohttp.ClientSession, base_url: str,
) -> dict:
    """Get-or-create the cli-chat fallback agent. Only called when no
    last-used room exists in local state."""
    async with session.get(
        f"{base_url}/rooms/api/agents/{DEFAULT_AGENT_ID}"
    ) as r:
        if r.status == 200:
            data = await r.json()
            if isinstance(data, dict) and not data.get("error") and data.get("id"):
                return data
        elif r.status == 401:
            raise RuntimeError(
                "Daemon rejected the auth token. Check `auth_token` in "
                "emptyos.toml matches the running daemon's config."
            )
    payload = {
        "id": DEFAULT_AGENT_ID,
        "name": DEFAULT_NAME,
        "system_prompt": DEFAULT_SYSTEM,
        "server_actions": {"repo": ["read", "grep", "edit", "write", "exec"]},
    }
    async with session.post(f"{base_url}/rooms/api/agents", json=payload) as r:
        result = await r.json()
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(f"Could not create agent: {result['error']}")
        return result


async def _load_history(
    session: aiohttp.ClientSession, base_url: str, agent_id: str,
) -> list:
    async with session.get(f"{base_url}/rooms/api/history/{agent_id}") as r:
        if r.status != 200:
            return []
        data = await r.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("messages") or []
        return []


# ─────────────────────────── Room resolution ─────────────────────────

def _resolve_room(rooms: list[dict], query: str) -> dict | None:
    """Fuzzy match a room by id, name, or number-in-list.

    Resolution order:
      1. Exact id match.
      2. Number (1-based index into the list as shown by /rooms).
      3. Exact case-insensitive name match.
      4. Substring match on id or name (only if unambiguous).
    """
    q = (query or "").strip()
    if not q:
        return None
    for r in rooms:
        if r.get("id") == q:
            return r
    if q.isdigit():
        idx = int(q) - 1
        if 0 <= idx < len(rooms):
            return rooms[idx]
    lower = q.lower()
    for r in rooms:
        if (r.get("name") or "").lower() == lower:
            return r
    subs = [
        r for r in rooms
        if lower in (r.get("id") or "").lower()
        or lower in (r.get("name") or "").lower()
    ]
    if len(subs) == 1:
        return subs[0]
    return None


def _room_label(room: dict, idx: int | None = None) -> str:
    name = room.get("name") or room.get("id", "?")
    rid = room.get("id", "?")
    parts = room.get("participants") or []
    kind = "group" if len(parts) > 2 else "1:1"
    bits = [f"{idx}." if idx is not None else "", name, f"({rid})", f"[{kind}]"]
    return " ".join(b for b in bits if b)


def _participant_names(room: dict) -> list[str]:
    parts = room.get("participants") or []
    out = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        t = p.get("type", "?")
        nid = p.get("id") or p.get("name") or "?"
        out.append(f"{t}:{nid}")
    return out


# ─────────────────────────── Pending action UX ───────────────────────

# Diff rendering lives in the shared SDK module (also used by the agent REPL's
# terminal approval — `eos chat`). Kept under the local name for call sites below.
from emptyos.sdk.diff_render import render_diff_lines as _render_diff_lines  # noqa: E402


def _render_pending(action: dict) -> Panel:
    app_name = action.get("app", "?")
    method = action.get("method", "?")
    args = action.get("args", {})
    err = action.get("error")
    proposed = action.get("proposed_changes") or []
    proposed_cmd = action.get("proposed_command")

    # Impact-shaped action (repo.exec) — preview the command + cwd before
    # any execution. Distinct from diff-shaped so the user visually sees
    # "this will run a command" vs "this will edit a file".
    if proposed_cmd and not err:
        actor = action.get("source_actor") or {}
        actor_str = f"{actor.get('type','?')}/{actor.get('id','?')}"
        cmd_text = (proposed_cmd.get("cmd") or "").replace("[", r"\[")
        # Multi-line commands display with the $ prefix only on the first line.
        first, _, rest = cmd_text.partition("\n")
        body_lines = [
            f"[bold]{app_name}.{method}[/bold]",
            f"[cyan]$[/cyan] {first}",
        ]
        if rest:
            body_lines.append(rest)
        body_lines.append(
            f"[dim]cwd: {proposed_cmd.get('cwd')}    "
            f"shell: {proposed_cmd.get('shell')}    "
            f"timeout: {proposed_cmd.get('timeout')}s[/dim]"
        )
        body_lines.append(f"[dim]from {actor_str}[/dim]")
        return Panel(
            "\n".join(body_lines),
            title=f"[yellow]pending exec[/yellow] · {action.get('id','?')}",
            border_style="yellow",
            expand=False,
        )

    # Diff-shaped actions (repo.edit / repo.write / rooms.write_note) get
    # a richer card — the diff IS the value of the review gate. Args block
    # is collapsed to just the path since `content` / `old` / `new` are
    # already shown in the diff itself.
    if proposed and not err:
        sections = [f"[bold]{app_name}.{method}[/bold]"]
        for change in proposed:
            path = change.get("path") or "?"
            diff_lines = change.get("diff_lines") or []
            sections.append(f"[bold]→ {path}[/bold]")
            if diff_lines:
                sections.append(_render_diff_lines(diff_lines))
            else:
                sections.append("[dim](no diff produced — file may be unchanged)[/dim]")
        actor = action.get("source_actor") or {}
        actor_str = f"{actor.get('type','?')}/{actor.get('id','?')}"
        sections.append(f"[dim]from {actor_str}[/dim]")
        return Panel(
            "\n".join(sections),
            title=f"[yellow]pending diff[/yellow] · {action.get('id','?')}",
            border_style="yellow",
            expand=False,
        )

    # Standard non-diff action card.
    try:
        args_str = json.dumps(args, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        args_str = str(args)
    actor = action.get("source_actor") or {}
    actor_str = f"{actor.get('type','?')}/{actor.get('id','?')}"
    parts = [f"[bold]{app_name}.{method}[/bold]", args_str]
    if err:
        parts.append(f"[red]error: {err}[/red]")
    parts.append(f"[dim]from {actor_str}[/dim]")
    return Panel(
        "\n".join(parts),
        title=f"[yellow]pending[/yellow] · {action.get('id','?')}",
        border_style="yellow",
        expand=False,
    )


async def _ask_choice(prompt: str) -> str:
    """Run console.input in a thread so we don't block the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: console.input(prompt))


async def _resolve_pending(
    session: aiohttp.ClientSession, base_url: str, actions: list[dict],
) -> None:
    if not actions:
        return
    for action in actions:
        console.print(_render_pending(action))
        try:
            choice = (await _ask_choice(
                "[dim]Apply (a) / Reject (r) / Skip (s)? [/dim]"
            )).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]skipped — review in web UI[/dim]")
            return
        action_id = action.get("id")
        if not action_id:
            continue
        if choice.startswith("a"):
            try:
                async with session.post(
                    f"{base_url}/rooms/api/pending/{action_id}/apply"
                ) as r:
                    data = await r.json()
                if data.get("ok") or data.get("status") == "applied":
                    result = data.get("result")
                    if result is not None:
                        # Show a compact summary so the user can confirm the
                        # action actually did what was advertised.
                        try:
                            preview = json.dumps(result, ensure_ascii=False)[:240]
                        except (TypeError, ValueError):
                            preview = str(result)[:240]
                        console.print(f"[green]✓ applied[/green] [dim]{preview}[/dim]")
                    else:
                        console.print("[green]✓ applied[/green]")
                else:
                    console.print(f"[red]✗ {data.get('error') or data}[/red]")
            except aiohttp.ClientError as e:
                console.print(f"[red]apply failed:[/red] {e}")
        elif choice.startswith("r"):
            try:
                async with session.post(
                    f"{base_url}/rooms/api/pending/{action_id}/reject"
                ) as r:
                    await r.read()
                console.print("[dim]rejected[/dim]")
            except aiohttp.ClientError as e:
                console.print(f"[red]reject failed:[/red] {e}")
        else:
            console.print("[dim]skipped — still pending in web UI[/dim]")


# ─────────────────────────── One conversation turn ───────────────────

async def _do_turn(
    session: aiohttp.ClientSession, base_url: str,
    room: dict, text: str,
) -> None:
    pending: list[dict] = []
    responder_id: str | None = None
    url = f"{base_url}/rooms/api/chat/stream"
    payload = {"agent_id": room["id"], "text": text}
    is_group = len(room.get("participants") or []) > 2
    header_printed = False

    try:
        async for chunk in _stream_ndjson(session, url, payload):
            if chunk.get("error"):
                console.print(f"\n[red]server error:[/red] {chunk['error']}")
                continue
            if "text_replace" in chunk:
                console.print(
                    "\n[dim](response refined — [DO:] tokens stripped)[/dim]"
                )
                continue
            if chunk.get("pending_action"):
                pending.append(chunk["pending_action"])
                continue
            t = chunk.get("text")
            if t:
                # In group rooms, print the responder name once before the
                # first text chunk so the user knows who's speaking.
                if is_group and not header_printed and responder_id:
                    console.print(
                        f"\n[bold cyan]{responder_id}:[/bold cyan] ", end=""
                    )
                    header_printed = True
                sys.stdout.write(t)
                sys.stdout.flush()
            if chunk.get("responder_id"):
                responder_id = chunk["responder_id"]
    except RuntimeError as e:
        console.print(f"\n[red]stream error:[/red] {e}")
        return
    except aiohttp.ClientError as e:
        console.print(f"\n[red]network error:[/red] {e}")
        return

    sys.stdout.write("\n")
    sys.stdout.flush()

    footer_bits = []
    if responder_id and responder_id != room["id"]:
        footer_bits.append(f"responder={responder_id}")
    if pending:
        footer_bits.append(f"pending={len(pending)}")
    if footer_bits:
        console.print(f"[dim]· {' · '.join(footer_bits)}[/dim]")

    await _resolve_pending(session, base_url, pending)


# ─────────────────────────── Slash commands ──────────────────────────

HELP_TEXT = """[dim]Slash commands:
  /rooms             list all rooms (with numbers for quick switching)
  /switch <id|num>   switch to another room (fuzzy: /switch jobs)
  /new <name>        create a new 1:1 agent and switch to it
  /who               show current room id + participants
  /model [provider]  show/set room think provider; /model reset clears
  /temperature [n]   show/set room temperature 0.0..2.0; reset clears
  /compact           distill this room to a vault note (history retained)
  /agent [task]      hand off recent room context to eos chat (alias: /loop)
  /history           reload + print last 10 turns
  /clear             clear screen (history retained server-side)
  /help              show this
  /exit              quit (or Ctrl-D)

  @<participant-id>  in a group room, route this turn to a specific
                     agent or CLI participant (server-side resolution)[/dim]"""


async def _cmd_rooms(
    session: aiohttp.ClientSession, base_url: str,
) -> list[dict]:
    rooms = await _list_all_rooms(session, base_url)
    if not rooms:
        console.print("[dim](no rooms — create one with /new <name>)[/dim]")
        return rooms
    table = Table(show_header=True, header_style="dim", box=None, pad_edge=False)
    table.add_column("#", width=3, justify="right")
    table.add_column("name")
    table.add_column("id", style="dim")
    table.add_column("kind", style="dim")
    for i, r in enumerate(rooms, 1):
        parts = r.get("participants") or []
        kind = "group" if len(parts) > 2 else "1:1"
        table.add_row(
            str(i), r.get("name", "?"), r.get("id", "?"), kind,
        )
    console.print(table)
    return rooms


async def _cmd_new(
    session: aiohttp.ClientSession, base_url: str, name: str,
) -> dict | None:
    name = (name or "").strip()
    if not name:
        console.print("[red]usage:[/red] /new <name>")
        return None
    payload = {
        "name": name,
        "system_prompt": (
            "You are a helpful EmptyOS assistant. When you want to mutate "
            "state, emit a [DO:app.method({\"arg\":\"value\"})] token inline."
        ),
    }
    async with session.post(f"{base_url}/rooms/api/agents", json=payload) as r:
        data = await r.json()
        if not isinstance(data, dict) or data.get("error"):
            console.print(f"[red]create failed:[/red] {data}")
            return None
        console.print(
            f"[green]✓ created[/green] {data.get('name')} "
            f"[dim]({data.get('id')})[/dim]"
        )
        return data


def _room_model_line(room: dict, *, temp: bool = True) -> str:
    """Dim status line describing a room's think provider/model/temperature."""
    provider = room.get("provider") or "(default)"
    model = room.get("model") or "(provider default)"
    line = f"[dim]provider: {provider}  model: {model}"
    if temp:
        t = room.get("temperature")
        line += f"  temperature: {t if t is not None else '(default)'}"
    return line + "[/dim]"


def _cmd_who(room: dict) -> None:
    console.print(f"[bold]{room.get('name','?')}[/bold] [dim]({room.get('id','?')})[/dim]")
    parts = _participant_names(room)
    if parts:
        for p in parts:
            console.print(f"  · {p}")
    else:
        console.print("  [dim](1:1 — just you and the agent)[/dim]")
    actions = room.get("server_actions") or {}
    if actions:
        verbs = sum(len(v) for v in actions.values())
        console.print(
            f"[dim]server_actions: {verbs} verbs across {len(actions)} apps[/dim]"
        )
    console.print(_room_model_line(room))


async def _update_room_fields(
    session: aiohttp.ClientSession,
    base_url: str,
    room: dict,
    fields: dict,
) -> dict | None:
    async with session.put(
        f"{base_url}/rooms/api/agents/{quote(room['id'], safe='')}",
        json=fields,
    ) as r:
        data = await r.json()
    if not isinstance(data, dict) or data.get("error"):
        console.print(f"[red]update failed:[/red] {data}")
        return None
    return data


async def _think_provider_names(
    session: aiohttp.ClientSession, base_url: str,
) -> set[str]:
    async with session.get(f"{base_url}/api/capabilities") as r:
        if r.status != 200:
            return set()
        data = await r.json()
    providers = data.get("think") if isinstance(data, dict) else []
    names = {
        p.get("name")
        for p in providers
        if isinstance(p, dict) and isinstance(p.get("name"), str)
    }
    return {n for n in names if n}


async def _cmd_model(
    session: aiohttp.ClientSession,
    base_url: str,
    room: dict,
    arg: str,
) -> dict:
    arg = (arg or "").strip()
    if not arg:
        console.print(_room_model_line(room, temp=False))
        return room
    if arg.lower() in ("reset", "clear", "default"):
        updated = await _update_room_fields(session, base_url, room, {"provider": ""})
        if updated:
            console.print("[green]provider reset[/green] [dim](using default think chain)[/dim]")
            return updated
        return room

    names = await _think_provider_names(session, base_url)
    if arg not in names:
        hint = ", ".join(sorted(names)) if names else "no think providers advertised"
        console.print(f"[red]provider not available:[/red] {arg!r} [dim]({hint})[/dim]")
        return room
    updated = await _update_room_fields(session, base_url, room, {"provider": arg})
    if updated:
        console.print(f"[green]provider ->[/green] [cyan]{arg}[/cyan]")
        return updated
    return room


def _parse_temperature(value: str) -> float | None:
    temp = float(value)
    if temp < 0.0 or temp > 2.0:
        raise ValueError("temperature must be between 0.0 and 2.0")
    return temp


async def _cmd_temperature(
    session: aiohttp.ClientSession,
    base_url: str,
    room: dict,
    arg: str,
) -> dict:
    arg = (arg or "").strip()
    if not arg:
        console.print(_room_model_line(room))
        return room
    if arg.lower() in ("reset", "clear", "default"):
        updated = await _update_room_fields(session, base_url, room, {"temperature": None})
        if updated:
            console.print("[green]temperature reset[/green] [dim](provider default)[/dim]")
            return updated
        return room
    try:
        temp = _parse_temperature(arg)
    except (TypeError, ValueError) as e:
        console.print(f"[red]invalid temperature:[/red] {e}")
        return room
    updated = await _update_room_fields(session, base_url, room, {"temperature": temp})
    if updated:
        console.print(f"[green]temperature ->[/green] [cyan]{temp:g}[/cyan]")
        return updated
    return room


async def _cmd_compact(
    session: aiohttp.ClientSession,
    base_url: str,
    room: dict,
) -> None:
    async with session.post(
        f"{base_url}/rooms/api/rooms/{quote(room['id'], safe='')}/distill"
    ) as r:
        data = await r.json()
    if not isinstance(data, dict) or data.get("error"):
        console.print(f"[red]compact failed:[/red] {data}")
        return
    console.print(
        f"[green]distilled[/green] [dim]{data.get('message_count', 0)} messages -> "
        f"{data.get('path', '(no path)')}[/dim]"
    )


def _build_agent_handoff_prompt(
    room: dict,
    history: list,
    next_task: str = "",
    max_messages: int = 14,
) -> str:
    recent = history[-max_messages:] if history else []
    lines = []
    for msg in recent:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "?")
        actor = msg.get("actor") or {}
        speaker = actor.get("id") or role
        text = (msg.get("text") or msg.get("content") or "").strip()
        if text:
            lines.append(f"{speaker}: {text}")
    transcript = "\n".join(lines) or "(no recent transcript)"
    task = (next_task or "").strip() or "Continue from this room handoff and help with the next coding step."
    return (
        "You are taking over from an `eos rooms --code` session. "
        "The rooms backend remains separate; use this as context only.\n\n"
        f"Room: {room.get('name') or room.get('id')} ({room.get('id')})\n"
        f"Requested next task: {task}\n\n"
        "Recent transcript:\n"
        f"{transcript}\n\n"
        "Proceed in the autonomous `eos chat` tool loop. Re-read files before editing."
    )


async def _cmd_agent(
    session: aiohttp.ClientSession,
    base_url: str,
    room: dict,
    next_task: str,
) -> int:
    history = await _load_history(session, base_url, room["id"])
    prompt = _build_agent_handoff_prompt(room, history, next_task)
    payload = {
        "name": f"Handoff from {room.get('name') or room['id']}",
        "initial_user_message": prompt,
    }
    async with session.post(f"{base_url}/agent/api/sessions", json=payload) as r:
        data = await r.json()
    if not isinstance(data, dict) or data.get("error") or not data.get("id"):
        console.print(f"[red]handoff failed:[/red] {data}")
        return 1
    sid = data["id"]
    console.print(f"[green]handoff ->[/green] [cyan]eos chat {sid}[/cyan]")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "emptyos",
        "chat",
        sid,
    )
    return await proc.wait()


# ─────────────────────────── REPL ────────────────────────────────────

def _prompt_for(room: dict) -> str:
    """Render the prompt string. Use a short room tag so the user always
    sees which room is current."""
    tag = room.get("name") or room.get("id", "?")
    # Trim long names; the cursor needs to land somewhere readable.
    if len(tag) > 24:
        tag = tag[:22] + "…"
    return f"[bold cyan]{tag} > [/bold cyan]"


async def repl(
    base_url: str, agent_id: str | None, once: str | None,
    auth_token: str = "",
) -> int:
    if not _probe_daemon(base_url):
        console.print(f"[red]Daemon not reachable at {base_url}.[/red]")
        console.print(
            "[dim]Start it with restart.bat (Windows) or "
            "`python -m emptyos start`.[/dim]"
        )
        return 1

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        # Resolve initial room: explicit --agent wins, then last-used state,
        # then cli-chat (auto-created).
        state = _load_state()
        target_id = agent_id or state.get("last_room_id") or DEFAULT_AGENT_ID

        try:
            async with session.get(
                f"{base_url}/rooms/api/agents/{target_id}"
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, dict) and data.get("id"):
                        room = data
                    else:
                        room = await _ensure_default_agent(session, base_url)
                elif r.status == 401:
                    console.print(
                        "[red]Daemon rejected the auth token.[/red] Check "
                        "`auth_token` in emptyos.toml matches the running daemon."
                    )
                    return 1
                else:
                    room = await _ensure_default_agent(session, base_url)
        except (RuntimeError, aiohttp.ClientError) as e:
            console.print(f"[red]{e}[/red]")
            return 1

        # Persist immediately so a crashed first turn still remembers the room.
        state["last_room_id"] = room["id"]
        _save_state(state)

        if once is not None:
            await _do_turn(session, base_url, room, once)
            return 0

        console.print(Panel(
            f"[bold]eos rooms[/bold] · room=[cyan]{room.get('name', room['id'])}[/cyan] "
            f"[dim]({room['id']})[/dim] · {base_url}\n"
            "[dim]/help for commands, /rooms to switch, /exit to quit. "
            "For autonomous tool-loop work use [bold]eos chat[/bold].[/dim]",
            border_style="dim", expand=False,
        ))

        while True:
            try:
                line = await _ask_choice(_prompt_for(room))
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye.[/dim]")
                return 0
            line = (line or "").strip()
            if not line:
                continue

            if line.startswith("/"):
                parts = line.split(maxsplit=1)
                head = parts[0][1:].lower()
                rest = parts[1] if len(parts) > 1 else ""

                if head in ("exit", "quit", "q"):
                    console.print("[dim]bye.[/dim]")
                    return 0

                if head == "clear":
                    console.clear()
                    continue

                if head == "help":
                    console.print(HELP_TEXT)
                    continue

                if head == "who":
                    _cmd_who(room)
                    continue

                if head == "model":
                    room = await _cmd_model(session, base_url, room, rest)
                    state["last_room_id"] = room["id"]
                    _save_state(state)
                    continue

                if head == "temperature":
                    room = await _cmd_temperature(session, base_url, room, rest)
                    state["last_room_id"] = room["id"]
                    _save_state(state)
                    continue

                if head == "compact":
                    await _cmd_compact(session, base_url, room)
                    continue

                if head in ("agent", "loop"):
                    return await _cmd_agent(session, base_url, room, rest)

                if head == "rooms":
                    await _cmd_rooms(session, base_url)
                    continue

                if head == "switch":
                    if not rest:
                        console.print("[red]usage:[/red] /switch <id|num|name>")
                        continue
                    rooms = await _list_all_rooms(session, base_url)
                    target = _resolve_room(rooms, rest)
                    if not target:
                        console.print(
                            f"[red]no room matches[/red] {rest!r} "
                            "[dim](try /rooms to see all)[/dim]"
                        )
                        continue
                    room = target
                    state["last_room_id"] = room["id"]
                    _save_state(state)
                    console.print(
                        f"[green]→ switched to[/green] "
                        f"[cyan]{room.get('name', room['id'])}[/cyan] "
                        f"[dim]({room['id']})[/dim]"
                    )
                    continue

                if head == "new":
                    created = await _cmd_new(session, base_url, rest)
                    if created:
                        room = created
                        state["last_room_id"] = room["id"]
                        _save_state(state)
                    continue

                if head == "history":
                    hist = await _load_history(session, base_url, room["id"])
                    if not hist:
                        console.print("[dim](no history)[/dim]")
                    for msg in hist[-10:]:
                        role = msg.get("role", "?")
                        style = "cyan" if role == "user" else "green"
                        body = (msg.get("text") or "").replace("\n", " ")[:300]
                        actor = msg.get("actor") or {}
                        actor_tag = (
                            f"({actor.get('id')})" if actor.get("id") else ""
                        )
                        console.print(
                            f"[{style}]{role}{actor_tag}:[/{style}] {body}"
                        )
                    continue

                console.print(f"[red]unknown command:[/red] {parts[0]}")
                continue

            try:
                await _do_turn(session, base_url, room, line)
            except KeyboardInterrupt:
                console.print(
                    "\n[dim](interrupted — server may finish in background)[/dim]"
                )
                continue


# ─────────────────────────── Typer entrypoint ────────────────────────

def chat_command(
    agent: str = typer.Option(
        None, "--agent",
        help="Agent / room id to chat with (overrides last-used).",
    ),
    once: str = typer.Option(
        None, "--once",
        help="Send one message, stream the reply, exit. For scripting.",
    ),
    code: bool = typer.Option(
        False,
        "--code",
        help="Code preset: cli-code agent, repo.* allowlist, force-gated Apply/Reject diffs.",
    ),
):
    """Stream a conversation against any EmptyOS room in the terminal.

    Picks up the last-used room by default. Use /rooms + /switch inside the
    REPL to move between rooms, /new to create one, /who to inspect the
    current one. @-mention routing for group rooms is server-side: just
    type `@agent-id ...` and the responder gets dispatched accordingly.

    Use ``--code`` for the cli-code preset (repo.* allowlist plus force-gated
    Apply/Reject diffs). Sibling: `eos chat` / `eos code` (provided by
    apps/agent) is the autonomous tool-loop REPL for multi-iteration work.
    """
    from emptyos.cli.main import _daemon_url, _find_config

    base = _daemon_url()
    auth = ""
    try:
        from emptyos.kernel.config import Config
        c = Config(_find_config())
        client_host = "127.0.0.1" if c.host in ("0.0.0.0", "::") else c.host
        if not base:
            base = f"http://{client_host}:{c.port}"
        auth = c.auth_token or ""
    except Exception:
        if not base:
            base = "http://127.0.0.1:9000"

    try:
        if code:
            # Lazy import avoids a module cycle: code.py reuses this module's
            # REPL and rendering helpers.
            from emptyos.cli.code import _bootstrap_then_repl

            exit_code = asyncio.run(
                _bootstrap_then_repl(base, agent, once, auth_token=auth)
            )
        else:
            exit_code = asyncio.run(repl(base, agent, once, auth_token=auth))
    except KeyboardInterrupt:
        exit_code = 0
    raise typer.Exit(exit_code)
