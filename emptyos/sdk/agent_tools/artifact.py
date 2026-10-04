"""CreateArtifact tool — the model writes a standalone page, viz stores it.

A chat answer is sometimes a *thing* rather than a paragraph: a timeline, a
calculator, a diagram, a small playable board. This is how a session produces
one. The model writes a complete single-file HTML document inside the tool
call, and the tool hands it to the viz app, which is already the home for
exactly this artifact — vault record, ``author: ai`` frontmatter, the
vault-graph ``source`` link back to the chat, and the **version ring** that
makes "no, make it blue" undoable instead of destructive.

Two things it deliberately does NOT do:

- **It does not generate.** viz's own ``generate`` verb asks a model for an
  artifact; here the model that is already talking to the user has written it,
  and a second generation pass would only re-derive what exists and lose the
  conversation's context in the process.
- **It does not write files.** Every path decision belongs to viz
  (``save_artifact`` → ``_persist``), so a chat cannot choose where an artifact
  lands, and the tool needs no filesystem reach of its own. This is what lets
  it sit in the ``chat`` profile beside Read and Fetch rather than beside
  Write.

The saved page is served at ``/viz/api/html/<id>``. Wherever EmptyOS renders
it, it is framed with an opaque origin (``sandbox="allow-scripts"``, plus a CSP
on the response when viz's flag is on), so model-written script cannot read the
session cookie or any same-origin response. It can still *issue* a simple
same-origin request, which an opaque origin does not prevent and a header
cannot close — that is the daemon's CSRF posture, not this tool's.

**Rendering is a property of the surface, not of this tool.** The chat home
shows an artifact beside the conversation; ``/agent/`` and ``eos chat`` do not,
and the reply must not promise a panel that isn't there — hence the ``/viz/#``
link in the result text.
"""

from __future__ import annotations

from emptyos.sdk.agent_tools.base import Tool, ToolResult

#: Shapes worth offering in a chat. viz knows more (3d-scene, game-2d, …), but
#: those need a strong model and a long generation; a chat artifact is a page
#: the model writes in one tool call. An unlisted shape is refused rather than
#: silently rewritten, so the model learns the set.
CHAT_SHAPES = ("chart", "svg-diagram", "schematic", "network-graph",
               "math-explainer", "mermaid", "slide-deck", "anim-explainer")

DEFAULT_SHAPE = "svg-diagram"

#: Refuse a runaway here, with a message the model can act on, rather than
#: letting it reach viz — whose own refusal reads "LLM output truncated …
#: regenerate (don't iterate)", advice naming verbs this tool does not have.
#: That only works while this ceiling is at or BELOW viz's (``viz.max_html_kb``,
#: 256 KB by default): above it, the band between the two does exactly what
#: this constant exists to prevent. Measured in encoded bytes, not characters —
#: a CJK page is ~3× larger than its length suggests, and viz counts bytes.
MAX_HTML_BYTES = 256 * 1024


