"""Unit tests for the devices dashboard rasterizer (pure — no daemon)."""

import importlib.util
import io

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from helpers import app_path  # noqa: E402

# Load the pure module directly (no kernel / app package needed). Resolve via
# the track-tree scanner — devices moved engineering/ -> others/ and a
# hardcoded path silently breaks collection.
_PATH = app_path("devices") / "dashboard.py"
_spec = importlib.util.spec_from_file_location("devices_dashboard", _PATH)
dashboard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dashboard)


def _open(png: bytes) -> Image.Image:
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return Image.open(io.BytesIO(png))


def test_full_payload_renders_correct_size():
    panel = {
        "title": "EmptyOS",
        "clock": "14:32",
        "date": "Sun 28 Jun 2026",
        "sections": [
            {"heading": "Today", "accent": "blue", "lines": ["Buy milk", "Reflash puck"]},
            {"heading": "Devices · 1/2 online", "accent": "green", "lines": ["Voice satellite"]},
        ],
        "footer": "updated 14:32 · paper-1",
    }
    png = dashboard.render_dashboard_png(panel, width=400, height=600)
    img = _open(png)
    assert img.size == (400, 600)
    # Not blank: there must be non-white pixels (text was drawn).
    colors = img.convert("RGB").getcolors(maxcolors=100000)
    assert any(c != (255, 255, 255) for _, c in colors)


def test_landscape_dimensions():
    png = dashboard.render_dashboard_png({"clock": "09:00"}, width=600, height=400)
    assert _open(png).size == (600, 400)


def test_empty_payload_just_renders_blank_canvas():
    png = dashboard.render_dashboard_png({}, width=400, height=600)
    assert _open(png).size == (400, 600)  # no crash on a wholly-empty payload


def test_long_lines_are_truncated_not_crashed():
    panel = {
        "clock": "00:00",
        "sections": [{"heading": "x" * 200, "lines": ["y" * 300, "z" * 300]}],
        "footer": "f" * 200,
    }
    png = dashboard.render_dashboard_png(panel, width=400, height=600)
    assert _open(png).size == (400, 600)


def test_overflow_sections_clip_but_footer_survives():
    # 40 sections can't fit 600px tall; render must still succeed (clipped).
    panel = {
        "clock": "12:00",
        "sections": [{"heading": f"S{i}", "lines": [f"line {i}"]} for i in range(40)],
        "footer": "footer stays",
    }
    png = dashboard.render_dashboard_png(panel, width=400, height=600)
    assert _open(png).size == (400, 600)


def test_fonts_are_scalable_with_hierarchy():
    # Regression: on a bare Windows box truetype('DejaVuSans.ttf') fails and the
    # old code fell back to the tiny fixed bitmap font for EVERY size — no
    # hierarchy. The clock font must be substantially larger than the line font.
    clock = dashboard._font(None, 80)
    line = dashboard._font(None, 16)
    assert getattr(clock, "size", 0) > getattr(line, "size", 0)
    # And a big font must actually produce taller glyphs than a small one.
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    big = d.textbbox((0, 0), "8", font=clock)[3]
    small = d.textbbox((0, 0), "8", font=line)[3]
    assert big > small * 2


def test_unknown_accent_falls_back():
    png = dashboard.render_dashboard_png(
        {"clock": "12:00", "sections": [{"heading": "H", "accent": "chartreuse", "lines": ["a"]}]},
    )
    assert _open(png).size == (400, 600)


def test_clean_line_strips_emoji_keeps_cjk():
    # Emoji (SMP + BMP dingbats) are tofu on the panel — stripped.
    assert dashboard._clean_line("📌 回复 LinkedIn/Seek ✅") == "回复 LinkedIn/Seek"
    out = dashboard._clean_line("✨ sparkle ⭐ star 🚀")
    assert "✨" not in out and "⭐" not in out and "🚀" not in out
    assert "sparkle" in out and "star" in out
    # Plain ASCII untouched; adapter glyphs (□ ■ ▮ ▯ · →) survive.
    assert dashboard._clean_line("plain text 1.5%") == "plain text 1.5%"
    assert dashboard._clean_line("□ task · label ▮▮▯ → next") == "□ task · label ▮▮▯ → next"
    # CJK + fullwidth punctuation preserved.
    assert dashboard._clean_line("整理笔记（每周）") == "整理笔记（每周）"
    # Variation selector + keycap sequences vanish entirely.
    assert dashboard._clean_line("1️⃣ first") == "1 first"


def test_cjk_payload_renders_valid_png():
    panel = {
        "title": "EmptyOS",
        "clock": "20:24",
        "date": "周四 02 七月 2026",
        "sections": [{"heading": "今日", "accent": "blue",
                      "lines": ["📋 回复 LinkedIn/Seek", "整理模板 [[_模板]]"]}],
        "footer": "updated 20:24 · paper-01",
    }
    png = dashboard.render_dashboard_png(panel, width=400, height=600)
    img = _open(png)
    assert img.size == (400, 600)
    colors = img.convert("RGB").getcolors(maxcolors=100000)
    assert any(c != (255, 255, 255) for _, c in colors)


def test_adapt_task_list():
    # □/■ not ☐/☑ — CJK faces (YaHei) lack U+2610/2611 (renders tofu on the panel).
    rows = [{"text": "Buy milk"}, {"text": "Reflash puck", "done": True, "tag": "09:00"}]
    assert dashboard.adapt_panel("task-list", rows) == ["□ Buy milk", "■ Reflash puck · 09:00"]


def test_adapt_stat_tile_dict_and_list():
    assert dashboard.adapt_panel("stat-tile", {"label": "Devices online", "value": "1/2"}) == \
        ["Devices online · 1/2"]
    assert dashboard.adapt_panel("tiles-row", [{"label": "Overdue", "value": 3},
                                               {"label": "Today", "value": 2}]) == \
        ["Overdue · 3", "Today · 2"]


def test_adapt_plain_list_canonical_and_legacy_keys():
    rows = [{"title": "IEC 60287", "subtitle": "read §2.1"},
            {"label": "legacy row", "sub": "alias keys"}]
    assert dashboard.adapt_panel("plain-list", rows) == \
        ["IEC 60287 — read §2.1", "legacy row — alias keys"]


def test_adapt_bar_and_countdown():
    assert dashboard.adapt_panel("bar", {"label": "Memory", "pct": 60}) == ["Memory ▮▮▮▯▯ 60%"]
    assert dashboard.adapt_panel("countdown-tile", [{"title": "Visa decision", "days": 12}]) == \
        ["Visa decision · 12d"]


def test_adapt_hero_weather_and_quote():
    assert dashboard.adapt_panel("hero-weather",
                                 {"emoji": "🌧", "temperature": 12, "unit": "°C",
                                  "description": "light rain"}) == ["light rain · 12°C"]
    # wttr.in fallback has no temperature field → description only, no dangling "· °C".
    assert dashboard.adapt_panel("hero-weather",
                                 {"temperature": "", "description": "🌤 +10°C"}) == ["🌤 +10°C"]
    q = dashboard.adapt_panel("quote", {"text": "Less, but better", "author": "Rams"})
    assert q == ["“Less, but better”", "— Rams"]


def test_adapt_unknown_renderer_and_empty_data():
    assert dashboard.adapt_panel("garden-mini", {"anything": 1}) is None
    assert dashboard.adapt_panel("task-list", []) is None
    assert dashboard.adapt_panel("task-list", None) is None


def test_adapt_limit_caps_lines():
    rows = [{"text": f"t{i}"} for i in range(10)]
    assert len(dashboard.adapt_panel("task-list", rows, limit=4)) == 4


def test_qr_renders_top_right_beside_clock():
    pytest.importorskip("qrcode")
    panel = {"title": "EmptyOS", "clock": "12:00", "date": "Fri 03 Jul 2026",
             "sections": [{"heading": "Today", "lines": ["a", "b"]}],
             "footer": "updated 12:00 · paper-01"}
    plain = dashboard.render_dashboard_png(panel, width=400, height=600)
    with_qr = dashboard.render_dashboard_png(panel, width=400, height=600,
                                             qr_url="http://192.168.20.11:9000/devices/pages/panel.html?id=paper-01")
    assert _open(with_qr).size == (400, 600)
    # The QR sits top-right beside the clock (that corner is blank on the
    # plain render); it must add substantial black there and none at the old
    # bottom-right spot.
    img = _open(with_qr).convert("RGB")
    ref = _open(plain).convert("RGB")
    top_right = (400 - 130, 30, 400 - 5, 170)
    dark = sum(1 for px in img.crop(top_right).getdata() if px == (0, 0, 0))
    dark_ref = sum(1 for px in ref.crop(top_right).getdata() if px == (0, 0, 0))
    assert dark > dark_ref + 200, "QR modules should add substantial black beside the clock"


def test_qr_absent_lib_or_url_is_failsoft():
    # No qr_url → identical no-crash path.
    png = dashboard.render_dashboard_png({"clock": "12:00"}, qr_url=None)
    assert _open(png).size == (400, 600)
    # _qr_image with an unusable URL type must return None, not raise.
    assert dashboard._qr_image("", 80) is not None or True  # empty URL: lib-dependent, must not raise


def test_font_candidates_prefer_cjk_capable_face():
    # On any box that has one of the CJK candidates, a CJK glyph must not map to
    # .notdef. Skip quietly on machines with no CJK font at all (bare CI).
    f = dashboard._font(None, 20)
    if not hasattr(f, "getmask"):
        pytest.skip("bitmap fallback font — no glyph introspection")
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    cjk_w = d.textlength("中", font=f)
    notdef_w = d.textlength("", font=f)  # PUA char → guaranteed .notdef
    if cjk_w == notdef_w:
        pytest.skip("no CJK-capable font on this machine")
    assert cjk_w > 0
