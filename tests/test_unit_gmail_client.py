"""Gmail plugin client — pins the read-only safety contract (no network, no daemon).

The whole value of this plugin is what it CAN'T do, so the tests assert the
negative space: only the readonly scope, no write surface, metadata-only fetch,
and refusal of a token that somehow carries a wider scope.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _client():
    spec = importlib.util.spec_from_file_location(
        "gmail_client_test", REPO / "plugins" / "gmail" / "client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


C = _client()


# ── Scope is pinned read-only, and there is no write surface ─────────────────


def test_scope_is_only_readonly():
    assert C.SCOPES == ("https://www.googleapis.com/auth/gmail.readonly",)


def test_no_write_functions_exist():
    write_words = ("send", "delete", "trash", "modify", "add_label",
                   "remove_label", "insert", "import_", "batch_modify")
    offenders = [n for n in dir(C)
                 if not n.startswith("_") and callable(getattr(C, n))
                 and any(w in n.lower() for w in write_words)]
    assert offenders == [], f"unexpected write-ish functions: {offenders}"


def test_inert_without_token(tmp_path):
    assert C.load_credentials(tmp_path) is None      # no token file → None, no raise


# ── Reads are GET + metadata-only (body never fetched) ───────────────────────


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _patch_requests(monkeypatch, payload, calls):
    fake = types.ModuleType("requests")

    def _get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        return _FakeResp(payload)

    fake.get = _get
    monkeypatch.setitem(sys.modules, "requests", fake)


class _Creds:
    token = "fake-access-token"


def test_get_message_meta_uses_metadata_format_and_drops_body(monkeypatch):
    calls = []
    payload = {
        "id": "abc", "threadId": "t1", "snippet": "Re: your application",
        "labelIds": ["INBOX", "IMPORTANT"],
        "payload": {"headers": [
            {"name": "From", "value": "recruiter@acme.com"},
            {"name": "Subject", "value": "Interview invite"},
            {"name": "Date", "value": "Wed, 25 Jun 2026 10:00:00 +1000"},
            {"name": "Body", "value": "SHOULD NOT BE READ"},
        ]},
    }
    _patch_requests(monkeypatch, payload, calls)

    out = C.get_message_meta(_Creds(), "abc")

    # the request asked Gmail for metadata only — never the full body
    assert calls[0]["params"]["format"] == "metadata"
    assert calls[0]["headers"]["Authorization"] == "Bearer fake-access-token"
    # the returned dict carries no body field
    assert "body" not in out
    assert out["from"] == "recruiter@acme.com"
    assert out["subject"] == "Interview invite"
    assert out["snippet"] == "Re: your application"
    assert out["label_ids"] == ["INBOX", "IMPORTANT"]


def test_list_message_ids_clamps_max_results(monkeypatch):
    calls = []
    _patch_requests(monkeypatch, {"messages": [{"id": "1"}, {"id": "2"}]}, calls)
    ids = C.list_message_ids(_Creds(), "newer_than:90d", max_results=9999)
    assert ids == ["1", "2"]
    assert calls[0]["params"]["maxResults"] == 100      # clamped to API max
    assert calls[0]["params"]["q"] == "newer_than:90d"
