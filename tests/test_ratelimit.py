import unittest

from support import make_settings  # noqa: F401 - ensures src is on sys.path

from ratelimit import TokenBucketLimiter


class RateLimitTests(unittest.TestCase):
    def test_disabled_limiter_allows_everything(self):
        limiter = TokenBucketLimiter(0)
        self.assertFalse(limiter.enabled)
        for _ in range(50):
            self.assertTrue(limiter.check("ip").allowed)

    def test_bucket_exhausts_and_recovers(self):
        now = [0.0]
        limiter = TokenBucketLimiter(60, clock=lambda: now[0])
        results = [limiter.check("key") for _ in range(61)]
        self.assertTrue(all(item.allowed for item in results[:60]))
        self.assertFalse(results[60].allowed)
        self.assertGreaterEqual(results[60].retry_after, 1)
        now[0] += 1.0
        self.assertTrue(limiter.check("key").allowed)

    def test_keys_are_independent(self):
        limiter = TokenBucketLimiter(1)
        self.assertTrue(limiter.check("a").allowed)
        self.assertFalse(limiter.check("a").allowed)
        self.assertTrue(limiter.check("b").allowed)

    def test_reports_remaining(self):
        limiter = TokenBucketLimiter(5)
        first = limiter.check("key")
        self.assertEqual((first.limit, first.remaining), (5, 4))


if __name__ == "__main__":
    unittest.main()
