from __future__ import annotations

import os
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class SupabaseAuthConfig:
    url: str
    publishable_key: str
    service_role_key: str

    @classmethod
    def from_env(cls) -> "SupabaseAuthConfig":
        missing = [
            key
            for key in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SERVICE_ROLE_KEY")
            if not os.getenv(key)
        ]
        if missing:
            raise RuntimeError(f"Missing Supabase auth settings: {', '.join(missing)}")
        return cls(
            url=os.environ["SUPABASE_URL"],
            publishable_key=os.environ["SUPABASE_PUBLISHABLE_KEY"],
            service_role_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )


class SupabaseAuthService:
    def __init__(self, config: SupabaseAuthConfig) -> None:
        self.config = config

    @classmethod
    def from_env(cls) -> "SupabaseAuthService":
        config = SupabaseAuthConfig.from_env()
        return cls(config)

    def public_config(self) -> dict[str, str]:
        return {
            "supabase_url": self.config.url,
            "supabase_publishable_key": self.config.publishable_key,
        }

    def user_id_from_authorization(self, authorization: str | None) -> str:
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            raise PermissionError("Missing bearer token.")
        token = authorization[len(prefix) :].strip()
        if not token:
            raise PermissionError("Missing bearer token.")
        request = Request(
            f"{self.config.url.rstrip('/')}/auth/v1/user",
            headers={
                "apikey": self.config.service_role_key,
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise PermissionError("Invalid or expired bearer token.") from exc
        user_id = payload.get("id")
        if not user_id:
            raise PermissionError("Invalid or expired bearer token.")
        return str(user_id)
