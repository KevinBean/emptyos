# Auto-Provenance Rule — response provenance on declared output sinks

`ui.auto_provenance` is a dark flag for the serve-time provenance runtime. When
enabled in Settings (preferred, restart-free on the next page load) or config,
the server injects `/static/eos-provenance.js` into app pages.

## Page contract

- Stamp `data-ai-output="/app/api/path"` on the pre-existing container whose
  content is produced by that endpoint. The value is a URL-path prefix.
  **Prefer a prefix** — it is the self-documenting form and it scopes the sink
  to its real source.
- `data-ai-output=""` matches *every* same-origin `/api/` response on the page,
  including chrome traffic the page never issued itself (`/api/health`, polls).
  Reach for it only on a page you know makes no other API calls. The longest
  matching non-empty prefix wins; equal matches all mount.
- The runtime inserts an `EOS_UI.provenance(...)` chip as the sink's immediate
  next sibling, so `sink.innerHTML = ...` does not erase it.
- The sink must exist before the response. An empty container is sufficient.
- Pages that already mount `EOS_UI.provenance` themselves do not stamp this
  attribute; each AI output has one owner.

Only successful same-origin JSON `/api/` responses are inspected. A `provenance`
that is missing or mode-less clears the chip **only when it comes from the same
endpoint that painted it** (tracked in `data-prov-path`) — a retraction, not any
passing response. Streams, oversized JSON, cross-origin responses, and pages
without a declared sink are untouched.

## Export boundary

The runtime is injected only while serving daemon pages and is intentionally not
in the exporter whitelist. `data-ai-output` attributes are inert in standalone
exports.
