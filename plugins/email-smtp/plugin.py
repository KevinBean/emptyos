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
"""

from __future__ import annotations

import asyncio
import os

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

    def consent_summary(self, **kwargs) -> str:
        to = kwargs.get("to", "")
        subject = kwargs.get("subject", "")
        body = kwargs.get("body", "")
        return f"To: {to}\nSubject: {subject}\n\n{body}"

    async def execute(self, *, to: str, subject: str = "", body: str = "", **kwargs) -> dict:
        if not to:
            raise ValueError("send: 'to' is required")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_sync, to, subject, body, kwargs)
        return {"ok": True, "provider": self.name, "detail": f"emailed {to}"}

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
