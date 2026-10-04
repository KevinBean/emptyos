"""Publish — per-site branding-framework draft evaluator.

Grades a draft post against its target site's **branding framework** (a vault
note ``<source_folder>/_framework.md``, same presence-gated convention as
``writer._voice_block``'s ``_voice.md``) and returns an advisory **scorecard** —
per-dimension scores + guardrail hits + fixes. Read-only: the scorecard is a
card the user reads, never an applied edit, so it does NOT go through the pending
gate (.claude/rules/proposed-action.md "read-only → nothing to gate";
propose-never-autofill, .claude/rules/field-suggest.md).

Two layers produce one scorecard:
  - **Deterministic** (reuse, no new logic): ``emptyos.sdk.prose_lint.lint_prose``
    (tone) + ``self._redaction_hits`` (the app's own in-process ``.eos-personal``
    /``.eos-branding`` port in assets.py). NOT the git-only check-*.py CLIs.
  - **Judgment** (new): ``self.think`` grounded in the framework + draft +
    deterministic findings → ``parse_llm_json`` → validated scorecard. Shaped like
    ``BaseApp.propose_kb_extractions`` (extract → parse → validate).

Gated by the dark flag ``feature.framework-eval.enabled`` (default False): off →
the endpoints return ``{ok: False}`` and nothing changes.

Functions here are bound onto PublishApp as methods (see app.py). They receive
``self`` first and use BaseApp helpers + the app's site/vault helpers directly.
Reaches into other modules: ``self._redaction_hits`` (assets),
``self._source_folder`` / ``self._vault_dir`` / ``self._get_site`` /
``self._active_site`` (app). Do not import from ``.app`` (it imports us → cycle).

SDK graduation (Dev Rule 9): publish is the FIRST runtime consumer of
"grade an artifact against a declared rubric → structured scorecard" (every other
LLM-judge in the repo returns prose; model-bench/conformance are deterministic).
On a SECOND consumer (a KB-note grader, a resume-vs-JD app, a room-output grader),
extract a structured ``multi_lens_score()`` sibling into ``emptyos/sdk/multi_lens.py``
(lenses = dimensions, per-lens {score, notes}) or a ``grade_against_rubric()``
helper following ``propose_kb_extractions``. Do NOT extract before that.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.utils import parse_frontmatter, parse_llm_json, strip_frontmatter

from .prompts import PROMPTS

if TYPE_CHECKING:
    from .app import PublishApp  # noqa: F401 — for type hints only


# ─── Bind to PublishApp class as ─────────────────────────────────────
#   _framework_enabled    = _framework._framework_enabled
#   _framework_note_path  = _framework._framework_note_path
#   _load_framework       = _framework._load_framework
#   _deterministic_findings = _framework._deterministic_findings
#   _grade_draft          = _framework._grade_draft
#   api_evaluate          = _framework.api_evaluate
#   api_framework_get     = _framework.api_framework_get
#   api_framework_seed    = _framework.api_framework_seed
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


# Default scoring axes when a site's _framework.md declares no "## Dimensions".
DEFAULT_DIMENSIONS: list[str] = [
    "Positioning fit",
    "Spine (judgment / verifiable execution)",
    "Audience fit",
    "Receipts-grade",
    "Differentiation",
]


def _framework_enabled(self) -> bool:
    """Dark flag — evaluator off by default (endpoints inert when off).

    Honours the runtime Settings store as well as ``emptyos.toml`` so the
    evaluator can be armed from the UI without a daemon restart. Either source
    turning it on is enough; toml stays authoritative for a machine that wants
    it on at boot.
    """
    if bool(self.app_config("feature.framework-eval.enabled", False)):
        return True
    return bool(self.setting("publish.feature.framework-eval.enabled", False))


def _framework_note_path(self, site: dict | None) -> Path | None:
    """Absolute path to ``<vault>/<source_folder>/_framework.md`` for a site."""
    vault = self._vault_dir()
    source = self._source_folder(site)
    if not vault or not source:
        return None
    return Path(vault) / source / "_framework.md"


def _parse_dimensions(text: str) -> list[str]:
    """Extract axis names from a ``## Dimensions`` bullet list; [] if absent.

    Pure. Reads the first ``## Dimensions`` (or ``## Scoring dimensions``)
    section and returns each ``- `` / ``* `` bullet's leading label (text before
    a `` — ``/`` - ``/``:`` separator), so a note can annotate each axis.
    """
    lines = (text or "").splitlines()
    out: list[str] = []
    in_section = False
    for ln in lines:
        s = ln.strip()
        low = s.lower()
        if s.startswith("#"):
            if in_section:
                break  # next heading ends the section
            in_section = low.startswith("## dimensions") or low.startswith(
                "## scoring dimensions"
            )
            continue
        if not in_section:
            continue
        if s.startswith("- ") or s.startswith("* "):
            item = s[2:].strip().lstrip("*_ ").strip()
            for sep in (" — ", " - ", ": ", "—", ":"):
                if sep in item:
                    item = item.split(sep, 1)[0].strip()
                    break
            item = item.strip("*_` ")
            if item:
                out.append(item[:80])
    return out


async def _load_framework(self, site: dict | None) -> dict:
    """Read a site's branding framework note → {present, text, dimensions}.

    Presence-gated like ``writer._voice_block``: no note → present=False and the
    built-in default dimensions, so callers can still grade (against a generic
    rubric) or surface "no framework configured".
    """
    path = _framework_note_path(self, site)
    if not path or not path.exists():
        return {"present": False, "text": "", "dimensions": list(DEFAULT_DIMENSIONS)}
    try:
        body = strip_frontmatter(await self.read(str(path))).strip()
    except Exception:
        return {"present": False, "text": "", "dimensions": list(DEFAULT_DIMENSIONS)}
    dims = _parse_dimensions(body) or list(DEFAULT_DIMENSIONS)
    return {"present": bool(body), "text": body[:12000], "dimensions": dims}


def _deterministic_findings(self, body: str) -> dict:
    """Tone + leak findings for a draft body — reuse only, no new rules.

    ``lint_prose`` (in-process, structured) + ``self._redaction_hits`` (the app's
    own ``.eos-personal``/``.eos-branding`` port). Never raises.
    """
    from emptyos.sdk.prose_lint import lint_prose

    try:
        prose = lint_prose(body or "", source="draft")
    except Exception:
        prose = {"metrics": {}, "findings": []}
    try:
        redaction = self._redaction_hits(body or "")
    except Exception:
        redaction = []
    return {"prose": prose, "redaction": redaction}


def _guardrail_hits_from_det(det: dict) -> list[dict]:
    """Turn deterministic findings into scorecard guardrail rows (pure)."""
    hits: list[dict] = []
    for r in det.get("redaction", []) or []:
        hits.append(
            {
                "kind": "redaction",
                "severity": "high",
                "detail": f"leaks '{r.get('match', '')}' (matches {r.get('pattern', '')})",
            }
        )
    for f in (det.get("prose", {}) or {}).get("findings", []) or []:
        if f.get("severity") == "high":
            hits.append(
                {
                    "kind": "prose-tone",
                    "severity": "high",
                    "detail": f"L{f.get('line', '?')} {f.get('rule', '')}: {f.get('message', '')}",
                }
            )
    return hits


def _validate_scorecard(raw: object, dimensions: list[str], det: dict, site_id: str) -> dict:
    """Coerce a model reply into the scorecard contract (pure, never raises)."""
    obj = raw if isinstance(raw, dict) else {}

    def _clamp(v) -> int | None:
        try:
            n = int(round(float(v)))
        except (TypeError, ValueError):
            return None
        return max(1, min(5, n))

    dims_out: list[dict] = []
    raw_dims = obj.get("dimensions")
    by_name = {}
    if isinstance(raw_dims, list):
        for d in raw_dims:
            if isinstance(d, dict) and d.get("name"):
                by_name[str(d["name"]).strip().lower()] = d
    for name in dimensions:
        d = by_name.get(name.strip().lower(), {})
        dims_out.append(
            {
                "name": name,
                "score": _clamp(d.get("score")),
                "notes": str(d.get("notes", "")).strip()[:400],
            }
        )

    # Deterministic hits come from a real scanner and are ground truth. Model hits
    # are the model's *opinion* that something is a violation — surfaced (they're
    # often useful) but tagged, because only the deterministic ones may gate.
    guardrails = [{**g, "source": "deterministic"} for g in _guardrail_hits_from_det(det)]
    for g in obj.get("guardrail_hits", []) or []:
        if isinstance(g, dict) and g.get("detail"):
            guardrails.append(
                {
                    "kind": str(g.get("kind", "brand"))[:40],
                    "severity": str(g.get("severity", "medium"))[:10],
                    "detail": str(g["detail"]).strip()[:400],
                    "source": "model",
                }
            )

    # Both `overall` and `verdict` are DERIVED from the clamped per-axis scores,
    # never taken from the model's own top-level numbers. Those two fields are the
    # ones nothing validates, and the model contradicts its own axis scores with
    # them in both directions: a draft scoring 4/5/4/5/4/3/5 (mean 4.3) came back
    # overall=2 verdict="off-brand", and nothing would have stopped an overall=5
    # "ready" asserted over a row of 1s. The per-axis scores are the rubric the
    # model had to defend; grade on those, ignore the headline it just asserted.
    #
    # Only a DETERMINISTIC high-severity hit (a real scanner found a real leak) may
    # gate. A model-asserted "high" is just the model's opinion wearing a severity
    # label, and it is non-deterministic: the same footer link was flagged high on
    # one run and not the next, flipping the verdict on identical text with
    # identical scores. That is the same bug as trusting the model's `verdict`
    # field — an assertion treated as ground truth. Model hits stay in the output
    # (they're often right, and a human should read them) but never decide.
    scored = [d["score"] for d in dims_out if d["score"] is not None]
    overall = int(round(sum(scored) / len(scored))) if scored else None
    if any(g["severity"] == "high" and g.get("source") == "deterministic" for g in guardrails):
        verdict = "off-brand"
    elif any(s <= 2 for s in scored):
        # A hard failure on any single rubric axis is off-brand regardless of
        # how well the other axes carried the average.
        verdict = "off-brand"
    elif overall is not None and overall >= 4:
        verdict = "ready"
    else:
        verdict = "needs-polish"

    fixes = [str(f).strip()[:300] for f in (obj.get("fixes") or []) if str(f).strip()][:6]

    return {
        "site": site_id,
        "verdict": verdict,
        "overall": overall,
        "dimensions": dims_out,
        "guardrail_hits": guardrails,
        "fixes": fixes,
        "deterministic": det,
    }


def _eval_user_message(framework: dict, title: str, body: str, det: dict) -> str:
    """Assemble the grading user message (pure)."""
    fw = framework.get("text") or "(no framework note configured — grade against the default dimensions below.)"
    dims = "\n".join(f"- {d}" for d in framework.get("dimensions", []))
    prose_metrics = (det.get("prose", {}) or {}).get("metrics", {})
    prose_findings = (det.get("prose", {}) or {}).get("findings", [])
    det_lines = [f"- prose metrics: {prose_metrics}"]
    for f in prose_findings[:8]:
        det_lines.append(f"- prose [{f.get('severity')}] L{f.get('line')} {f.get('rule')}: {f.get('message')}")
    for r in (det.get("redaction") or [])[:8]:
        det_lines.append(f"- LEAK: '{r.get('match')}' matches {r.get('pattern')}")
    det_block = "\n".join(det_lines) if det_lines else "(none)"
    return (
        f"## Branding framework for this site\n{fw}\n\n"
        f"## Score the draft on exactly these dimensions (1-5 each)\n{dims}\n\n"
        f"## Deterministic findings (ground truth — fold into guardrail_hits + notes)\n{det_block}\n\n"
        f"## Draft to evaluate\nTITLE: {title}\n\n{body[:12000]}"
    )


async def _grade_draft(self, title: str, body: str, framework: dict, det: dict, site_id: str) -> dict:
    """LLM judgment layer → validated scorecard.

    First consumer of grade-artifact-vs-rubric; see the module docstring's SDK
    graduation note before copying this into a second app.
    """
    system = PROMPTS.eval_scorecard_system  # resolve once (prefix-cache rule)
    user = _eval_user_message(framework, title, body, det)
    parsed: dict = {}
    err = None
    # Retry once: a cold provider (claude-cli's first call after a daemon
    # restart) can return a non-JSON preamble; a second call is warm and clean.
    for _ in range(2):
        try:
            raw = await self.think(user, system=system, domain="text", temperature=0.3)
        except Exception as e:
            err = f"evaluation failed: {e}"
            continue
        cand = parse_llm_json(raw, fallback={})
        if isinstance(cand, dict) and cand.get("dimensions"):
            parsed, err = cand, None
            break
        err = "model did not return a usable scorecard"
    card = _validate_scorecard(parsed, framework.get("dimensions", []), det, site_id)
    card["provenance"] = self.last_provenance()
    if err and not parsed:
        card["error"] = err  # deterministic verdict/guardrails still stand
    return card


def _read_draft_body(self, raw_path: str) -> tuple[str, str, str]:
    """Resolve a vault-relative/absolute path → (abs_path, title, body). Raises on escape."""
    vault = self._vault_dir()
    fp = Path(raw_path)
    if not fp.is_absolute():
        fp = Path(vault) / raw_path
    fp.resolve().relative_to(Path(vault).resolve())  # containment (raises ValueError)
    if not fp.exists():
        raise FileNotFoundError(raw_path)
    content = fp.read_text(encoding="utf-8")
    fm = parse_frontmatter(content) or {}
    title = str(fm.get("title") or Path(raw_path).stem.replace("-", " "))
    return str(fp), title, strip_frontmatter(content).strip()


@web_route("POST", "/api/evaluate")
async def api_evaluate(self, request):
    """Grade a draft against its target site's framework → advisory scorecard.

    Body: {path, site_id?|site?}. Read-only — never writes, never gates.
    """
    if not _framework_enabled(self):
        return {"ok": False, "error": "framework evaluation is disabled"}
    data = await self.safe_json(request) or {}
    raw_path = (data.get("path") or "").strip()
    if not raw_path:
        return {"ok": False, "error": "path is required"}
    site_id = (data.get("site_id") or data.get("site") or "").strip()
    site = self._get_site(site_id) if site_id else self._active_site()
    if site is None:
        return {"ok": False, "error": f"site '{site_id}' not found"}
    try:
        _abs, title, body = _read_draft_body(self, raw_path)
    except ValueError:
        return {"ok": False, "error": "path outside vault"}
    except FileNotFoundError:
        return {"ok": False, "error": "draft not found"}
    framework = await _load_framework(self, site)
    det = _deterministic_findings(self, body)
    card = await _grade_draft(self, title, body, framework, det, site["id"])
    card["ok"] = True
    card["framework_present"] = framework["present"]
    return card


@web_route("GET", "/api/framework/{site_id}")
async def api_framework_get(self, request):
    """Presence + dimensions of a site's framework note (for the settings panel)."""
    site_id = request.path_params.get("site_id", "")
    site = self._get_site(site_id)
    if site is None:
        return {"ok": False, "error": "site not found"}
    fw = await _load_framework(self, site)
    path = _framework_note_path(self, site)
    return {
        "ok": True,
        "enabled": _framework_enabled(self),
        "present": fw["present"],
        "dimensions": fw["dimensions"],
        "path": str(path) if path else "",
    }


