"""Demo seed — one hand-authored branching story (no LLM).

Ships a small, valid C1 interactive-fiction graph so story mode is playable
offline the moment the flag is on, and so live verification has a deterministic
fixture that doesn't depend on a model. Idempotent: skips if the story exists.
"""

from __future__ import annotations

from emptyos.sdk.decision_graph import DecisionGraph

SLUG = "the-tower-at-dusk"

STORY = {
    "start": "arrival",
    "vars": {},
    "meta": {
        "title": "The Tower at Dusk",
        "premise": "You reach a lighthouse that should have been decommissioned years ago — yet its lamp is lit.",
        "bible": {
            "setting": "a weathered granite lighthouse on a black-rock headland, salt spray, a single warm lamp burning at the top, slate-grey sea under a bruised dusk sky",
            "era": "timeless coastal, late autumn evening",
            "characters": [
                {"name": "You", "anchor": "a lone traveller in a dark oilskin coat, collar up, windblown"},
                {"name": "The Keeper", "anchor": "a tall stooped figure in a faded navy jumper, white stubble, lantern-lit weathered face"},
            ],
            "style_hint": "lonely, luminous, faintly uncanny",
        },
    },
    "nodes": [
        {
            "id": "arrival",
            "kind": "content",
            "data": {
                "title": "The Lit Lamp",
                "text": "The path peters out at the headland, and there it stands: a lighthouse the maps insist was decommissioned a decade ago. Yet high above, the lamp turns — a slow, deliberate beam sweeping the darkening water. The door at its base is ajar, and a thread of warm light leaks onto the wet stone. The wind presses at your back, almost encouraging.",
            },
            "transitions": [
                {"to": "enter", "kind": "choice", "label": "Push the door open and step inside"},
                {"to": "circle", "kind": "choice", "label": "Circle the tower first, looking for another way"},
            ],
        },
        {
            "id": "circle",
            "kind": "content",
            "data": {
                "title": "Around the Base",
                "text": "You follow the curve of the granite. On the seaward side a narrow iron ladder climbs into the dark, slick with spray and rust. There are no other doors — only that ladder, and the open one you passed. Somewhere above, a gull complains. The choice narrows, as choices on headlands tend to.",
            },
            "transitions": [
                {"to": "climb", "kind": "choice", "label": "Climb the slippery outer ladder"},
                {"to": "enter", "kind": "choice", "label": "Go back and take the open door"},
            ],
        },
        {
            "id": "enter",
            "kind": "content",
            "data": {
                "title": "The Spiral Stair",
                "text": "Inside, the air is close and smells of paraffin and old rope. A spiral stair winds upward, each step worn to a shallow dish by countless feet. From above comes a sound you can't quite place — a low, patient humming, the kind a person makes when they have waited a long time and expect to wait longer.",
            },
            "transitions": [
                {"to": "keeper", "kind": "choice", "label": "Climb toward the humming"},
                {"to": "leave", "kind": "choice", "label": "Think better of it and leave"},
            ],
        },
        {
            "id": "climb",
            "kind": "content",
            "data": {
                "title": "The Outer Ascent",
                "text": "The ladder is a mistake and you know it three rungs up, but pride is a poor listener. At the gallery rail a hand reaches down — weathered, steady — and hauls you over. The Keeper says nothing at first, only studies you the way the lamp studies the sea: slowly, completely.",
            },
            "transitions": [
                {"to": "keeper", "kind": "choice", "label": "Follow the Keeper inside"},
            ],
        },
        {
            "id": "keeper",
            "kind": "content",
            "data": {
                "title": "The Keeper",
                "text": "At the top, the Keeper tends the great lamp by hand, though no hand should be needed. 'They switched me off,' he says, not turning. 'On paper.' He gestures at the beam combing the dark water. 'But something still needs guiding home. The question is only ever whether you'll carry the light on, or let it gutter.' He holds out the matches.",
            },
            "transitions": [
                {"to": "accept", "kind": "choice", "label": "Take the matches"},
                {"to": "decline", "kind": "choice", "label": "Refuse and walk back down"},
            ],
        },
        {
            "id": "accept",
            "kind": "end",
            "data": {
                "title": "The Light Carries On",
                "text": "Your fingers close around the matches. The Keeper steps back, and in stepping back seems to thin, to become a shape the lamplight passes through. The beam does not falter. Below, far out, a small boat corrects its course toward the headland. You understand now that the light was never the building's to give — only the keeping of it, passed hand to hand. You begin to wait.",
            },
            "transitions": [],
        },
        {
            "id": "decline",
            "kind": "end",
            "data": {
                "title": "The Long Walk Back",
                "text": "You shake your head and descend, the humming fading behind you. At the headland's edge you look back once: the lamp still turns, patient as ever. Perhaps it needs no one. Perhaps that was the test, and you passed it by refusing a burden that was never fairly offered. The maps were right after all — and yet, all the way home, you find yourself listening for the sea.",
            },
            "transitions": [],
        },
        {
            "id": "leave",
            "kind": "end",
            "data": {
                "title": "Better Judgement",
                "text": "Some doors are open precisely because no one sensible walks through them. You step back into the wind and let the dark have its lighthouse. Whatever waited at the top waits still — and that, you decide, is exactly where it should stay.",
            },
            "transitions": [],
        },
    ],
}


async def seed(app) -> dict:
    existing = {s["slug"] for s in app._list_stories()}
    if SLUG in existing:
        return {"skipped": "story already seeded"}
    graph = DecisionGraph.from_dict(STORY)
    errors = app._story_validation_errors(graph)
    if errors:
        return {"error": "seed story invalid: " + "; ".join(errors[:5])}
    rel = app._save_story(
        SLUG,
        STORY["meta"]["title"],
        graph,
        lang="English",
        cefr="C1",
        premise=STORY["meta"]["premise"],
    )
    return {"created": 1, "path": rel}
