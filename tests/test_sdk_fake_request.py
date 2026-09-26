"""FakeRequest must satisfy the body readers every `@web_route` handler uses.

Handlers decode through `BaseApp.read_json` / `safe_json`, which call
`request.body()`, not `request.json()`. A shim with only `.json()` raised
AttributeError inside `call_app`, so dogfood-agent's friction route to
`quick-action.api_to_task` always failed over to a bare `task.add` and every
finding landed in the human inbox instead of the `emptyos-dogfood` project.
"""

import asyncio

from emptyos.sdk.base_app import BaseApp
from emptyos.sdk.utils import FakeRequest


def _run(coro):
    return asyncio.run(coro)


def test_read_json_decodes_a_fake_request_body():
    req = FakeRequest(body={"text": "[ux] slow list", "tag": "dogfood"})
    assert _run(BaseApp.read_json(req)) == {"text": "[ux] slow list", "tag": "dogfood"}


def test_safe_json_decodes_a_fake_request_body():
    req = FakeRequest(body={"filename": "a.md"})
    assert _run(BaseApp.safe_json(req)) == {"filename": "a.md"}


def test_non_ascii_survives_the_round_trip():
    req = FakeRequest(body={"text": "em — dash · 中文"})
    assert _run(BaseApp.read_json(req)) == {"text": "em — dash · 中文"}


def test_no_body_reads_as_empty():
    assert _run(BaseApp.read_json(FakeRequest())) == {}
    assert _run(FakeRequest().json()) == {}
