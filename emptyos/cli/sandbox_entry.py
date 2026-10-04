"""`eos-sandbox` console entry point.

This is a thin convenience wrapper for people who want every command in the
invocation to target a ready sandbox member without spelling `EOS_CONFIG=...`.
"""

from __future__ import annotations

import os
import sys


def main():
    # Let emptyos.cli.main's callback resolve the ready sandbox. Keeping the
    # resolution there means `eos --sandbox ...` and `eos-sandbox ...` share
    # the same behavior.
    os.environ["EOS_SANDBOX_DEFAULT"] = "1"
    from emptyos.cli.main import app

    app(prog_name="eos-sandbox", args=sys.argv[1:])
