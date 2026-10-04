"""Windows screen + context capture primitives (sibling of ``capture_mac.py``).

All PowerShell, no new deps — scripts are passed via ``-EncodedCommand`` (base64
UTF-16LE) so quoting/backslashes never bite:

    grab_screen      System.Drawing over the VirtualScreen bounds
    frontmost_app    user32 GetForegroundWindow → process name  (one P/Invoke)
    window_title     user32 GetWindowText of the foreground window
    browser_context  degrade: URL empty (no AppleScript on Windows) — the Chrome
                     history reader supplies URLs; the window title carries the tab
    selected_text    SendKeys ^c + Get-Clipboard, clipboard saved/restored

Every primitive is exception-guarded: a failed/absent PowerShell degrades to an
empty field, never a raised capture. Reached only via the ``capture.py`` facade,
which dispatches by ``sys.platform``.
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Awaitable, Callable, Optional

Runner = Callable[..., Awaitable[tuple[int, str, str]]]

# Chrome-family process names (ProcessName, no .exe) → browser.
_BROWSER_PROCS = {"chrome", "msedge", "brave", "chromium", "vivaldi", "arc", "opera"}


async def _default_run(argv, *, stdin: Optional[bytes] = None, timeout: float = 12.0):
    """Spawn ``argv``, return ``(rc, stdout, stderr)``; ``(-1, "", <err>)`` on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(input=stdin), timeout)
        return (
            proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )
    except Exception as e:
        return -1, "", str(e)


def _ps(script: str) -> list[str]:
    """PowerShell argv using -EncodedCommand (dodges all quoting/backslash hell)."""
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc]


# ── scripts ───────────────────────────────────────────────────────────────────
_FOREGROUND_PS = r"""
$ErrorActionPreference='SilentlyContinue'
Add-Type @"
using System;using System.Runtime.InteropServices;using System.Text;
public class EOSW {
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, out int pid);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
}
"@
$h=[EOSW]::GetForegroundWindow()
$sb=New-Object System.Text.StringBuilder 512
[void][EOSW]::GetWindowText($h,$sb,512)
$p=0
[void][EOSW]::GetWindowThreadProcessId($h,[ref]$p)
$name=''
try { $name=(Get-Process -Id $p -ErrorAction SilentlyContinue).ProcessName } catch {}
Write-Output $name
Write-Output $sb.ToString()
"""

_SCREEN_PS = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$vs=[System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp=New-Object System.Drawing.Bitmap($vs.Width,$vs.Height)
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($vs.Location,[System.Drawing.Point]::Empty,$vs.Size)
$bmp.Save('__DEST__',[System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose();$bmp.Dispose()
"""

_SELECTION_PS = r"""
$ErrorActionPreference='SilentlyContinue'
$old=''
try { $old=Get-Clipboard -Raw } catch {}
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('^c')
Start-Sleep -Milliseconds __SETTLE__
$new=''
try { $new=Get-Clipboard -Raw } catch {}
Write-Output $new
try { Set-Clipboard -Value $old } catch {}
"""


# ── pure parsers ──────────────────────────────────────────────────────────────
def parse_foreground(out: str) -> tuple[str, str]:
    """``"chrome\\nDocs - Google Chrome"`` → ``("chrome", "Docs - Google Chrome")``."""
    lines = (out or "").splitlines()
    name = lines[0].strip() if lines else ""
    title = lines[1].strip() if len(lines) > 1 else ""
    return name, title


def is_browser(proc_name: str) -> str:
    """Return ``"chrome"`` for a browser process name, else ``""``."""
    return "chrome" if (proc_name or "").strip().lower() in _BROWSER_PROCS else ""


# ── async primitives ──────────────────────────────────────────────────────────
async def grab_screen(dest: str | Path, *, run: Runner | None = None) -> bool:
    run = run or _default_run
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    script = _SCREEN_PS.replace("__DEST__", str(dest).replace("'", "''"))
    rc, _out, _err = await run(_ps(script))
    return rc == 0 and dest.exists() and dest.stat().st_size > 0


async def _foreground(run: Runner) -> tuple[str, str]:
    try:
        rc, out, _ = await run(_ps(_FOREGROUND_PS))
        return parse_foreground(out) if rc == 0 else ("", "")
    except Exception:
        return "", ""


async def frontmost_app(*, run: Runner | None = None) -> str:
    return (await _foreground(run or _default_run))[0]


async def window_title(*, run: Runner | None = None) -> str:
    return (await _foreground(run or _default_run))[1]


async def browser_context(app_name: str, *, run: Runner | None = None) -> dict:
    # No reliable live-URL grab on Windows (no AppleScript). The window title
    # already carries "<tab> - Chrome" and the history reader supplies URLs.
    return {"url": "", "title": ""}


async def selected_text(*, run: Runner | None = None, settle: float = 0.3) -> str:
    run = run or _default_run
    ms = max(50, int(settle * 1000))
    try:
        rc, out, _ = await run(_ps(_SELECTION_PS.replace("__SETTLE__", str(ms))))
        return out.strip() if rc == 0 else ""
    except Exception:
        return ""
