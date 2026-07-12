"""Eyeball harness: load a viz scene.html, capture console errors + motion proof.

Usage: python eyeball_scene.py <path-to-scene.html> <out-prefix>
Writes <out-prefix>-t0.png / -t2.png / -t4.png and prints a JSON verdict:
console errors, whether pixels changed between frames (motion), and
whether the animejs adapter registered (window probe).
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

scene = Path(sys.argv[1]).resolve()
prefix = sys.argv[2]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    errors, warnings = [], []
    page.on("console", lambda m: (errors if m.type == "error" else warnings).append(m.text)
            if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(scene.as_uri())
    page.wait_for_timeout(3000)  # let CDN modules load + scene boot
    shots = []
    for i, name in enumerate(("t0", "t2", "t4")):
        page.screenshot(path=f"{prefix}-{name}.png")
        shots.append(f"{prefix}-{name}.png")
        if i < 2:
            page.wait_for_timeout(2000)
    probe = page.evaluate(
        """() => ({
            hasCanvas: !!document.querySelector('canvas'),
            animations: document.getAnimations().length,
            hasImportmap: !!document.querySelector('script[type=importmap]'),
            importmap: (document.querySelector('script[type=importmap]')||{}).textContent || '',
        })"""
    )
    browser.close()

# pixel-diff the frames for motion
try:
    from PIL import Image, ImageChops
    a, b = Image.open(shots[0]), Image.open(shots[2])
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    bbox = diff.getbbox()
    hist = diff.histogram()
    changed = sum(hist[16:256]) + sum(hist[256 + 16:512]) + sum(hist[512 + 16:768])
    motion = {"bbox": bbox, "changed_px_channels": changed}
except Exception as e:  # Pillow absent — bbox check skipped
    motion = {"error": str(e)}

print(json.dumps({
    "console_errors": errors[:10],
    "n_errors": len(errors),
    "probe": {k: (v[:400] if isinstance(v, str) else v) for k, v in probe.items()},
    "motion": motion,
}, indent=2))
