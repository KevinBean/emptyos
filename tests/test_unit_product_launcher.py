"""Unit tests for the shared desktop-product launcher (products/_shared/).

Offline — no daemon, no PyInstaller, no subprocess spawn. Covers the pieces that
have real logic and would otherwise only be exercised by a 120 MB build:
product.toml parsing, first-run config, port selection, the daemon child's argv
and env, and the supervisor's restart decision.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "products"))

from _shared import launcher_core as lc  # noqa: E402
from _shared.product_config import ProductConfig, parse_product  # noqa: E402

PRODUCT = ProductConfig(
    id="test-product",
    display_name="Test Product",
    tier="standard",
    exe_name="TestProduct",
    appdata_name="TestProduct",
)


# ── product.toml ─────────────────────────────────────────────────────────────
class TestParseProduct:
    def test_minimal(self):
        p = parse_product({"product": {"id": "p1", "tier": "core"}})
        assert p.id == "p1"
        assert p.tier == "core"
        assert p.display_name == "p1"       # falls back to id
        assert p.exe_name == "p1"           # falls back to display_name
        assert p.start_url == "/hub/"
        assert p.welcome_url == ""          # no wizard until a product declares one
        assert p.update_feed == ""          # update check off by default

    def test_full(self):
        p = parse_product({
            "product": {
                "id": "emptyos-desktop", "display_name": "EmptyOS Desktop",
                "exe_name": "EmptyOS", "tier": "standard", "start_url": "/journal/",
                "welcome_url": "/welcome?first=1", "appdata_name": "EmptyOS",
                "brand_dir": "brand/emptyos", "window": {"width": 1200, "height": 800},
            },
            "update": {"feed": "https://example.test/latest.json"},
        })
        assert p.exe_name == "EmptyOS"
        assert p.start_url == "/journal/"
        assert p.welcome_url == "/welcome?first=1"
        assert p.update_feed == "https://example.test/latest.json"
        assert (p.window_width, p.window_height) == (1200, 800)

    @pytest.mark.parametrize("data", [
        {"product": {"tier": "core"}},                 # no id
        {"product": {"id": "p1"}},                     # no tier
        {},                                            # no [product]
    ])
    def test_required_fields(self, data):
        with pytest.raises(ValueError):
            parse_product(data)

    def test_real_product_toml_parses(self):
        """The shipped declaration must stay loadable — the spec and the launcher
        both die at boot if it doesn't."""
        root = Path(__file__).resolve().parents[1]
        from _shared.product_config import load_product

        p = load_product(root / "products" / "desktop-windows" / "product.toml")
        assert p.id == "emptyos-desktop"
        assert p.exe_name == "EmptyOS"
        # The tier must exist in release.toml, or package-release.py can't build it.
        import tomllib

        with open(root / "release.toml", "rb") as f:
            assert p.tier in tomllib.load(f)["tiers"]


# ── first-run config ─────────────────────────────────────────────────────────
class TestEnsureConfig:
    def test_creates_config_vault_and_data(self, tmp_path):
        cfg = tmp_path / "appdata" / "emptyos.toml"
        vault = tmp_path / "Vault"

        created = lc.ensure_config(cfg, vault=vault, port=9003, display_name="Test Product")

        assert created is True
        assert cfg.exists()
        assert vault.is_dir()
        assert (cfg.parent / "data").is_dir()
        body = cfg.read_text(encoding="utf-8")
        assert vault.as_posix() in body
        assert "port = 9003" in body
        assert 'mode = "local"' in body          # a desktop app is single-user, no auth
        assert '"human"' in body                 # usable with no model configured

    def test_never_overwrites(self, tmp_path):
        cfg = tmp_path / "emptyos.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("# hand-edited\n", encoding="utf-8")

        created = lc.ensure_config(cfg, vault=tmp_path / "v", port=9000,
                                   display_name="Test Product")

        assert created is False                  # not a first run
        assert cfg.read_text(encoding="utf-8") == "# hand-edited\n"

    def test_read_port_roundtrip(self, tmp_path):
        cfg = tmp_path / "emptyos.toml"
        lc.ensure_config(cfg, vault=tmp_path / "v", port=9007, display_name="T")
        assert lc.read_port(cfg) == 9007

    def test_read_port_survives_a_corrupt_config(self, tmp_path):
        cfg = tmp_path / "emptyos.toml"
        cfg.write_text("this is not toml [[[", encoding="utf-8")
        assert lc.read_port(cfg, default=9000) == 9000


# ── port selection ───────────────────────────────────────────────────────────
class TestFreePort:
    def test_skips_a_taken_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
            taken.bind(("127.0.0.1", 0))
            port = taken.getsockname()[1]
            taken.listen(1)

            picked = lc.free_port(start=port, count=10)

            assert picked != port
            assert port < picked <= port + 10

    def test_prefers_the_start_port_when_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        assert lc.free_port(start=free, count=5) == free


