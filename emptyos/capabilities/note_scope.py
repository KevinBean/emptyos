"""Keep the learner's notes on the machine unless they switch it on.

Rule 19: vault content does not go to a cloud provider by default. A build that
allows a cloud model (``[cloud] allow``) still sends only calls that carry no
notes — a dictionary word, a page being read. Apps whose AI calls do send the
learner's notes are listed in ``[cloud] note_apps`` (env ``EOS_CLOUD_NOTE_APPS``,
comma-separated); for them every cloud provider is refused until the learner
turns on ``cloud.notes_to_cloud`` in Settings. Off unless ``note_apps`` is set.

The rule is per app, not per call, on purpose: an app is listed because its AI
features read notes, and a "this call carries notes" marker would leak through
any call site that forgot it. The calling app's id reaches the chain as
``caller_app``, which the BaseApp think helpers supply; code that calls
``Capability.execute`` directly must pass it too (kb's flipbook refine does).
A local provider still answers for a listed app.
"""

from __future__ import annotations

NOTES_SETTING = "cloud.notes_to_cloud"
NOTES_SETTING_LABEL = "Let AI features read my notes"


class NotesToCloudOff(RuntimeError):
    """A listed app's AI call found only cloud providers, and the learner has
    not allowed their notes to go to one. The message keeps the
    "No available provider for capability" prefix, like SpendCapReached."""


def _truthy(value) -> bool:
    """Only an explicit yes opts in; anything else, a bare number included,
    stays off — the failure mode of a stray value is notes staying local."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return value is True


class NoteScope:
    def __init__(self, note_apps, settings=None):
        self.note_apps = frozenset(a for a in (str(x).strip() for x in note_apps) if a)
        self._settings = settings

    @classmethod
    def from_config(cls, config, settings=None) -> NoteScope | None:
        raw = config.get("cloud.note_apps", ()) or ()
        items = raw.split(",") if isinstance(raw, str) else raw
        scope = cls(items, settings)
        return scope if scope.note_apps else None

    def opted_in(self) -> bool:
        """The learner's switch, read on every call so it takes effect at once.
        Anything but an explicit yes is no."""
        try:
            return _truthy(self._settings.get(NOTES_SETTING, False)) if self._settings else False
        except Exception:
            return False

    def blocks(self, provider, app_id: str | None) -> bool:
        return (
            bool(app_id)
            and app_id in self.note_apps
            and bool(getattr(provider, "is_cloud", False))
            and not self.opted_in()
        )

    @staticmethod
    def reason() -> str:
        return (f"AI features in this app can read your notes, so they are off "
                f"until you turn on '{NOTES_SETTING_LABEL}' in Settings")
