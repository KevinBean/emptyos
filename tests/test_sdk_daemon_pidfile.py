"""The daemon PID record must never hand a killer the wrong process."""

from __future__ import annotations

import json
import os

import pytest

from emptyos.sdk import daemon_pidfile as dp


def test_roundtrip_identifies_this_process(tmp_path):
    record = dp.write_pidfile(tmp_path, port=9000)
    assert record is not None, "psutil is required for a verifiable record"
    assert record["pid"] == os.getpid()
    assert dp.verify_owner(tmp_path, port=9000) == os.getpid()


def test_missing_record_is_unknown_not_a_guess(tmp_path):
    assert dp.read_pidfile(tmp_path) is None
    assert dp.verify_owner(tmp_path) is None


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.update(identity="something-else"), id="foreign-identity"),
        pytest.param(lambda r: r.update(create_time=r["create_time"] - 3600), id="recycled-pid"),
        pytest.param(lambda r: r.update(pid=2 ** 30), id="dead-pid"),
        pytest.param(lambda r: r.pop("create_time"), id="no-create-time"),
    ],
)
def test_a_record_that_cannot_be_trusted_returns_none(tmp_path, mutate):
    """Every one of these used to be a live PID for someone to force-kill.

    `create_time` is the load-bearing field: a bare PID is recycled by the OS,
    so a stale record can name an unrelated process. Returning None makes the
    caller fall back to asking the user, which is the safe direction — the
    unsafe one is widening the aim to every python.exe on the machine.
    """
    dp.write_pidfile(tmp_path, port=9000)
    record = dp.read_pidfile(tmp_path)
    mutate(record)
    (tmp_path / dp.PIDFILE_NAME).write_text(json.dumps(record), encoding="utf-8")

    assert dp.verify_owner(tmp_path, port=9000) is None


def test_record_written_for_another_port_is_not_ours(tmp_path):
    """A sandbox member on :9002 must not be mistaken for the :9000 daemon."""
    dp.write_pidfile(tmp_path, port=9002)
    assert dp.verify_owner(tmp_path, port=9000) is None
    assert dp.verify_owner(tmp_path, port=9002) == os.getpid()


def test_corrupt_file_is_unknown_not_an_exception(tmp_path):
    (tmp_path / dp.PIDFILE_NAME).write_text("{not json", encoding="utf-8")
    assert dp.read_pidfile(tmp_path) is None
    assert dp.verify_owner(tmp_path) is None


def test_clear_is_idempotent(tmp_path):
    dp.write_pidfile(tmp_path, port=9000)
    dp.clear_pidfile(tmp_path)
    dp.clear_pidfile(tmp_path)
    assert dp.verify_owner(tmp_path) is None
