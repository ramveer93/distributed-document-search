"""Per-tenant rate limiting.

Fixed window: one INCR plus one EXPIRE. A sliding window is more accurate but
needs a sorted set per tenant, which is not worth it for fairness limiting.
"""
import pytest

from common.constants import key_rate_limit
from common.exceptions import RateLimited
from gateway.managers import rate_limit


class TestAllowance:
    def test_the_first_request_consumes_one(self, redis):
        assert rate_limit.check("acme", 10) == 9

    def test_the_allowance_counts_down(self, redis):
        assert [rate_limit.check("acme", 5) for _ in range(5)] == [4, 3, 2, 1, 0]

    def test_the_request_that_hits_the_limit_exactly_is_allowed(self, redis):
        for _ in range(9):
            rate_limit.check("acme", 10)
        assert rate_limit.check("acme", 10) == 0

    def test_one_past_the_limit_is_rejected(self, redis):
        for _ in range(10):
            rate_limit.check("acme", 10)
        with pytest.raises(RateLimited):
            rate_limit.check("acme", 10)

    def test_a_limit_of_zero_rejects_everything(self, redis):
        with pytest.raises(RateLimited):
            rate_limit.check("acme", 0)


class TestTenantIsolation:
    def test_tenants_have_separate_counters(self, redis):
        """A noisy tenant must not be able to exhaust anyone else's budget —
        which is the entire reason the limit is per tenant rather than global."""
        for _ in range(10):
            rate_limit.check("noisy", 10)
        with pytest.raises(RateLimited):
            rate_limit.check("noisy", 10)

        assert rate_limit.check("quiet", 10) == 9

    def test_tenants_can_have_different_limits(self, redis):
        """The limit comes from the tenant row, so a plan upgrade is a
        database change rather than a deploy."""
        assert rate_limit.check("free", 5) == 4
        assert rate_limit.check("enterprise", 1000) == 999


class TestWindow:
    def test_the_key_is_scoped_to_the_current_minute(self, redis):
        rate_limit.check("acme", 10)
        keys = redis.keys("rl:acme:*")
        assert len(keys) == 1
        minute = keys[0].split(":")[-1]
        assert len(minute) == 12 and minute.isdigit()

    def test_the_counter_expires_so_it_cannot_accumulate(self, redis):
        """Without the EXPIRE, redis fills with one dead key per tenant per
        minute, forever."""
        rate_limit.check("acme", 10)
        key = redis.keys("rl:acme:*")[0]
        assert 0 < redis.ttl(key) <= 60

    def test_a_new_window_starts_fresh(self, redis, monkeypatch):
        for _ in range(10):
            rate_limit.check("acme", 10)
        with pytest.raises(RateLimited):
            rate_limit.check("acme", 10)

        monkeypatch.setattr(rate_limit.time, "strftime",
                            lambda fmt: "999912312359" if "%M" in fmt else "00")
        assert rate_limit.check("acme", 10) == 9


class TestRejection:
    def test_the_error_tells_the_client_when_to_come_back(self, redis):
        """A 429 without Retry-After leaves a client guessing, and most guess
        by retrying immediately."""
        with pytest.raises(RateLimited) as exc:
            rate_limit.check("acme", 0)
        assert 0 <= exc.value.retry_after <= 60
        assert exc.value.status == 429

    def test_the_error_reports_the_limit_that_was_hit(self, redis):
        with pytest.raises(RateLimited) as exc:
            rate_limit.check("acme", 0)
        assert exc.value.limit == 0
        assert "requests/min" in str(exc.value)

    def test_rejected_requests_still_count(self, redis):
        """Deliberate: a client hammering past its limit does not get its
        window reset by the rejections, so backing off is the only way out."""
        rate_limit.check("acme", 1)
        for _ in range(3):
            with pytest.raises(RateLimited):
                rate_limit.check("acme", 1)
        assert int(redis.get(redis.keys("rl:acme:*")[0])) == 4


class TestKeyNaming:
    def test_the_key_is_namespaced(self, redis):
        """rl: keeps the counters distinguishable from q: and inv: in the
        same keyspace — which matters the first time someone runs KEYS in
        production."""
        assert key_rate_limit("acme", "202601011200") == "rl:acme:202601011200"
