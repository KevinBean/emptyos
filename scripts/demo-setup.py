"""demo-setup.py — build a curated demo vault for a public EmptyOS instance.

The demo vault has no personal data. It showcases EmptyOS's three-layer note
model (frontmatter + sections + prose), app integration (tasks routed through
projects, journal entries ripple into related notes), and the wellbeing-wheel
balance. Everything here is fictional.

Run:
    python scripts/demo-setup.py [--output ./demo/vault] [--force]

This is the content that ships with the live demo instance. Keep it small
(< 50 notes) so the demo feels fresh and navigable.
"""

from __future__ import annotations

import argparse
import shutil
import textwrap
from datetime import date, timedelta
from pathlib import Path


DEMO_CLAUDE_MD = """# Demo Vault

This is a curated sample vault shipped with the EmptyOS live demo.
It contains no personal data — every name, project, and entry is fictional.

To install EmptyOS locally with your own vault, see https://github.com/KevinBean/emptyos.
"""


def _write(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def _project(root: Path, slug: str, fm: dict, body: str):
    """Create a project directory following the project standard."""
    folder = root / "10_Projects" / slug
    note = folder / f"{slug}.md"
    (folder / "docs").mkdir(parents=True, exist_ok=True)
    (folder / "assets").mkdir(parents=True, exist_ok=True)
    (folder / "log").mkdir(parents=True, exist_ok=True)
    fm_block = "---\n" + "\n".join(f"{k}: {v}" for k, v in fm.items()) + "\n---\n"
    note.write_text(fm_block + body, encoding="utf-8")


def _journal(root: Path, d: date, mood: str, body: str):
    folder = root / "50_Journal" / str(d.year)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{d.isoformat()}.md"
    fm = f"---\ndate: {d.isoformat()}\nmood: {mood}\ntags: [journal, daily]\n---\n"
    path.write_text(fm + body, encoding="utf-8")


def build(output: Path, force: bool = False):
    if output.exists():
        if not force:
            raise SystemExit(f"Refusing to overwrite {output} (use --force)")
        shutil.rmtree(output)
    output.mkdir(parents=True)

    # --- Top-level marker + CLAUDE.md ---
    (output / "CLAUDE.md").write_text(DEMO_CLAUDE_MD, encoding="utf-8")
    (output / "README.md").write_text(DEMO_CLAUDE_MD, encoding="utf-8")

    # --- PARA folders ---
    for folder in ["00_Inbox", "10_Projects", "20_Areas", "30_Resources", "40_Archive", "50_Journal"]:
        (output / folder).mkdir(parents=True, exist_ok=True)

    # --- Inbox captures ---
    _write(output / "00_Inbox" / "2026-04-15-book-idea.md", """
        ---
        created: 2026-04-15
        tags: [capture, idea]
        ---

        ## Book idea
        A field guide for switching careers after 35. Lean on real stories
        from people who did it, not survivorship-bias advice.
    """)

    _write(output / "00_Inbox" / "2026-04-16-quote.md", """
        ---
        created: 2026-04-16
        tags: [capture, quote]
        ---

        ## Quote
        > The vault is the point. The tools come and go.
    """)

    # --- Projects ---
    _project(
        output,
        "learn-piano",
        {
            "title": "Learn piano",
            "status": "active",
            "priority": "med",
            "created": "2026-01-04",
            "tags": "[project, music]",
        },
        textwrap.dedent("""

        ## Tasks
        - [ ] Practice Chopin Prelude Op. 28 No. 4 (4 sessions)
        - [ ] Record a rough take of "Hallelujah" and listen back
        - [x] Book a lesson with local teacher
        - [ ] Set metronome practice goal: 80 BPM scales, 4 weeks

        ## Timeline
        - 2026-01-04 — started
        - 2026-02-12 — first lesson
        - 2026-03-30 — played Clair de Lune intro all the way through

        ## Notes
        The hardest part isn't the hands; it's keeping the practice habit
        alive during busy weeks. Short daily sessions beat weekend marathons.
        """),
    )

    _project(
        output,
        "garden-rebuild",
        {
            "title": "Garden rebuild",
            "status": "active",
            "priority": "low",
            "created": "2026-03-01",
            "tags": "[project, home, environmental]",
        },
        textwrap.dedent("""

        ## Tasks
        - [ ] Source pollinator-friendly perennials
        - [ ] Build raised bed #3
        - [x] Tear out old lawn on south side
        - [ ] Set up drip irrigation before July heat

        ## Notes
        Native plants only. Goal: something bees visit every month from March
        to October. No lawn.
        """),
    )

    _project(
        output,
        "read-50-books",
        {
            "title": "Read 50 books in 2026",
            "status": "active",
            "priority": "low",
            "created": "2026-01-01",
            "tags": "[project, learning, intellectual]",
        },
        textwrap.dedent("""

        ## Tasks
        - [ ] Finish "The Overstory" (page 340)
        - [ ] Request "Piranesi" from library
        - [x] "Annie Ernaux — A Woman's Story"

        ## Reading log
        2026-01 — 5 books
        2026-02 — 4 books
        2026-03 — 6 books

        ## Notes
        Notes per book live in 30_Resources/Books/. Not chasing the number —
        chasing the habit of finishing what I start.
        """),
    )

    _project(
        output,
        "inbox",
        {
            "title": "Inbox",
            "status": "active",
            "priority": "med",
            "tags": "[project, system]",
        },
        textwrap.dedent("""

        ## Tasks
        - [ ] Decide whether to keep the Tuesday standing meeting
        - [ ] Refactor the morning routine — it's grown to 90 minutes
        """),
    )

    # --- Areas ---
    _write(output / "20_Areas" / "Health.md", """
        ---
        title: Health
        tags: [area, physical]
        ---

        ## Current focus
        Sleep window 22:30–06:30. Walk 8k/day. Strength 3x/week.

        ## Notes
        Track sleep debt, not sleep duration. A 7-hour night after a 5-hour
        one doesn't "make up" — the deficit compounds.
    """)

    _write(output / "20_Areas" / "Relationships.md", """
        ---
        title: Relationships
        tags: [area, social]
        ---

        ## Current focus
        One deliberate outreach per week — a friend I haven't talked to in months.

        ## Notes
        Quality > quantity. Two hours of full attention beats ten hours of
        half-listening.
    """)

    # --- Resources ---
    _write(output / "30_Resources" / "Books" / "the-overstory.md", """
        ---
        title: The Overstory
        author: Richard Powers
        tags: [book, fiction]
        started: 2026-03-12
        ---

        ## Summary
        Nine characters, nine trees. The novel argues that trees are not
        background — they are the protagonists we keep missing.

        ## Quotes
        > The best time to plant a tree was twenty years ago. The second best
        > time is now.

        ## Thoughts
        Reads slow on purpose. The first 100 pages are nine self-contained
        short stories. They weave together only once you've lived with each one.
    """)

    _write(output / "30_Resources" / "Music" / "chopin-prelude-op28-no4.md", """
        ---
        title: Chopin Prelude Op. 28 No. 4 in E minor
        composer: Chopin
        tags: [music, piano, piece]
        ---

        ## Practice notes
        Left hand: repeating chords, resist making them louder over time.
        Right hand: let the phrase breathe — this is a dirge, not an étude.

        ## Why this piece
        Short. Manageable. Every practice ends feeling like I played music,
        not drills.
    """)

    # --- Journal ---
    today = date(2026, 4, 17)
    _journal(output, today - timedelta(days=2), "content", """
        ## Morning
        Walked before work. Saw the first swallows back.

        ## Work
        Shipped the refactor. Quiet day after.

        ## Piano
        15 minutes, Chopin. Hands finally relaxed on the descending phrase.
    """)
    _journal(output, today - timedelta(days=1), "tired", """
        ## Notes
        Up too late reading. Payment comes tomorrow — sleep is non-negotiable
        tonight.
    """)
    _journal(output, today, "focused", """
        ## Morning
        Eight hours of sleep. The difference isn't subtle.

        ## Work
        Deep focus block 09:00–11:30. Finished the migration plan.

        ## Evening
        Garden: planted three lavenders. Bees within the hour.
    """)

    # --- EmptyOS vault-map (auto-generated on first boot, but pre-seeded
    #     here so the demo is ready to serve immediately) ---
    (output / "30_Resources" / "EmptyOS").mkdir(parents=True, exist_ok=True)
    _write(output / "30_Resources" / "EmptyOS" / "_vault-map.toml", """
        # Vault map — app data locations within this vault.
        # Auto-generated on first boot in a real install; pre-seeded for the demo.

        [projects]
        path = "10_Projects"

        [journal]
        path = "50_Journal"

        [capture]
        path = "00_Inbox"

        [note]
        path = "30_Resources"
    """)

    print(f"Demo vault created at: {output}")
    print(f"  Notes:    {sum(1 for _ in output.rglob('*.md'))}")
    print(f"  Folders:  {sum(1 for p in output.rglob('*') if p.is_dir())}")


def main():
    ap = argparse.ArgumentParser(description="Build the EmptyOS demo vault.")
    ap.add_argument("--output", default="./demo/vault", help="output directory")
    ap.add_argument("--force", action="store_true", help="overwrite existing")
    args = ap.parse_args()
    build(Path(args.output).resolve(), force=args.force)


if __name__ == "__main__":
    main()