@web_route("POST", "/api/framework/{site_id}/seed")
async def api_framework_seed(self, request):
    """Create ``_framework.md`` from the built-in template when absent (never overwrites)."""
    if not _framework_enabled(self):
        return {"ok": False, "error": "framework evaluation is disabled"}
    site_id = request.path_params.get("site_id", "")
    site = self._get_site(site_id)
    if site is None:
        return {"ok": False, "error": "site not found"}
    path = _framework_note_path(self, site)
    if path is None:
        return {"ok": False, "error": "no vault source folder for this site"}
    if path.exists():
        return {"ok": False, "error": "framework note already exists", "path": str(path)}
    template = _EMPTYOS_FRAMEWORK_TEMPLATE if site_id == "emptyos" else _DEFAULT_FRAMEWORK_TEMPLATE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template, encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"could not write note: {e}"}
    return {"ok": True, "path": str(path)}


# ── Seed templates (constants travel with their consumer, multi-module rule 5) ──

_DEFAULT_FRAMEWORK_TEMPLATE = """---
author: user
---

# Branding framework — draft your voice

Fill in each section below in your own words before the framework evaluator
starts grading drafts against it. Delete this note's guidance text as you go.

## Positioning
Who are you writing as? What's the one-line description a stranger should
walk away with? What would make a post feel obviously off-brand for you?

## Spine
What's the underlying principle every post should reflect back to, even when
the topic varies? (e.g. a stance on how you work, a recurring theme, a value
you build around.)

## Audiences
Who is this actually for? List 1-3 concrete audiences (not "everyone") and
what each one is looking for when they read you.

## Post shape
What does a typical post look like end to end? (e.g. problem → mechanism →
how it's built → a real demo/receipt → why it matters.) Prefer a shape that
forces a real, falsifiable artifact per post over argument-only pieces.

## Dimensions
List the 3-5 things you'd score a draft against before publishing — these
become the framework evaluator's grading rubric. Examples: does it match your
positioning, does it serve the audiences above, does it carry a real receipt
(a demo/number/repo path) rather than just an argument.

## Hard guardrails (any hit = off-brand)
List anything that should never appear in public copy for you — proprietary
work product, employer/client data, topics you keep private, personal data,
third-party branding. Be specific; a vague guardrail won't get enforced.
"""

_EMPTYOS_FRAMEWORK_TEMPLATE = """---
author: user
---

# Branding framework — EmptyOS product voice

## Positioning
EmptyOS is a mind companion — think and create with you, not for you. A local-first,
markdown-vault operating system. Builder-to-builder, impersonal; show the mechanism,
never hype.

## Spine
The human owns judgment; the machine owns reversible, verifiable execution.

## Audiences
- AI-builder community — reusable ideas, working demos, borrowable patterns.
- Self-hosters / local-first advocates — data ownership, no lock-in.

## Post shape
Mechanism deep-dive, demo-centered: problem → the mechanism → how it's wired →
a live demo / screenshot → why the design is right.

## Dimensions
- Positioning fit — reads as the mind-companion OS, builder-to-builder, not hype?
- Spine — reversibility / verifiable-execution present?
- Audience fit — useful/borrowable for a builder or self-hoster?
- Receipts-grade — a real demo / screenshot / mechanism, not just a claim?
- Differentiation — a genuinely new angle?

## Hard guardrails (any hit = off-brand)
- No personal data (this is the product voice, not the author's).
- No third-party branding — generic terms ("markdown vault", "source URL").
- No cable-rating / proprietary engineering.
"""