class CreateArtifactTool(Tool):
    name = "CreateArtifact"
    description = (
        "Save a standalone HTML page you have written as an artifact the user "
        "can see, keep and re-open — use it when the answer is a THING (a chart, "
        "a diagram, an interactive explainer, a slide deck) rather than prose. "
        "The user can always open it at /viz/#<id>; some surfaces also show it "
        "beside the conversation. "
        "Write one complete document starting with <!doctype html>; all CSS and "
        "JS inline or from a public CDN, no local paths (it is served as a "
        "static file). To revise an artifact you already made in this "
        "conversation, pass its id back as `artifact_id` — the previous version "
        "is kept, so the user can undo. Do not paste the HTML into your reply as "
        "well — say what the artifact shows."
    )
    permission = "auto"
    readonly = False
    input_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short name for the artifact, e.g. 'Q3 travel budget'.",
            },
            "html": {
                "type": "string",
                "description": "The complete HTML document, starting with <!doctype html>.",
            },
            "shape": {
                "type": "string",
                "enum": list(CHAT_SHAPES),
                "description": f"What kind of artifact this is. Default {DEFAULT_SHAPE}.",
            },
            "artifact_id": {
                "type": "string",
                "description": (
                    "Only when revising an artifact from THIS conversation: its "
                    "id, as returned by an earlier CreateArtifact call."
                ),
            },
        },
        "required": ["title", "html"],
    }

    def is_readonly(self, input: dict) -> bool:
        # Plan mode is "look, don't touch": an artifact is a new vault note.
        return False

    def permission_summary(self, input: dict) -> str:
        title = str(input.get("title") or "untitled")
        rid = str(input.get("artifact_id") or "").strip()
        size = len(str(input.get("html") or ""))
        what = f"revise artifact {rid}" if rid else "save a new artifact"
        return f"CreateArtifact: {what} — {title} ({size} bytes)"

    async def run(self, app, **kwargs) -> ToolResult:
        title = str(kwargs.get("title") or "").strip()
        html = str(kwargs.get("html") or "")
        shape = str(kwargs.get("shape") or "").strip() or DEFAULT_SHAPE
        rid = str(kwargs.get("artifact_id") or "").strip()

        if not title:
            return ToolResult(ok=False, content="error: title is required")
        if not html.strip():
            return ToolResult(ok=False, content="error: html is required")
        size = len(html.encode("utf-8"))
        if size > MAX_HTML_BYTES:
            return ToolResult(
                ok=False,
                content=(
                    f"error: artifact is {size} bytes, over the "
                    f"{MAX_HTML_BYTES} limit — split it up or simplify the page"
                ),
            )
        if shape not in CHAT_SHAPES:
            return ToolResult(
                ok=False,
                content=f"error: unknown shape {shape!r} — use one of: {', '.join(CHAT_SHAPES)}",
            )
        if app is None or not hasattr(app, "call_app"):
            return ToolResult(ok=False, content="error: agent app reference unavailable")

        try:
            res = await app.call_app(
                "viz", "save_artifact",
                content=html,
                title=title,
                shape=shape,
                # The app id, not the session: a tool only ever sees the model's
                # own arguments, and reading the live session off `app` would be
                # shared mutable state across concurrent chats. Which chat made
                # it is recorded by the after-tool hook, which does know.
                source="agent",
                rid=rid,
            )
        except Exception as e:  # noqa: BLE001 — viz absent, or a write failure
            return ToolResult(ok=False, content=f"error: could not save the artifact ({e})")

        if not isinstance(res, dict) or not res.get("ok"):
            why = (res or {}).get("error") if isinstance(res, dict) else "viz did not answer"
            return ToolResult(ok=False, content=f"error: {why}")

        new_id = res.get("id") or rid
        if res.get("unchanged"):
            # viz refused a byte-identical re-save. Say so plainly: a model that
            # reads "Updated" here calls again, and the loop that produced 25
            # identical calls in one turn was exactly this sentence sounding
            # like progress instead of like an ending.
            return ToolResult(
                ok=True,
                content=(
                    f"No change — artifact {new_id} already has exactly this content. "
                    f"It is saved and viewable at /viz/#{new_id}. Do NOT call "
                    f"CreateArtifact again for it; answer the user in words."
                ),
                display={"artifact": {
                    "id": new_id, "title": str(res.get("title") or title), "shape": shape,
                    "updated": False, "url": f"/viz/api/html/{new_id}",
                }},
            )
        # "revised", not "updated": viz's metadata carries an `updated`
        # timestamp, so that name would be truthy on every save.
        updated = bool(res.get("revised"))
        return ToolResult(
            ok=True,
            content=(
                f"{'Updated' if updated else 'Saved'} artifact {new_id} ({title}). "
                f"Done — do NOT call CreateArtifact again for this. Answer the user "
                f"in words now, saying what the artifact shows. It is viewable at "
                f"/viz/#{new_id}, and the chat home displays it beside the "
                f"conversation. Call again only if the user asks for a CHANGE, "
                f"passing artifact_id={new_id}."
            ),
            display={
                "artifact": {
                    "id": new_id,
                    "title": title,
                    "shape": shape,
                    "updated": updated,
                    "url": f"/viz/api/html/{new_id}",
                }
            },
        )
