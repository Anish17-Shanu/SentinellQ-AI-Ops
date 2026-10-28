import unittest

from support import make_settings  # noqa: F401 - ensures src is on sys.path

from auth import Authenticator
from errors import AuthError, ConfigError, ForbiddenError

KEYS = (
    "viewer-key-123456:viewer:acme,"
    "responder-key-123456:responder:acme,"
    "admin-key-123456:admin:globex"
)


class AuthenticatorTests(unittest.TestCase):
    def test_demo_identity_without_keys(self):
        auth = Authenticator("", "default", required=False)
        identity = auth.identify({})
        self.assertIsNotNone(identity)
        self.assertEqual(identity.role, "admin")
        self.assertFalse(identity.authenticated)
        self.assertFalse(auth.enabled)

    def test_parses_roles_and_tenants(self):
        auth = Authenticator(KEYS, "default", required=True)
        self.assertTrue(auth.enabled)
        viewer = auth.identify({"x-api-key": "viewer-key-123456"})
        self.assertEqual((viewer.role, viewer.tenant), ("viewer", "acme"))
        admin = auth.identify({"authorization": "Bearer admin-key-123456"})
        self.assertEqual((admin.role, admin.tenant), ("admin", "globex"))
        self.assertTrue(admin.label.startswith("key-"))

    def test_unknown_key_is_unauthenticated(self):
        auth = Authenticator(KEYS, "default", required=True)
        self.assertIsNone(auth.identify({"x-api-key": "wrong-key-000000"}))
        with self.assertRaises(AuthError):
            auth.require(None, "viewer")

    def test_role_hierarchy(self):
        auth = Authenticator(KEYS, "default", required=True)
        viewer = auth.identify({"x-api-key": "viewer-key-123456"})
        admin = auth.identify({"x-api-key": "admin-key-123456"})
        self.assertIs(auth.require(viewer, "viewer"), viewer)
        with self.assertRaises(ForbiddenError):
            auth.require(viewer, "responder")
        with self.assertRaises(ConfigError):
            auth.require(admin, "owner")
        self.assertIs(auth.require(admin, "admin"), admin)

    def test_rejects_short_keys(self):
        with self.assertRaises(ConfigError):
            Authenticator("short:viewer:acme", "default", required=True)

    def test_rejects_bad_role(self):
        with self.assertRaises(ConfigError):
            Authenticator("long-enough-key:owner:acme", "default", required=True)

    def test_rejects_bad_tenant(self):
        with self.assertRaises(ConfigError):
            Authenticator("long-enough-key:viewer:bad tenant!", "default", required=True)


if __name__ == "__main__":
    unittest.main()