# ── daemon child ─────────────────────────────────────────────────────────────
class TestDaemonChild:
    def test_argv_from_source_carries_the_entry_script(self):
        argv = lc.daemon_argv("products/desktop-windows/launcher.py")
        assert argv[0] == sys.executable
        assert argv[1] == "products/desktop-windows/launcher.py"
        assert argv[-1] == "--daemon"

    def test_env_marks_the_product_and_pins_the_config(self, tmp_path):
        cfg = tmp_path / "emptyos.toml"
        env = lc.daemon_env(PRODUCT, cfg)

        assert env["EOS_CONFIG"] == str(cfg)
        # EOS_PRODUCT is the dark-flag for product-only daemon surfaces; a dev
        # daemon never sets it, so it never grows them.
        assert env["EOS_PRODUCT"] == "test-product"
        assert env["EOS_INSTALL_ROOT"]
        assert env["PYTHONIOENCODING"] == "utf-8"

    def test_env_points_app_paths_at_the_bundle_not_the_config(self, tmp_path):
        """apps/ lives inside the versioned app dir, which an update replaces —
        so it must be passed as an env override, never written into the user's
        config."""
        env = lc.daemon_env(PRODUCT, tmp_path / "emptyos.toml")
        root = lc.app_root()

        assert env["EOS_APPS_PATH"] == str(root / "apps")
        assert Path(env["EOS_APPS_PATH"]).is_dir()
        assert env["EOS_PLUGINS_PATH"] == str(root / "plugins")


class TestStdinWatch:
    """The daemon's shutdown signal must be an explicit message, never EOF.

    The first version blocked on `stdin.read()` and treated EOF as "the supervisor
    said stop". EOF is the normal state of a process with no stdin, so it fired
    instantly and raised KeyboardInterrupt inside `import emptyos.cli.main` —
    the frozen daemon died at boot, every time, with no error anyone could read.
    """

    def _watch(self, stdin, monkeypatch):
        """Run _stdin_watch with a fake stdin; report whether it interrupted."""
        interrupted = []
        monkeypatch.setattr(lc.sys, "stdin", stdin)
        import _thread

        monkeypatch.setattr(_thread, "interrupt_main", lambda: interrupted.append(True))
        lc._stdin_watch()
        return bool(interrupted)

    def test_eof_does_not_stop_the_daemon(self, monkeypatch):
        import io
        import types

        stdin = types.SimpleNamespace(buffer=io.BytesIO(b""))  # immediate EOF
        assert self._watch(stdin, monkeypatch) is False

    def test_no_stdin_at_all_does_not_stop_the_daemon(self, monkeypatch):
        # A windowed build has sys.stdin = None.
        assert self._watch(None, monkeypatch) is False

    def test_explicit_stop_message_stops_the_daemon(self, monkeypatch):
        import io
        import types

        stdin = types.SimpleNamespace(buffer=io.BytesIO(lc.STOP_MESSAGE + b"\n"))
        assert self._watch(stdin, monkeypatch) is True

    def test_unrelated_input_is_ignored_until_stop(self, monkeypatch):
        import io
        import types

        stdin = types.SimpleNamespace(
            buffer=io.BytesIO(b"hello\nworld\n" + lc.STOP_MESSAGE + b"\n")
        )
        assert self._watch(stdin, monkeypatch) is True


# ── supervisor ───────────────────────────────────────────────────────────────
class TestSupervisor:
    def _sup(self, tmp_path, monkeypatch, product=PRODUCT):
        monkeypatch.setattr(lc, "user_data_dir", lambda name: tmp_path / name)
        return lc.Supervisor(product, open_window=False)

    def test_prepare_first_run_then_not(self, tmp_path, monkeypatch):
        sup = self._sup(tmp_path, monkeypatch)
        sup.prepare()
        assert sup.first_run is True
        assert sup.config_path.exists()
        assert sup.log_path.exists()          # a windowed build's only diagnosis

        again = self._sup(tmp_path, monkeypatch)
        again.prepare()
        assert again.first_run is False
        assert again.port == sup.port         # the pinned port is respected

    def test_startup_url_never_opens_a_route_the_product_lacks(self, tmp_path, monkeypatch):
        sup = self._sup(tmp_path, monkeypatch)
        sup.prepare()
        assert sup.first_run is True
        # welcome_url is empty (Phase 1) → a first run still lands on start_url
        assert sup.startup_url() == f"{sup.url}/hub/"

    def test_startup_url_uses_the_wizard_on_a_first_run_when_declared(self, tmp_path, monkeypatch):
        from dataclasses import replace

        sup = self._sup(tmp_path, monkeypatch,
                        product=replace(PRODUCT, welcome_url="/welcome?first=1"))
        sup.prepare()
        assert sup.startup_url() == f"{sup.url}/welcome?first=1"
        sup.first_run = False
        assert sup.startup_url() == f"{sup.url}/hub/"

    @pytest.mark.parametrize("code,expected", [
        (lc.RESTART_EXIT_CODE, "restart"),   # the wizard / updater asking for a respawn
        (0, "exit"),
        (1, "exit"),
    ])
    def test_next_action_on_exit_code(self, tmp_path, monkeypatch, code, expected):
        sup = self._sup(tmp_path, monkeypatch)
        assert sup.next_action(code) == expected

    def test_tray_quit_wins_over_the_exit_code(self, tmp_path, monkeypatch):
        sup = self._sup(tmp_path, monkeypatch)
        sup._quit = True
        assert sup.next_action(lc.RESTART_EXIT_CODE) == "quit"

    def test_tray_restart_respawns_a_cleanly_exited_daemon(self, tmp_path, monkeypatch):
        sup = self._sup(tmp_path, monkeypatch)
        sup._restart_requested = True
        assert sup.next_action(0) == "restart"
