"""Runbook — offline unit tests (``test_unit_`` prefix → runs without daemon).

Covers the pure layers: the eos-block parse/serialize round-trip, the safe
expression evaluator (incl. escape attempts), the run-state sidecar, and the
staleness rule in ``engine.block_statuses`` (driven by a fake app holding a
temp data dir). Block execution + gating run against the live daemon in
``test_sys_runbook.py``.
"""

import importlib.util
import pathlib
import sys
import tempfile
import types

import pytest

_BASE = pathlib.Path(__file__).resolve().parent.parent / "apps/public/standard/runbook"
_PKG = "rbpkg_test"


def _load_pkg():
    if _PKG in sys.modules:
        return sys.modules[_PKG]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_BASE)]
    sys.modules[_PKG] = pkg
    for name in ("shared", "calc", "runs", "blocks", "engine", "routes"):
        spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", _BASE / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{name}"] = mod
        spec.loader.exec_module(mod)
    return pkg


_load_pkg()
shared = sys.modules[f"{_PKG}.shared"]
calc = sys.modules[f"{_PKG}.calc"]
runs = sys.modules[f"{_PKG}.runs"]
blocks = sys.modules[f"{_PKG}.blocks"]
engine = sys.modules[f"{_PKG}.engine"]
routes = sys.modules[f"{_PKG}.routes"]


_SAMPLE = '''```eos-block
id = "txns"
type = "vault_query"
output = "txns"
---
tags = ["expense"]
```

```eos-block
id = "total"
type = "calculate"
output = "total"
inputs = { rows = "txns" }
---
sum(r.amount for r in rows)
```

```eos-block
id = "draft"
type = "write_draft"
output = "draft"
path = "x/y.md"
---
Total {{total}}
```'''


class TestParse:
    def test_parse_shape(self):
        b = shared.parse_blocks(_SAMPLE)
        assert [x.type for x in b] == ["vault_query", "calculate", "write_draft"]
        assert b[1].inputs == {"rows": "txns"}
        # write_draft is auto-marked side-effecting; the others aren't.
        assert b[2].side_effect is True
        assert b[0].side_effect is False

    def test_serialize_is_fixed_point(self):
        s = shared.serialize_blocks(shared.parse_blocks(_SAMPLE))
        assert shared.serialize_blocks(shared.parse_blocks(s)) == s

    def test_invalid_block_fails_soft(self):
        bad = "```eos-block\nid = \"x\"\ntype = \"bogus\"\n---\nbody\n```"
        b = shared.parse_blocks(bad)
        assert b[0].type == "invalid" and b[0].parse_error

    def test_template(self):
        out = shared.render_template("T {{total}} @ {{now.date}}",
                                     {"total": 9.5, "now": {"date": "2026-06-05"}})
        assert out == "T 9.5 @ 2026-06-05"


class TestCalc:
    def test_comprehension_attr_and_subscript(self):
        rows = [{"amount": 3}, {"amount": 4.5}, {"amount": 2}]
        assert calc.safe_eval("sum(r.amount for r in rows)", {"rows": rows}) == 9.5
        assert calc.safe_eval("sorted([r['amount'] for r in rows])", {"rows": rows}) == [2, 3, 4.5]

    @pytest.mark.parametrize("expr", [
        "__import__('os')",
        "(1).__class__",
        "[].__class__.__base__",
        "len.__globals__",
        "lambda x: x",
        "(x := 5)",
        "open('x')",
    ])
    def test_escapes_blocked(self, expr):
        with pytest.raises(calc.CalcError):
            calc.safe_eval(expr, {})


class TestRunStore:
    def test_set_and_cache(self):
        d = pathlib.Path(tempfile.mkdtemp())
        store = runs.RunStore(d)
        store.set_block("rb", "a", status="ok", type="number")
        assert store.block_state("rb", "a")["status"] == "ok"
        store.cache_output("rb", "a", [1, 2, 3])
        assert store.cached_output("rb", "a") == [1, 2, 3]


class TestBlockExecutors:
    @pytest.mark.asyncio
    async def test_notify_targets_runbook_notification_wrapper(self):
        block = shared.parse_blocks(
            '```eos-block\nid="n"\ntype="notify"\npriority="warning"\n---\nHello {{name}}\n```'
        )[0]

        res = await blocks.execute_block(types.SimpleNamespace(), block, {"name": "Ada"})

        assert res == {
            "action": {
                "app": "runbook",
                "method": "send_notification",
                "args": {"text": "Hello Ada", "priority": "warning"},
            },
            "type": "text",
        }

    @pytest.mark.asyncio
    async def test_notify_stamps_scheduled_flag_in_scheduled_mode(self):
        block = shared.parse_blocks(
            '```eos-block\nid="n"\ntype="notify"\n---\nPing\n```'
        )[0]

        res = await blocks.execute_block(
            types.SimpleNamespace(), block, {"mode": "scheduled"}
        )

        assert res["action"]["args"] == {
            "text": "Ping", "priority": "info", "scheduled": True,
        }


