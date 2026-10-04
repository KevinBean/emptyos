"""The optional creative pass proposes paths and never edits source material."""

import asyncio
import json

from emptyos.sdk.creative_improv import explore


class FakeApp:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def think(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.response

    def last_provenance(self):
        return {"mode": "local", "provider": "test", "model": "fake"}


def test_explore_requires_a_start_and_end_before_calling_model():
    app = FakeApp("unexpected")
    result = asyncio.run(explore(app, "writing", {"start": "A locked door"}))
    assert result == {"error": "start and intended end are required"}
    assert app.calls == []


def test_explore_returns_editable_offer_chains_with_connections():
    response = json.dumps({"paths": [{
        "name": "The key changes hands",
        "steps": [
            {"offer": "She finds a key", "because": "The door implies a lock"},
            {"offer": "The key belongs to her rival", "because": "The key's owner raises the cost"},
            {"offer": "She returns the key", "because": "The choice resolves the rivalry"},
        ],
        "return": "The door opens after she gives up the key",
        "risk": "The coincidence may feel convenient",
    }, {
        "name": "The door stays closed",
        "steps": [
            {"offer": "She knocks", "because": "The door blocks her"},
            {"offer": "A voice answers", "because": "The knock invites a response"},
            {"offer": "She walks away", "because": "The response changes her choice"},
        ],
        "return": "She no longer needs the door to open",
        "risk": "The choice needs preparation",
    }]})
    app = FakeApp(response)
    result = asyncio.run(explore(app, "writing", {
        "start": "A locked door", "end": "She chooses trust", "constraints": "One room"
    }))
    assert result["ok"] is True
    assert result["provenance"]["model"] == "fake"
    assert result["paths"][0]["steps"][1]["because"] == "The key's owner raises the cost"
    prompt, _ = app.calls[0]
    assert "A locked door" in prompt and "She chooses trust" in prompt and "One room" in prompt


def test_explore_rejects_unusable_model_output():
    app = FakeApp(json.dumps({"paths": [
        {"name": "First fragment", "steps": [
            {"offer": "A flash"}, {"offer": "A face"}, {"offer": "A cut"}]},
        {"name": "Second fragment", "steps": [
            {"offer": "A shadow"}, {"offer": "A lamp"}, {"offer": "A door"}]},
    ]}))
    result = asyncio.run(explore(app, "mv", {"start": "Dark phone", "end": "Daylight"}))
    assert result == {"error": "could not form two usable candidate paths"}


def test_explore_requires_both_candidate_paths():
    app = FakeApp(json.dumps({"paths": [{"name": "Only one", "steps": [
        {"offer": "A light", "because": "The room is dark"},
        {"offer": "A signal", "because": "The light can communicate"},
        {"offer": "A reply", "because": "The signal reaches someone"},
    ]}]}))
    result = asyncio.run(explore(app, "mv", {"start": "Dark room", "end": "Connection"}))
    assert result == {"error": "could not form two usable candidate paths"}


def test_explore_handles_malformed_model_reply():
    app = FakeApp("This is not JSON")
    result = asyncio.run(explore(app, "writing", {"start": "A key", "end": "Trust"}))
    assert result == {"error": "could not read candidate paths"}


def test_explore_rejects_repeated_or_unfinished_paths():
    path = {"name": "Same", "steps": [
        {"offer": "A light", "because": "It is dark"},
        {"offer": "A signal", "because": "Light can signal"},
        {"offer": "A reply", "because": "Someone sees it"},
    ], "return": "Light means home", "risk": "Coincidence"}
    app = FakeApp(json.dumps({"paths": [path, path]}))
    assert asyncio.run(explore(app, "mv", {"start": "Dark", "end": "Home"})) == {"error": "candidate paths repeat the same offers"}
    app = FakeApp(json.dumps({"paths": [path, {**path, "steps": [*path["steps"][:-1], {"offer": "A door", "because": "A reply arrives"}], "return": ""}]}))
    assert asyncio.run(explore(app, "mv", {"start": "Dark", "end": "Home"})) == {"error": "could not form two usable candidate paths"}
