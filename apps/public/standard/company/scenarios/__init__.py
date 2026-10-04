"""Scenario registry — pluggable runners over a company's workers.

Each scenario exposes `async run(app, company, workers, prompt, mode) -> dict`
that returns the persisted run record. Add a new scenario by writing a
module here and registering it in SCENARIOS.
"""

from __future__ import annotations

from . import critique, interview, workshop

SCENARIOS = {
    "critique": critique,
    "workshop": workshop,
    "interview": interview,
}

SCENARIO_META = {
    "critique": {
        "label": "Critique",
        "description": "Workers comment on a proposal and vote. Read-only.",
        "emoji": "\U0001f50d",
    },
    "workshop": {
        "label": "Workshop",
        "description": "Workers draft work; proposed actions go through a review gate.",
        "emoji": "\U0001f6e0",
    },
    "interview": {
        "label": "Interview",
        "description": "Each worker answers the same question, from their role.",
        "emoji": "\U0001f3a4",
    },
}
