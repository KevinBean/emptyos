# Security Policy

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Use GitHub's **Report a vulnerability** button on this repo's Security tab (preferred — it opens a private advisory only the maintainer sees), or email **security@binbian.net**. Include:

- A description of the issue and the impact you think it has
- Steps to reproduce (or a proof of concept)
- The version / commit hash you tested against
- Any suggested mitigation, if you have one

You'll get an acknowledgement within **3 business days**. If the issue is confirmed, expect a fix or mitigation plan within **14 days** for high-severity findings, longer for low-severity. You'll be credited in the fix commit and release notes unless you ask not to be.

If you don't hear back within a week, feel free to follow up — email gets lost.

## Scope

In scope:

- The EmptyOS daemon (`emptyos/` — kernel, capabilities, web server, runtime)
- Bundled apps under `apps/` and bundled plugins under `plugins/`
- The Docker image and `docker-compose*.yml` files in this repo
- The `eos` CLI

Out of scope:

- User-installed apps under `apps/personal/` and user plugins
- Third-party services EmptyOS integrates with (Ollama, OpenAI, Anthropic, ComfyUI, etc.) — report those upstream
- Bugs in dependencies, unless EmptyOS uses them in a way that's exploitable beyond the dependency's own threat model
- Issues that require physical access to the host or root on the host
- Self-DoS via deliberately malicious config, vault content, or app code that the user installed themselves

## Threat model

EmptyOS is designed for three deployment contexts and the security expectations differ:

| Mode | Who can reach the daemon | Auth | Network exposure |
|---|---|---|---|
| **Local** (default) | The local user | None | `127.0.0.1` only |
| **Private** | Trusted devices on a private network or VPN (e.g. Tailscale) | Auth token + Origin check + audit log | Bound to private IP |
| **Public** | Anyone with the URL | Auth token **required** (daemon refuses to start without one) | Public IP, expected to be behind TLS reverse proxy |

In **local** mode, the daemon trusts its environment — the threat model is the same as any tool you run on your own machine. In **private** and **public** modes, the auth token is the security boundary. Treat it like a password.

## What we care about most

- Auth bypass in private/public mode
- Path traversal or arbitrary file write/read outside the configured vault
- Server-side request forgery via capability providers
- Code execution via app loading, plugin loading, or vault content
- Secrets leaking into logs, events, or HTTP responses
- Cloud-provider consent gate bypass — any path that ships vault content to a cloud provider without going through the consent check in `Capability.execute()`

## What's known and accepted

These are documented design choices, not bugs:

- **Apps run in-process with full Python access.** Installing a third-party app is equivalent to running its code on your machine. There's no sandbox. Audit before installing.
- **Single-user assumption.** The daemon serves one user; there's no multi-tenant isolation. Don't share an instance with someone you wouldn't share a laptop with.
- **Plugins are trusted.** Same caveat as apps.
- **Vault content is trusted.** Markdown notes can include arbitrary HTML; the renderer doesn't sandbox it. Don't open a vault from someone you don't trust.
- **No rate limiting in private mode.** Origin check + auth token + audit log only. If you expose private mode beyond a small trusted group, put a rate-limiting reverse proxy in front of it.

## Hardening checklist for self-hosters

- [ ] Generate a strong `EOS_NETWORK_AUTH_TOKEN` (≥32 random bytes; `python -c 'import secrets; print(secrets.token_urlsafe(32))'`)
- [ ] Run public deployments behind TLS (Caddy, nginx + Let's Encrypt) — never bare HTTP
- [ ] Set `cloud.consent` deliberately; default `"ask"` is safest
- [ ] Mount only the vault you want exposed; don't bind-mount your home directory
- [ ] Keep the Docker image current; subscribe to GitHub releases for security updates
- [ ] Don't reuse the demo's `cloud.consent = "always"` setting on a private instance — that's calibrated for BYOK ephemeral demos, not your real vault

## Disclosure policy

We follow coordinated disclosure. After a fix ships, expect a security advisory on the GitHub repo within ~30 days describing the issue, affected versions, and mitigation. If you reported the issue, you'll see a draft before it's published.
