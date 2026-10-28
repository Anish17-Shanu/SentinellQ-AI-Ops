import unittest

from support import make_settings

from errors import ConfigError


class SettingsTests(unittest.TestCase):
    def test_local_defaults(self):
        settings = make_settings(".")
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8081)
        self.assertEqual(settings.storage_backend, "json")
        self.assertFalse(settings.production)
        self.assertEqual(settings.max_body_bytes, 262144)
        self.assertEqual(settings.default_tenant, "default")
        self.assertFalse(settings.auth_required)
        self.assertEqual(settings.anonymous_role, "admin")

    def test_overrides_are_applied(self):
        settings = make_settings(
            ".",
            HOST="0.0.0.0",
            PORT="9000",
            SENTINELIQ_STORAGE="sqlite",
            SENTINELIQ_RATE_LIMIT_RPM="30",
            SENTINELIQ_CORS_ORIGINS="https://ops.example.com, https://admin.example.com",
        )
        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.port, 9000)
        self.assertEqual(settings.storage_backend, "sqlite")
        self.assertEqual(settings.rate_limit_rpm, 30)
        self.assertEqual(settings.cors_origins, ("https://ops.example.com", "https://admin.example.com"))

    def test_rejects_invalid_port(self):
        with self.assertRaises(ConfigError):
            make_settings(".", PORT="not-a-number")

    def test_rejects_unknown_environment(self):
        with self.assertRaises(ConfigError):
            make_settings(".", SENTINELIQ_ENV="staging")

    def test_rejects_unknown_storage(self):
        with self.assertRaises(ConfigError):
            make_settings(".", SENTINELIQ_STORAGE="mysql")

    def test_postgres_requires_database_url(self):
        with self.assertRaises(ConfigError):
            make_settings(".", SENTINELIQ_STORAGE="postgres")

    def test_auth_required_needs_keys(self):
        with self.assertRaises(ConfigError):
            make_settings(".", SENTINELIQ_AUTH_REQUIRED="true")

    def test_auth_is_implied_by_keys(self):
        settings = make_settings(".", SENTINELIQ_API_KEYS="viewer-key-123456:viewer:acme")
        self.assertTrue(settings.auth_required)
        self.assertEqual(settings.anonymous_role, "disabled")
        self.assertTrue(settings.production is False)

    def test_anonymous_viewer_mode(self):
        settings = make_settings(".", SENTINELIQ_ANONYMOUS_ROLE="viewer")
        self.assertEqual(settings.anonymous_role, "viewer")
        self.assertFalse(settings.auth_required)

    def test_keys_with_anonymous_viewer(self):
        settings = make_settings(
            ".",
            SENTINELIQ_API_KEYS="viewer-key-123456:viewer:acme",
            SENTINELIQ_ANONYMOUS_ROLE="viewer",
        )
        self.assertFalse(settings.auth_required)
        self.assertEqual(settings.anonymous_role, "viewer")

    def test_anonymous_disabled_needs_keys(self):
        with self.assertRaises(ConfigError):
            make_settings(".", SENTINELIQ_ANONYMOUS_ROLE="disabled")

    def test_anonymous_viewer_conflicts_with_required_auth(self):
        with self.assertRaises(ConfigError):
            make_settings(
                ".",
                SENTINELIQ_API_KEYS="viewer-key-123456:viewer:acme",
                SENTINELIQ_AUTH_REQUIRED="true",
                SENTINELIQ_ANONYMOUS_ROLE="viewer",
            )

    def test_rejects_unknown_anonymous_role(self):
        with self.assertRaises(ConfigError):
            make_settings(".", SENTINELIQ_ANONYMOUS_ROLE="owner")

    def test_production_profile(self):
        settings = make_settings(".", SENTINELIQ_ENV="production")
        self.assertTrue(settings.production)
        self.assertEqual(settings.log_format, "json")

    def test_invalid_notify_priority(self):
        with self.assertRaises(ConfigError):
            make_settings(".", SENTINELIQ_NOTIFY_MIN_PRIORITY="urgent")


if __name__ == "__main__":
    unittest.main()
