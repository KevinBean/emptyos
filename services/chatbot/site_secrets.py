"""Small deployment-local secret store; values are never returned by admin APIs."""

from __future__ import annotations

import json
import os
from pathlib import Path


class SiteSecrets:
    def __init__(self, path: Path | None = None) -> None:
        data = Path(os.environ.get("CHATBOT_DATA_DIR", "./data"))
        data.mkdir(parents=True, exist_ok=True)
        self.path = path or data / "site-secrets.json"

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def configured(self, site_id: str) -> bool:
        return bool(self._read().get(site_id, {}).get("bearer_token"))

    def get_bearer(self, site_id: str) -> str:
        return str(self._read().get(site_id, {}).get("bearer_token") or "")

    def set_bearer(self, site_id: str, value: str) -> None:
        payload = self._read()
        if value:
            payload.setdefault(site_id, {})["bearer_token"] = value
        else:
            payload.pop(site_id, None)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)
