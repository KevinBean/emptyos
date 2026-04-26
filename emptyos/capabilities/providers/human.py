"""Human providers — the foundation. A human can always fulfill any capability.

In interactive mode (terminal), prompts the user for input.
In daemon/non-interactive mode, raises so the capability falls back
or reports graceful degradation.
"""

from __future__ import annotations

import asyncio
import os
import sys
from functools import partial

from emptyos.capabilities import Provider


def _is_interactive() -> bool:
    """Check if stdin is a real terminal (not daemon, pipe, or subprocess)."""
    if os.environ.get("EOS_DAEMON"):
        return False
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _input_sync(prompt: str) -> str:
    return input(prompt)


async def _async_input(prompt: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_input_sync, prompt))


class HumanThinkProvider(Provider):
    name = "human"

    async def available(self) -> bool:
        return _is_interactive()

    async def execute(self, *, prompt: str, **kwargs) -> str:
        print(f"\n--- EmptyOS needs you to think ---\n{prompt}\n")
        return await _async_input("Your answer: ")


class HumanReadProvider(Provider):
    name = "human"

    async def available(self) -> bool:
        return _is_interactive()

    async def execute(self, *, path: str, **kwargs) -> str:
        print(f"\n--- EmptyOS needs file content ---\nPlease paste content of: {path}")
        print("(enter an empty line when done)\n")
        lines = []
        while True:
            line = await _async_input("")
            if line == "":
                break
            lines.append(line)
        return "\n".join(lines)


class HumanWriteProvider(Provider):
    name = "human"

    async def available(self) -> bool:
        return _is_interactive()

    async def execute(self, *, path: str, content: str, **kwargs) -> str:
        print(f"\n--- EmptyOS needs you to save a file ---")
        print(f"Path: {path}")
        print(f"Content:\n{content[:500]}{'...' if len(content) > 500 else ''}\n")
        await _async_input("Press Enter when you've saved it: ")
        return path


class HumanSearchProvider(Provider):
    name = "human"

    async def available(self) -> bool:
        return _is_interactive()

    async def execute(self, *, query: str, **kwargs) -> list[str]:
        print(f"\n--- EmptyOS needs you to search ---\nQuery: {query}")
        print("Type matching file paths or info, one per line (empty line to finish):\n")
        results = []
        while True:
            line = await _async_input("")
            if line == "":
                break
            results.append(line)
        return results


class HumanSeeProvider(Provider):
    name = "human"

    async def available(self) -> bool:
        return _is_interactive()

    async def execute(self, *, mode: str = "snapshot", **kwargs) -> str:
        print(f"\n--- EmptyOS needs an image ({mode}) ---")
        return (await _async_input("Paste path to an image file: ")).strip()
