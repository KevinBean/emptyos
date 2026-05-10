"""Add a sample figure to the demo PDR, reference it from the architecture section, and re-export.

Exercises the `![[filename.svg|caption]]` auto-numbered figure pipeline end-to-end.

Run: python scripts/seed_reports_figure.py
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
import urllib.request as ur
import uuid
from pathlib import Path


def _resolve_base() -> str:
    env = os.environ.get("EMPTYOS_BASE")
    if env:
        return env.rstrip("/")
    try:
        cfg = tomllib.loads(
            (Path(__file__).resolve().parent.parent / "emptyos.toml").read_text(encoding="utf-8")
        )
        net = cfg.get("network", {})
        host = net.get("host", "127.0.0.1")
        if host in ("0.0.0.0", ""):
            host = "127.0.0.1"
        port = net.get("port", 9000)
        return f"http://{host}:{port}"
    except Exception:
        return "http://127.0.0.1:9000"


BASE = _resolve_base()
REPORT_ID = "demo-offshore-export-cable-route-pdr"
FIGURE_NAME = "cable-cross-section.svg"


SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 260" width="420" height="260">
  <style>
    .label { font: 11px -apple-system, "Segoe UI", sans-serif; fill: #1b1e24; }
    .muted { font: 9.5px monospace; fill: #5b6370; }
    .axis  { stroke: #b0b6c0; stroke-width: 0.8; fill: none; }
    .armor { stroke: #2a2f36; stroke-width: 2; fill: #dfe3e9; }
    .xlpe  { stroke: #1b1e24; stroke-width: 0.8; fill: #f2f4f7; }
    .cond  { stroke: #1b1e24; stroke-width: 0.8; fill: #c49547; }
    .sheath { stroke: #1b1e24; stroke-width: 0.8; fill: #9aa2ad; }
    .fibre { stroke: #1b1e24; stroke-width: 0.8; fill: #1f5ca8; }
  </style>

  <!-- Outer armor -->
  <circle class="armor" cx="130" cy="130" r="95"/>

  <!-- Three cores arranged at 120 degrees -->
  <!-- Core 1 -->
  <g transform="translate(130,130)">
    <circle class="xlpe"   cx="0"  cy="-45" r="34"/>
    <circle class="sheath" cx="0"  cy="-45" r="28"/>
    <circle class="cond"   cx="0"  cy="-45" r="18"/>
  </g>
  <!-- Core 2 -->
  <g transform="translate(130,130)">
    <circle class="xlpe"   cx="39" cy="22" r="34"/>
    <circle class="sheath" cx="39" cy="22" r="28"/>
    <circle class="cond"   cx="39" cy="22" r="18"/>
  </g>
  <!-- Core 3 -->
  <g transform="translate(130,130)">
    <circle class="xlpe"   cx="-39" cy="22" r="34"/>
    <circle class="sheath" cx="-39" cy="22" r="28"/>
    <circle class="cond"   cx="-39" cy="22" r="18"/>
  </g>

  <!-- Fibre in interstice -->
  <circle class="fibre" cx="130" cy="130" r="7"/>

  <!-- Leader lines and labels -->
  <g class="axis">
    <line x1="235" y1="60"  x2="290" y2="60"/>
    <line x1="235" y1="130" x2="290" y2="130"/>
    <line x1="235" y1="200" x2="290" y2="200"/>
    <line x1="175" y1="170" x2="210" y2="170"/>
    <line x1="140" y1="138" x2="162" y2="158"/>
  </g>

  <text class="label" x="295" y="64">630 mm&#178; Cu conductor</text>
  <text class="label" x="295" y="134">XLPE insulation + sheath</text>
  <text class="label" x="295" y="204">Armor wires (galvanised steel)</text>
  <text class="label" x="215" y="173">Filler</text>
  <text class="label" x="165" y="160">Integrated fibre</text>

  <!-- Title -->
  <text class="label" x="10" y="18" style="font-weight:700; font-size:12px">132 kV three-core XLPE cable — cross section</text>
  <text class="muted" x="10" y="245">Schematic only; not to scale.</text>
</svg>
"""


def req_json(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = ur.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with ur.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read().decode())


def upload_figure(report_id: str, filename: str, content: bytes, content_type: str) -> dict:
    """Send a multipart/form-data POST (uses only stdlib)."""
    boundary = "----seed-" + uuid.uuid4().hex
    body_parts = [
        f"--{boundary}",
        f'Content-Disposition: form-data; name="file"; filename="{filename}"',
        f"Content-Type: {content_type}",
        "",
    ]
    prefix = ("\r\n".join(body_parts) + "\r\n").encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    payload = prefix + content + suffix
    r = ur.Request(
        f"{BASE}/reports/api/reports/{report_id}/figures",
        data=payload,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with ur.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


ARCHITECTURE_BODY = """### Cable construction
Three-core XLPE-insulated cable with integrated fibre-optic element. Conductor cross-section 630 mm2 stranded copper, sized against continuous current rating with derating for burial depth and soil thermal resistivity. The construction is shown in the figure below.

![[cable-cross-section.svg|Three-core 132 kV submarine cable cross-section]]

### Route corridor
The corridor follows the marine spatial plan designated cable window, minimising conflict with fishing grounds and shipping lanes. Deviations are documented in the route engineering report.

### Burial strategy
| Soil class | Burial method | Target depth |
|---|---|---|
| Soft sand / silt | Jet trencher | 1.5 m |
| Dense sand / clay | Mechanical plough | 1.0 m |
| Exposed bedrock | Rock placement + cable protection system | 0.5 m cover |

### Protection
Exposed segments over bedrock receive an articulated half-shell cable protection system, locally rock-placed. Landfall transition uses horizontal directional drilling under the intertidal zone.
"""


def main() -> int:
    # --- 1. Upload the SVG as a figure ---
    up = upload_figure(REPORT_ID, FIGURE_NAME, SVG.encode("utf-8"), "image/svg+xml")
    if not up.get("ok"):
        print(f"Upload failed: {up}")
        return 1
    print(f"Uploaded figure: {up['name']}")

    # --- 2. Rewrite the architecture section to include the figure ---
    put = req_json(
        "PUT",
        f"/reports/api/reports/{REPORT_ID}/sections/architecture",
        {"body": ARCHITECTURE_BODY, "meta": {"status": "ready"}},
    )
    print(f"Architecture section updated: {put.get('ok')}")

    # --- 3. Preview has the figure ---
    with ur.urlopen(f"{BASE}/reports/api/reports/{REPORT_ID}/preview", timeout=15) as r:
        html = r.read().decode()
    has_figure_tag = "<figure" in html and "Figure 1" in html
    has_img = "cable-cross-section.svg" in html
    print(f"Preview has <figure>: {has_figure_tag} | includes SVG reference: {has_img}")

    # --- 4. Re-export PDF + DOCX ---
    pdf = req_json("POST", f"/reports/api/reports/{REPORT_ID}/export/pdf")
    if pdf.get("ok"):
        print(f"PDF re-exported: {pdf['file']} ({pdf['size']:,} bytes)")
    else:
        print(f"PDF export: {pdf}")

    docx = req_json("POST", f"/reports/api/reports/{REPORT_ID}/export/docx")
    if docx.get("ok"):
        print(f"DOCX re-exported: {docx['file']} ({docx['size']:,} bytes)")
    else:
        print(f"DOCX export: {docx}")

    print()
    print(f"Inspect:  {BASE}/reports/#{REPORT_ID}")
    print(f"Preview:  {BASE}/reports/api/reports/{REPORT_ID}/preview")
    return 0


if __name__ == "__main__":
    sys.exit(main())
