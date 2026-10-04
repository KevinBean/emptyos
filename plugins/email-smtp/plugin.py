"""Email (SMTP) plugin — injects an outbound email provider into `send`.

Graceful-enhancement pattern (like edge-tts → speak): when configured, the
`send` capability gains an SMTP provider; when not, the chain falls through
to the human fallback (or raises in daemon mode) and apps degrade.

Config in emptyos.toml (gitignored — keeps mail creds out of git per Rule 13):

    [plugins.email-smtp]
    host = "smtp.gmail.com"
    port = 587
    username = "you@gmail.com"
    password_env = "EOS_SMTP_PASSWORD"   # env var holding an app-password
    from_addr = "you@gmail.com"          # defaults to username
    from_name = "Your Name"              # optional display name
    security = "starttls"                # starttls | ssl | none
    timeout = 30

The SMTP host is non-localhost for hosted mail, so the provider is
cloud-classified and the first send passes through the consent gate. Grant it
once (consent policy "always" for `email-smtp`) and booking/outreach emails
flow without a per-message prompt.

Attachments (added 2026-08-18):

    await self.send(to="…", subject="…", body="…",
                    attachments=["D:/emptyos/dist/thing.zip"])

Absolute paths only, capped at 20MB total, and every file is validated *before*
the SMTP connection opens — an invalid path raises rather than sending a mail
that quietly lacks the file it was sent for. The names and sizes are rendered
into `consent_summary`, because the gate is the last place a wrong attachment
can be caught and a bare filename hides how big it is.
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path

from emptyos.capabilities import Provider
from emptyos.sdk import BasePlugin


class EmailSmtpPlugin(BasePlugin):
    name = "email_smtp"

    async def connect(self):
        host = (self.config("host", "") or "").strip()
        if not host:
            # Nothing to do — leave `send` to its human fallback so we don't
            # plant a dead provider in the chain.
            return
        from_addr = (self.config("from_addr", "") or self.config("username", "") or "").strip()
        provider = SmtpSendProvider(
            host=host,
            port=int(self.config("port", 587) or 587),
            username=(self.config("username", "") or "").strip(),
            password_env=(self.config("password_env", "EOS_SMTP_PASSWORD") or "").strip(),
            from_addr=from_addr,
            from_name=(self.config("from_name", "") or "").strip(),
            security=(self.config("security", "starttls") or "starttls").strip().lower(),
            timeout=int(self.config("timeout", 30) or 30),
        )
        send = self.kernel.capabilities.get("send")
        # priority 0 — tried before the human fallback.
        send.add_provider(provider, priority=0)


class SmtpSendProvider(Provider):
    """Outbound email via a standard SMTP server (stdlib smtplib, no deps)."""

    name = "email-smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        username: str = "",
        password_env: str = "EOS_SMTP_PASSWORD",
        from_addr: str = "",
        from_name: str = "",
        security: str = "starttls",
        timeout: int = 30,
    ):
        self.host = host  # base Provider.is_cloud auto-detects non-local host
        # A mail send carries no per-call charge, so the monthly spend cap
        # (spend_cap.is_metered) neither counts nor stops it.
        self.metered = False
        self.port = port
        self.username = username
        self.password_env = password_env
        self.from_addr = from_addr or username
        self.from_name = from_name
        self.security = security if security in ("starttls", "ssl", "none") else "starttls"
        self.timeout = timeout

    def _password(self) -> str:
        return os.environ.get(self.password_env, "") if self.password_env else ""

    async def available(self) -> bool:
        # Configured host + a from-address is the floor. Auth is optional —
        # some local/relay setups don't require a password.
        return bool(self.host and self.from_addr)

    async def health(self) -> dict:
        if self.host and self.from_addr:
            return {"available": True, "reason": None, "recovery": None}
        return {
            "available": False,
            "reason": "SMTP host or from-address not configured",
            "recovery": {
                "kind": "config",
                "path": "emptyos.toml",
                "section": "[plugins.email-smtp]",
            },
        }

    # Total attached bytes. Gmail rejects ~25MB and most relays sit near it, so
    # the cap exists to fail *here* with a readable message rather than at the
    # SMTP server after the upload.
    MAX_ATTACH_BYTES = 20 * 1024 * 1024

    def _resolve_attachments(self, raw) -> list[Path]:
        """Validate `attachments=` into readable files, or raise.

        Outbound, so it refuses rather than silently skipping: an email that
        quietly arrives without the file it was sent for is worse than an error.
        """
        if not raw:
            return []
        items = [raw] if isinstance(raw, (str, Path)) else list(raw)
        out: list[Path] = []
        total = 0
        for item in items:
            p = Path(str(item)).expanduser()
            if not p.is_absolute():
                raise ValueError(f"send: attachment must be an absolute path: {item}")
            if not p.is_file():
                raise ValueError(f"send: attachment not found: {p}")
            size = p.stat().st_size
            total += size
            if total > self.MAX_ATTACH_BYTES:
                raise ValueError(
                    f"send: attachments exceed {self.MAX_ATTACH_BYTES // (1024*1024)}MB "
                    f"(at {p.name}, running total {total // 1024}KB)"
                )
            out.append(p)
        return out

    def consent_summary(self, **kwargs) -> str:
        to = kwargs.get("to", "")
        subject = kwargs.get("subject", "")
        body = kwargs.get("body", "")
        # Attachments must appear in the consent text — the gate is the last
        # place a wrong file can be caught, and a name alone hides the size.
        lines = [f"To: {to}", f"Subject: {subject}"]
        try:
            files = self._resolve_attachments(kwargs.get("attachments"))
        except ValueError as e:
            files, lines = [], lines + [f"Attachments: INVALID — {e}"]
        for p in files:
            lines.append(f"Attachment: {p.name} ({p.stat().st_size // 1024} KB) — {p}")
        return "\n".join(lines) + f"\n\n{body}"

    async def execute(self, *, to: str, subject: str = "", body: str = "", **kwargs) -> dict:
        if not to:
            raise ValueError("send: 'to' is required")
        files = self._resolve_attachments(kwargs.get("attachments"))
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_sync, to, subject, body, kwargs)
        detail = f"emailed {to}"
        if files:
            detail += " with " + ", ".join(p.name for p in files)
        return {"ok": True, "provider": self.name, "detail": detail}

    def _send_sync(self, to: str, subject: str, body: str, kwargs: dict) -> None:
        import smtplib
        from email.message import EmailMessage
        from email.utils import formataddr

        msg = EmailMessage()
        msg["From"] = formataddr((self.from_name, self.from_addr)) if self.from_name else self.from_addr
        msg["To"] = to
        msg["Subject"] = subject
        html = kwargs.get("html", "")
        msg.set_content(body or (subject or "(no content)"))
        if html:
            msg.add_alternative(html, subtype="html")

        # Attachments last: add_attachment on a message that already has an
        # html alternative converts it to multipart/mixed correctly, whereas
        # attaching before set_content loses the body.
        for p in self._resolve_attachments(kwargs.get("attachments")):
            ctype, _ = mimetypes.guess_type(p.name)
            maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
            msg.add_attachment(p.read_bytes(), maintype=maintype,
                               subtype=subtype or "octet-stream", filename=p.name)

        password = self._password()
        if self.security == "ssl":
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        try:
            server.ehlo()
            if self.security == "starttls":
                server.starttls()
                server.ehlo()
            if self.username and password:
                server.login(self.username, password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass
