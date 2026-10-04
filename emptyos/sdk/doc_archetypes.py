"""Document archetype scaffolds for the PDF markdown profile.

A thin *typed-document* layer over ``emptyos.sdk.pdf``: given an archetype name
("resume", "research-report"), produce a profile-valid markdown skeleton plus
the matching ``PdfStyle`` theme, so a caller composes a *kind of document*
instead of hand-shaping every masthead + section each time.

Borrowed idea, not borrowed code: tw93/Kami ships archetype templates +
its own HTML→PDF renderer. EmptyOS already has the renderer (``pdf.py`` via
Playwright) and the design-system-as-constraints (the PDF markdown profile +
``PDF_THEMES``). The only missing piece was the *archetype scaffold* — that's
all this module adds.

Pure: no kernel, no I/O, no LLM call. Two consumption shapes:

  scaffold = scaffold_markdown("resume")          # empty profile-valid skeleton
  # ... fill it (by hand, or via an LLM using compose_prompt) ...
  render_markdown_pdf(filled, style=archetype_style("resume"))

  system, user = compose_prompt("resume", brief, profile=PROFILE_REMINDER)
  filled = await self.think(user, system=system, domain="text")

The scaffold follows ``.claude/rules/pdf-markdown.md`` exactly: a leading fenced
masthead, ``## sections``, and ``%%…%%`` guidance comments that vanish at render
time (``pdf.py::_obsidian_clean`` strips them) so an unfilled scaffold still
renders cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Section:
    """One ``## Heading`` block in a scaffold."""

    heading: str
    guidance: str = ""          # %%…%% hint shown to the author/LLM, stripped at render
    body: str = ""              # optional example markdown (e.g. a sample ### entry)


@dataclass(frozen=True)
class DocArchetype:
    """A typed document: masthead shape + section skeleton + default theme."""

    name: str
    title: str                  # human label ("Resume / CV")
    description: str            # one line — what this archetype is for
    style: str                  # PDF_THEMES key
    masthead: list[str]         # placeholder masthead lines (name/title, subtitle, meta…)
    sections: list[Section]
    page_size: str = "A4"
    compose_system: str = ""    # LLM persona for the fill path (compose_prompt)


# Reminder appended to every compose_prompt so the LLM keeps the profile.
PROFILE_REMINDER = (
    "Output ONLY the filled markdown. Keep the leading ```fenced``` masthead block "
    "(first line = name/title, second = subtitle, rest = contact/meta). Use `## ` for "
    "sections and `### ` for individual entries (the line right after a `### ` renders "
    "as muted meta — put dates/location there). Use `- ` bullets, `**bold**` for emphasis, "
    "and `| tables |` where tabular. Remove every `%%…%%` guidance comment. Do not add "
    "an H1 title above the masthead. Do not invent facts not in the brief."
)


_RESUME = DocArchetype(
    name="resume",
    title="Resume / CV",
    description="One- to two-page professional resume — masthead + experience entries.",
    style="slate",
    masthead=[
        "Full Name",
        "Target role · specialisation",
        "email · phone · location · links",
    ],
    sections=[
        Section("Summary", "2–3 sentence positioning statement — who you are, what you're targeting."),
        Section(
            "Experience",
            "One `### Org — Role` per job, newest first. The line after each ### is the date/location meta. 3–5 achievement bullets each (impact + number where possible).",
            body="### Company — Title\nCity · 2023–Present\n\n- Achievement with a measurable result\n",
        ),
        Section("Skills", "Grouped, comma-separated. Lead with what the target role asks for."),
        Section("Education", "One `### Institution — Qualification` per entry, with year on the meta line."),
    ],
    compose_system=(
        "You are a resume writer. Produce a tight, results-led resume from the brief. "
        "Lead bullets with impact and quantify where the brief allows. Plain, confident "
        "voice — no clichés ('passionate', 'synergy', 'go-getter'), no filler."
    ),
)


_RESEARCH_REPORT = DocArchetype(
    name="research-report",
    title="Research Report",
    description="Structured analytical report — exec summary through recommendations + references.",
    style="default",
    masthead=[
        "Report Title",
        "Subtitle / scope line",
        "Author · date",
    ],
    sections=[
        Section("Executive Summary", "3–5 sentences: the question, the headline finding, the recommendation. A reader who stops here should still get the point."),
        Section("Background", "What prompted this, what's already known, what's in/out of scope."),
        Section("Findings", "The evidence. Use `### ` sub-findings; `| tables |` for data; `**bold**` the numbers that matter."),
        Section("Analysis", "What the findings mean — interpretation, trade-offs, caveats. Distinct from raw findings above."),
        Section("Recommendations", "Numbered, actionable, prioritised. Each ties back to a finding."),
        Section("References", "Sources cited above. One `- ` bullet each."),
    ],
    compose_system=(
        "You are an analyst writing a research report. Be precise and evidence-led: "
        "every claim in Analysis traces to a Findings item, every recommendation traces "
        "to a finding. State uncertainty plainly. No marketing tone, no padding."
    ),
)


DOCUMENT_ARCHETYPES: dict[str, DocArchetype] = {
    a.name: a for a in (_RESUME, _RESEARCH_REPORT)
}


def archetype_names() -> list[str]:
    """Registered archetype names, sorted."""
    return sorted(DOCUMENT_ARCHETYPES)


def get_archetype(name: str) -> DocArchetype:
    """Look up an archetype by name. Raises KeyError on an unknown name (loud — a
    typo'd archetype should fail at the call site, not silently scaffold nothing)."""
    key = (name or "").lower().strip()
    if key not in DOCUMENT_ARCHETYPES:
        raise KeyError(
            f"unknown document archetype {name!r}; known: {', '.join(archetype_names())}"
        )
    return DOCUMENT_ARCHETYPES[key]


def archetype_style(name: str) -> str:
    """The PDF_THEMES key this archetype renders with — pass straight to
    ``render_markdown_pdf(..., style=archetype_style(name))``."""
    return get_archetype(name).style


def scaffold_markdown(name: str, *, with_guidance: bool = True) -> str:
    """Build an empty, profile-valid markdown skeleton for an archetype.

    Follows ``.claude/rules/pdf-markdown.md``: a leading fenced masthead, then
    one ``## `` block per section. Section guidance is emitted as ``%%…%%``
    comments (stripped at render by ``pdf.py::_obsidian_clean``), so the scaffold
    renders cleanly even if left unfilled. Pass ``with_guidance=False`` for a
    bare skeleton (headings + masthead only).
    """
    a = get_archetype(name)
    parts: list[str] = ["```", *a.masthead, "```", ""]
    for sec in a.sections:
        parts.append(f"## {sec.heading}")
        parts.append("")
        if with_guidance and sec.guidance:
            parts.append(f"%%{sec.guidance}%%")
            parts.append("")
        if sec.body:
            parts.append(sec.body.rstrip())
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def compose_prompt(name: str, brief: str, *, profile: str = PROFILE_REMINDER) -> tuple[str, str]:
    """Build ``(system, user)`` for an LLM that fills this archetype from a brief.

    Pure — returns the two strings; the caller runs ``self.think(user, system=…)``.
    The user message carries the brief + the scaffold to fill, so the model can't
    drift off the profile.
    """
    a = get_archetype(name)
    system = f"{a.compose_system}\n\n{profile}".strip()
    user = (
        f"Compose a {a.title.lower()} from this brief:\n\n{brief.strip()}\n\n"
        f"Fill in this scaffold (replace every %%guidance%% comment with real content "
        f"and remove the comment markers):\n\n{scaffold_markdown(name)}"
    )
    return system, user
