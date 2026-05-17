from __future__ import annotations

import unittest
from unittest.mock import patch

from audiobook_narrator.auth import SupabaseAuthConfig, SupabaseAuthService


class AuthServiceTest(unittest.TestCase):
    def test_public_config_omits_secret_key(self) -> None:
        service = SupabaseAuthService(
            SupabaseAuthConfig("https://example.supabase.co", "pk_test", "secret"),
        )
        self.assertEqual(
            service.public_config(),
            {
                "supabase_url": "https://example.supabase.co",
                "supabase_publishable_key": "pk_test",
            },
        )

    def test_user_id_requires_bearer_token(self) -> None:
        service = SupabaseAuthService(
            SupabaseAuthConfig("https://example.supabase.co", "pk_test", "secret"),
        )
        with self.assertRaisesRegex(PermissionError, "Missing bearer token"):
            service.user_id_from_authorization(None)
        with patch("audiobook_narrator.auth.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"id":"user-1"}'
            self.assertEqual(service.user_id_from_authorization("Bearer good"), "user-1")
