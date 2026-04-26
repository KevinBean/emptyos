# Contributing to EmptyOS

Thanks for your interest. EmptyOS grows through three modes — code, conversation, and use — and contributions in any of them help.

## Ways to contribute

- **File an issue** — bug reports, design questions, "why does it work this way" all welcome. Search first; reference the file path + line number you're looking at.
- **Open a PR** — fix a bug, add an app, extract a pattern into the SDK, sharpen the docs.
- **Build an app and share it** — apps are atoms (manifest + `app.py`). If you write one that's generally useful, propose moving it from `apps/personal/` into `apps/`.
- **Use it and tell us what broke** — dogfood feedback is as valuable as code.

## Before you start

Read these once — they encode decisions you'd otherwise have to rediscover:

- `README.md` and `docs/GETTING-STARTED.md` — what the system is and how to run it
- `CLAUDE.md` — the project's living constitution (architecture, principles, app patterns)
- `docs/DESIGN.md` — philosophy and the consciousness model
- `docs/APP-DEVELOPMENT.md` — building apps

The two non-negotiable principles:

1. **With you, not for you.** Features that augment human judgment (surface, suggest, assist, generate-for-review) are the default. Features that replace judgment (autopilot, silent auto-decisions) need strong justification.
2. **Apps use capabilities, never raw tools.** `self.read()` not `open()`. `self.think()` not direct LLM calls. This is what makes the system self-testable and provider-agnostic.

## Development setup

```bash
git clone https://github.com/KevinBean/emptyos.git
cd emptyos
cp emptyos.toml.example emptyos.toml      # then edit notes.path
pip install -e .
pip install playwright pytest-playwright pytest-timeout pytest-rerunfailures httpx
playwright install chromium
python -m emptyos start                    # boots daemon on :9000
```

Run tests before opening a PR:

```bash
python -m pytest tests/ --ignore=tests/personal -v
```

If you touched a single app, the per-app file is faster:

```bash
python -m pytest tests/test_sys_<app>.py -v
```

Always invoke as `python -m pytest`, not bare `pytest` — see `CLAUDE.md` § Testing for why.

## Pull request checklist

- [ ] Code follows the patterns in `CLAUDE.md` § Development Rules (capabilities not raw I/O, `@web_route` for app APIs, no hardcoded `localhost`/personal paths)
- [ ] New apps include a `tests/test_sys_<app>.py` with ≥10 cases
- [ ] No personal data in committed files — `python scripts/check-personal.py` passes
- [ ] No third-party brand names in user-facing strings — `python scripts/check-branding.py` passes
- [ ] Docs touched if behaviour changed (CLAUDE.md, GETTING-STARTED.md, or app's own page)
- [ ] PR description explains the *why*, not just the *what*

CI runs the personal-data scan, the branding scan, and `pytest --collect-only` on every push. Merges block on those.

## Coding style

- Python 3.11+, type hints encouraged but not required
- Prefer editing existing files over creating new ones
- No comments unless the *why* is non-obvious
- No backwards-compatibility shims for not-yet-released code — just change it
- Prompts are first-class artifacts: `UPPERCASE` constants, `system=` for persona, negative examples in content prompts (see CLAUDE.md § Development Rule 12)

## Commit messages

`type(scope): short summary` — e.g. `feat(journal): add streak detection`, `fix(reactor): debounce vault watcher`. Multi-line body for the *why*. No mandatory format beyond legibility.

## What gets accepted

- **Yes**: bug fixes, new generic apps, SDK extractions (after the second caller appears), test coverage, docs sharpenings, capability providers, plugin integrations.
- **Maybe**: new core principles, kernel changes, breaking API changes — open an issue first to discuss.
- **No**: features that replace user judgment with silent automation, third-party brand names baked into UI text, personal data or absolute paths, dependencies that pull a runtime into Docker without a fallback.

## License

By contributing, you agree your contributions are licensed under [MIT](LICENSE), the same license as the project.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Security

If you find a security issue, please follow [SECURITY.md](SECURITY.md) — don't open a public issue.