class TestNotificationWrapper:
    @pytest.mark.asyncio
    async def test_send_notification_uses_notifications_service(self):
        sent = []

        class Notifier:
            async def send(self, message, priority="info", source="system"):
                sent.append((message, priority, source))

        fake = types.SimpleNamespace(
            service=lambda name: Notifier() if name == "notifications" else None
        )

        res = await engine.send_notification(fake, text="Ping", priority="warning")

        assert res == {"ok": True, "priority": "warning"}
        assert sent == [("Ping", "warning", "runbook")]

    @pytest.mark.asyncio
    async def test_scheduled_routes_via_proactive_gate(self):
        calls = []

        async def proactive_notify(kind, text, **kw):
            calls.append((kind, text, kw))
            return {"delivered": True, "reason": "ok", "channels": ["notify"]}

        fake = types.SimpleNamespace(
            proactive_notify=proactive_notify,
            service=lambda name: None,  # raw path must not be reached
        )

        res = await engine.send_notification(fake, text="Nightly done", scheduled=True)

        assert res == {"ok": True, "priority": "info", "via": "proactive"}
        assert calls == [("runbook", "Nightly done",
                          {"priority": "info", "urgency": "normal"})]

    @pytest.mark.asyncio
    async def test_scheduled_critical_maps_to_critical_urgency(self):
        calls = []

        async def proactive_notify(kind, text, **kw):
            calls.append(kw)
            return {"delivered": True, "reason": "ok", "channels": ["notify"]}

        fake = types.SimpleNamespace(proactive_notify=proactive_notify,
                                     service=lambda name: None)

        await engine.send_notification(
            fake, text="Backup FAILED", priority="critical", scheduled=True,
        )

        assert calls[0]["urgency"] == "critical"

    @pytest.mark.asyncio
    async def test_scheduled_real_suppression_is_honored(self):
        async def proactive_notify(kind, text, **kw):
            return {"delivered": False, "reason": "quiet-hours", "channels": []}

        sent = []

        class Notifier:
            async def send(self, message, priority="info", source="system"):
                sent.append(message)

        fake = types.SimpleNamespace(
            proactive_notify=proactive_notify,
            service=lambda name: Notifier() if name == "notifications" else None,
        )

        res = await engine.send_notification(fake, text="Ping", scheduled=True)

        assert res["suppressed"] == "quiet-hours"
        assert sent == []  # quiet-hours is a real suppression, no raw fallback

    @pytest.mark.asyncio
    async def test_scheduled_disabled_falls_back_to_raw(self):
        async def proactive_notify(kind, text, **kw):
            return {"delivered": False, "reason": "disabled", "channels": []}

        sent = []

        class Notifier:
            async def send(self, message, priority="info", source="system"):
                sent.append((message, priority, source))

        fake = types.SimpleNamespace(
            proactive_notify=proactive_notify,
            service=lambda name: Notifier() if name == "notifications" else None,
        )

        res = await engine.send_notification(fake, text="Ping", scheduled=True)

        assert res == {"ok": True, "priority": "info"}
        assert sent == [("Ping", "info", "runbook")]


class TestDraftTokenGuard:
    """from-session confirm takes a token from the request body and uses it as
    a filename — guard against path traversal."""
    def test_valid_token_resolves_inside_drafts(self):
        d = pathlib.Path(tempfile.mkdtemp())
        fake = types.SimpleNamespace(data_dir=str(d))
        p = routes._draft_path(fake, "0123456789ab")
        assert str(p).endswith("0123456789ab.json")
        assert str(p.parent).endswith("drafts")

    @pytest.mark.parametrize("bad", [
        "../../../etc/passwd", "..\\..\\win", "abc", "0123456789AB",
        "0123456789abc", "0123456789a", "../secret", "", "a/b/c",
    ])
    def test_bad_tokens_rejected(self, bad):
        fake = types.SimpleNamespace(data_dir=tempfile.mkdtemp())
        with pytest.raises(ValueError):
            routes._draft_path(fake, bad)


class TestStaleness:
    def test_downstream_stale_when_upstream_newer(self):
        d = pathlib.Path(tempfile.mkdtemp())
        store = runs.RunStore(d)
        # Write controlled timestamps: producer 'a' ran AFTER consumer 'b'.
        store._save("rb", {"blocks": {
            "a": {"status": "ok", "ran_at": "2026-01-01T00:00:02+00:00"},
            "b": {"status": "ok", "ran_at": "2026-01-01T00:00:01+00:00"},
        }, "runs": []})
        blocks = shared.parse_blocks(
            '```eos-block\nid="a"\ntype="calculate"\noutput="x"\n---\n1\n```\n\n'
            '```eos-block\nid="b"\ntype="calculate"\noutput="y"\ninputs={ v = "x" }\n---\nv\n```'
        )
        fake = types.SimpleNamespace(data_dir=str(d))
        rows = engine.block_statuses(fake, "rb", blocks)
        by = {r["id"]: r for r in rows}
        assert by["a"]["status"] == "ok"
        assert by["b"]["status"] == "stale"
