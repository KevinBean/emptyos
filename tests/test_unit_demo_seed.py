"""Unit tests: demo seeding runs for enabled apps, not just started ones.

Regression pin for a silent no-op found 2026-08-09 on the live demo: apps only
reach STARTED via the `apps.autostart` list, and demo.binbian.net sets none, so
`self.apps.running` was empty when `_demo_seed_apps` iterated it. Every seeded
app still looked healthy afterwards — apps lazy-load on their first HTTP
request — so nothing errored and the sample content simply never appeared.

Both directions are pinned: seeding must fire for an enabled-but-not-started
app, and must still skip an app that is disabled.
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest

from emptyos.kernel import Kernel

SEED_SRC = """\
RAN = []


async def seed(app):
    RAN.append(app.marker)
    app.seeded.append(app.marker)
"""


class _Syslog:
    def __init__(self):
        self.lines = []

    def info(self, src, msg):
        self.lines.append(("info", src, msg))

    def warn(self, src, msg):
        self.lines.append(("warn", src, msg))

    error = warn


class _Apps:
    """Minimal stand-in for AppLoader: nothing is STARTED at boot."""

    def __init__(self, manifests, enabled):
        self.manifests = manifests
        self.instances: dict = {}
        self._enabled = enabled
        self.running: list[str] = []          # the empty-autostart reality
        self.loaded: list[str] = []

    def enabled_ids(self):
        return set(self._enabled)

    async def load(self, app_id):
        self.loaded.append(app_id)
        inst = types.SimpleNamespace(marker=app_id, seeded=[])
        self.instances[app_id] = inst
        return inst

    async def start(self, app_id):
        self.running.append(app_id)


def _make_app(tmp_path: Path, app_id: str, with_seed: bool = True):
    d = tmp_path / app_id
    (d / "demo").mkdir(parents=True)
    if with_seed:
        (d / "demo" / "seed.py").write_text(SEED_SRC, encoding="utf-8")
    return types.SimpleNamespace(id=app_id, path=d)


def _run(apps):
    stub = types.SimpleNamespace(apps=apps, syslog=_Syslog())
    asyncio.run(Kernel._demo_seed_apps(stub))
    return stub


def test_seeds_an_enabled_app_that_was_never_started(tmp_path):
    m = _make_app(tmp_path, "seeded-app")
    apps = _Apps({"seeded-app": m}, enabled={"seeded-app"})
    assert apps.running == [], "precondition: nothing is STARTED"

    stub = _run(apps)

    assert "seeded-app" in apps.loaded, "seed must load the app on demand"
    assert apps.instances["seeded-app"].seeded == ["seeded-app"]
    assert any("seeded-app OK" in line[2] for line in stub.syslog.lines)


def test_skips_a_disabled_app(tmp_path):
    m = _make_app(tmp_path, "off-app")
    apps = _Apps({"off-app": m}, enabled=set())

    _run(apps)

    assert apps.loaded == [], "a disabled app must never be loaded to seed it"
    assert apps.instances == {}


def test_skips_an_app_with_no_seed_file(tmp_path):
    m = _make_app(tmp_path, "plain-app", with_seed=False)
    apps = _Apps({"plain-app": m}, enabled={"plain-app"})

    _run(apps)

    assert apps.loaded == []


def test_a_failing_seed_is_isolated(tmp_path):
    good = _make_app(tmp_path, "aaa-good")
    bad = _make_app(tmp_path, "bbb-bad")
    (bad.path / "demo" / "seed.py").write_text(
        "async def seed(app):\n    raise RuntimeError('boom')\n", encoding="utf-8"
    )
    apps = _Apps({"aaa-good": good, "bbb-bad": bad}, enabled={"aaa-good", "bbb-bad"})

    stub = _run(apps)

    assert apps.instances["aaa-good"].seeded == ["aaa-good"]
    assert any("boom" in line[2] for line in stub.syslog.lines)


@pytest.mark.parametrize("already_loaded", [True, False])
def test_does_not_reload_an_already_loaded_app(tmp_path, already_loaded):
    m = _make_app(tmp_path, "warm-app")
    apps = _Apps({"warm-app": m}, enabled={"warm-app"})
    if already_loaded:
        apps.instances["warm-app"] = types.SimpleNamespace(marker="warm-app", seeded=[])

    _run(apps)

    assert apps.loaded == ([] if already_loaded else ["warm-app"])
    assert apps.instances["warm-app"].seeded == ["warm-app"]
