"""People — compose grounded relationship messages.

Wraps the shared ``emptyos.sdk.compose`` engine, auto-grounding the draft in a
person note (name, role, company, how-you-know-them, last contact, energy,
focus areas). The relationship half of the playbook (reconnect, congratulations,
coffee invite, thank-you, intro two people, keep-warm).

Drafting is reversible/internal — runs freely. ``/api/compose/log`` is the
opt-in bridge into the person's Quick Log via ``log_interaction`` (it bumps
``last_contact``); it records the touch, it does not send anything (v1 =
copy-to-clipboard).

Bound onto ``PeopleApp`` — see the binding block near the end of ``app.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import compose as _compose
from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import PeopleApp  # noqa: F401 — for type hints only


# ─── Bind to PeopleApp class as ──────────────────────────────────────
#   api_compose_kinds = _compose_routes.api_compose_kinds
#   api_compose       = _compose_routes.api_compose
#   api_compose_log   = _compose_routes.api_compose_log
#   _compose_context  = _compose_routes._compose_context
#   _voice_rules      = _compose_routes._voice_rules
# Adding a method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _voice_rules(self) -> list[str]:
    """The user's own voice directives from their behavior-pattern records.

    Consumer #2 of the behavior-pattern layer. Dark by default
    (``people.feature.pattern-voice.enabled``); absent records ⇒ ``[]`` ⇒
    byte-identical drafts. Reads the vault directly via the BaseApp helper, so
    people never hard-depends on the behavior-patterns app being installed.
    """
    s = self.setting("people.feature.pattern-voice.enabled", None)
    on = bool(s) if s is not None else bool(self.app_config("feature.pattern-voice.enabled", False))
    if not on:
        return []
    try:
        recs = self.behavior_patterns("self", applies_to="comms")
    except Exception:  # noqa: BLE001 — voice layer is best-effort
        return []
    return [r["voice"] for r in recs if r.get("voice")]


async def _compose_context(self, body: dict) -> dict:
    """Build the grounding {label: value} dict from a person note + overrides."""
    context: dict = {}
    person_id = (body.get("person_id") or "").strip()
    if person_id:
        p = await self.get_person(person_id)
        if p:
            if p.get("name"):
                context["Recipient"] = p["name"]
            if p.get("role"):
                context["Their role"] = p["role"]
            if p.get("company"):
                context["Company"] = p["company"]
            if p.get("relationship"):
                context["How you know them"] = p["relationship"]
            if p.get("last_contact"):
                context["Last time you were in touch"] = p["last_contact"]
            if p.get("energy"):
                context["Relationship energy"] = p["energy"]
            focus = p.get("focus_areas")
            if isinstance(focus, list) and focus:
                context["Their focus areas"] = ", ".join(str(x) for x in focus)
            elif isinstance(focus, str) and focus.strip():
                context["Their focus areas"] = focus.strip()
    # Explicit overrides / extra fields from the request win.
    for label, key in (("Recipient", "to_name"), ("Their recent news", "their_news"),
                       ("Mutual contact", "mutual")):
        v = (body.get(key) or "").strip()
        if v:
            context[label] = v
    return context


@web_route("GET", "/api/compose/kinds")
async def api_compose_kinds(self, request):
    """Relationship message kinds + channels + tones for the compose picker."""
    return {
        "kinds": _compose.kinds_for("relationship"),
        "channels": _compose.channels_meta(),
        "tones": _compose.tones_meta(),
    }


@web_route("POST", "/api/compose")
async def api_compose(self, request):
    """Draft a relationship message grounded in a person note.

    Body: {kind, channel?, tone?, person_id?, to_name?, their_news?, mutual?, freeform?, variants?}
    """
    body = await self.safe_json(request)
    kind = (body.get("kind") or "").strip()
    if not kind:
        return {"ok": False, "error": "kind required"}
    context = await self._compose_context(body)
    result = await self.compose_message(
        kind,
        (body.get("channel") or "").strip(),
        context,
        tone=(body.get("tone") or "warm").strip(),
        freeform=(body.get("freeform") or "").strip(),
        variants=bool(body.get("variants")),
        voice_rules=self._voice_rules(),
    )
    if result.get("ok"):
        await self.emit("people:message_composed", {"kind": kind, "channel": result.get("channel")})
    return result


@web_route("POST", "/api/compose/log")
async def api_compose_log(self, request):
    """Log a composed message onto the person's Quick Log (bumps last_contact). No send.

    Body: {person_id, channel?, subject?, kind?}
    """
    body = await self.safe_json(request)
    person_id = (body.get("person_id") or "").strip()
    if not person_id:
        return {"ok": False, "error": "person_id required"}
    channel = (body.get("channel") or "message").strip()
    subject = (body.get("subject") or "").strip()
    kind = (body.get("kind") or "message").strip()
    bits = [f"drafted {kind} via {channel}"]
    if subject:
        bits.append(f"re: {subject}")
    return await self.log_interaction(person_id, " — ".join(bits), source="compose")
