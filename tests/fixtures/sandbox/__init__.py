"""Sandbox test fixtures — idempotent seeders that POST to a leased sandbox
member's app APIs to bootstrap known scenarios.

Each fixture exports a `seed(host: str) -> dict` function. `host` is the
member's URL (e.g. `http://127.0.0.1:9002`). Sandbox members run with
`network.mode = "local"`, so no auth header is needed.

Convention:
- Fixtures are idempotent — safe to run repeatedly; only create what's
  missing. Use existing `GET` endpoints to check before `POST`ing.
- Fixtures hit real `@web_route` handlers — same code paths as user-driven
  CRUD. No bespoke seed plumbing.
- Each fixture is also a CLI: `python tests/fixtures/sandbox/<name>.py <host>`.

The Claude self-driven testing contract is in `.claude/rules/sandbox-driven-testing.md`.
"""
