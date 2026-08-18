"""The two cache levels, and the property they must never violate.

A cache key of sha1(query) alone would serve one tenant's documents to
another. That is the single most damaging bug this system could have, so it
gets the first test.
"""
import json

import pytest

from common.constants import key_cache_version
from common.exceptions import ValidationFailed
from factories import es_hit, es_result
from search_service.managers import search


@pytest.fixture(autouse=True)
def clear_l1():
    search._l1.clear()
    yield
    search._l1.clear()


def run(tenant="acme", q="refund", page=1, size=20, facets=(),
        fuzzy=True, highlight=True):
    return search.run(tenant, q, page, size, list(facets), fuzzy, highlight)


# ------------------------------------------------------- tenant isolation

class TestTenantIsolation:
    def test_the_same_query_from_two_tenants_uses_different_keys(self, redis):
        a = search._cache_key("acme", "refund", 1, 20, [], True, True)
        b = search._cache_key("globex", "refund", 1, 20, [], True, True)
        assert a != b
        assert a.startswith("q:acme:") and b.startswith("q:globex:")

    def test_one_tenants_cached_result_is_never_served_to_another(self, redis, es):
        """The end-to-end version of the test above: warm the cache as acme,
        then search as globex and assert we went to Elasticsearch instead of
        being handed acme's documents."""
        es.next_result = es_result([es_hit(doc_id="acme-secret")])
        first, status = run(tenant="acme")
        assert status == "MISS"
        assert first["hits"][0]["id"] == "acme-secret"

        es.next_result = es_result([es_hit(doc_id="globex-own")])
        second, status = run(tenant="globex")

        assert status == "MISS", "globex was served from acme's cache entry"
        assert second["hits"][0]["id"] == "globex-own"
        assert len(es.searches) == 2

    def test_the_tenant_is_a_key_prefix_not_a_hashed_component(self, redis):
        """Prefixing rather than hashing keeps the keyspace inspectable — you
        can see at a glance in redis which tenant a key belongs to, and a
        per-tenant flush is a prefix scan rather than impossible."""
        key = search._cache_key("acme", "refund", 1, 20, [], True, True)
        assert key.split(":")[:2] == ["q", "acme"]


# --------------------------------------------------------------- key shape

class TestCacheKey:
    @pytest.mark.parametrize("kwargs", [
        {"q": "different"},
        {"page": 2},
        {"size": 50},
        {"facets": ["dept"]},
        {"fuzzy": False},
        {"highlight": False},
    ])
    def test_anything_that_changes_the_answer_changes_the_key(self, redis, kwargs):
        base = dict(tenant="acme", q="refund", page=1, size=20,
                    facets=[], fuzzy=True, highlight=True)
        assert (search._cache_key(**base)
                != search._cache_key(**{**base, **kwargs}))

    def test_identical_inputs_are_stable_across_calls(self, redis):
        """Nothing per-request leaks in. A request_id or a timestamp in the
        key would make every key unique and the hit rate exactly zero."""
        args = ("acme", "refund", 1, 20, [], True, True)
        assert search._cache_key(*args) == search._cache_key(*args)

    def test_facet_order_does_not_matter(self, redis):
        """Two clients asking for the same facets in a different order are
        asking the same question."""
        a = search._cache_key("acme", "q", 1, 20, ["dept", "year"], True, True)
        b = search._cache_key("acme", "q", 1, 20, ["year", "dept"], True, True)
        assert a == b

    def test_page_is_in_the_key(self, redis):
        """Stated separately because getting this wrong serves page 1 to
        someone asking for page 2 — a subtle, plausible, silent bug."""
        p1 = search._cache_key("acme", "refund", 1, 20, [], True, True)
        p2 = search._cache_key("acme", "refund", 2, 20, [], True, True)
        assert p1 != p2


# -------------------------------------------------------------- versioning

class TestInvalidation:
    def test_the_version_counter_is_part_of_the_key(self, redis):
        before = search._cache_key("acme", "refund", 1, 20, [], True, True)
        redis.incr(key_cache_version("acme"))
        after = search._cache_key("acme", "refund", 1, 20, [], True, True)
        assert before != after
        assert ":0:" in before and ":1:" in after

    def test_an_incr_retires_every_cached_query_for_that_tenant(self, redis, es):
        es.next_result = es_result([es_hit(doc_id="old")])
        run()
        search._l1.clear()                    # simulate a different pod
        assert run()[1] == "HIT"

        redis.incr(key_cache_version("acme"))   # what the indexer does

        search._l1.clear()
        es.next_result = es_result([es_hit(doc_id="new")])
        value, status = run()
        assert status == "MISS"
        assert value["hits"][0]["id"] == "new"

    def test_invalidating_one_tenant_leaves_another_cached(self, redis, es):
        es.next_result = es_result([es_hit()])
        run(tenant="acme")
        run(tenant="globex")
        search._l1.clear()

        redis.incr(key_cache_version("acme"))
        search._l1.clear()

        assert run(tenant="globex")[1] == "HIT"
        assert run(tenant="acme")[1] == "MISS"

    def test_old_keys_are_left_to_expire_rather_than_deleted(self, redis, es):
        """No SCAN, no key enumeration — that is the whole reason the version
        counter exists. The stale entry should still be sitting in redis."""
        es.next_result = es_result([es_hit()])
        run()
        stale = search._cache_key("acme", "refund", 1, 20, [], True, True)
        redis.incr(key_cache_version("acme"))
        assert redis.get(stale) is not None


# ------------------------------------------------------------- the levels

class TestCacheLevels:
    def test_a_cold_query_reaches_elasticsearch_and_is_reported_as_a_miss(self, redis, es):
        es.next_result = es_result([es_hit()])
        _, status = run()
        assert status == "MISS"
        assert len(es.searches) == 1

    def test_l1_serves_the_second_call_without_touching_redis_or_es(self, redis, es):
        es.next_result = es_result([es_hit()])
        run()
        redis.flushall()                 # if L1 works, redis is not consulted
        _, status = run()
        assert status == "HIT"
        assert len(es.searches) == 1

    def test_l2_serves_a_cold_process_and_repopulates_l1(self, redis, es):
        es.next_result = es_result([es_hit()])
        run()
        search._l1.clear()               # a different pod, or a restart

        _, status = run()
        assert status == "HIT"
        assert len(es.searches) == 1, "should have come from redis, not ES"
        assert len(search._l1) == 1, "L2 hit should warm L1"

    def test_l1_entries_expire(self, redis, es, monkeypatch):
        es.next_result = es_result([es_hit()])
        run()
        key = next(iter(search._l1))
        expires, value = search._l1[key]
        search._l1[key] = (expires - 999, value)      # fast-forward past the TTL
        assert search._l1_get(key) is None

    def test_l1_is_bounded(self, redis, es):
        """Unbounded, this is a memory leak with a 5 s TTL that never fires
        for a long tail of unique queries."""
        search._l1.update({f"k{i}": (9e9, {}) for i in range(10_001)})
        search._l1_put("one-more", {})
        assert len(search._l1) == 1

    def test_a_miss_writes_through_to_redis_with_a_ttl(self, redis, es):
        es.next_result = es_result([es_hit()])
        run()
        key = search._cache_key("acme", "refund", 1, 20, [], True, True)
        assert redis.get(key) is not None
        assert 0 < redis.ttl(key) <= 60


# --------------------------------------------------------------- contract

class TestResponseShape:
    def test_hits_are_flattened_for_the_client(self, redis, es):
        es.next_result = es_result([
            es_hit(doc_id="d1", title="Refund Policy", score=8.41,
                   snippet="request <em>refunds</em> today",
                   metadata={"dept": "finance"})])
        value, _ = run()
        assert value["hits"] == [{
            "id": "d1", "score": 8.41, "title": "Refund Policy",
            "snippet": "request <em>refunds</em> today",
            "metadata": {"dept": "finance"}}]

    def test_a_hit_without_a_highlight_has_a_null_snippet(self, redis, es):
        es.next_result = es_result([es_hit(snippet=None)])
        assert run()[0]["hits"][0]["snippet"] is None

    def test_facets_are_reshaped_into_value_count_pairs(self, redis, es):
        es.next_result = es_result(
            [es_hit()],
            aggregations={"dept": {"buckets": [
                {"key": "finance", "doc_count": 12},
                {"key": "legal", "doc_count": 3}]}})
        assert run(facets=["dept"])[0]["facets"] == {
            "dept": [{"value": "finance", "count": 12},
                     {"value": "legal", "count": 3}]}

    def test_no_aggregations_yields_an_empty_dict_not_a_missing_key(self, redis, es):
        es.next_result = es_result([es_hit()])
        assert run()[0]["facets"] == {}

    def test_took_ms_is_reported(self, redis, es):
        es.next_result = es_result([es_hit()])
        assert isinstance(run()[0]["took_ms"], int)

    def test_the_cached_payload_round_trips_through_json(self, redis, es):
        """Whatever is cached must survive json.dumps/loads unchanged, or a
        hit and a miss return different shapes to the client."""
        es.next_result = es_result([es_hit()])
        fresh, _ = run()
        search._l1.clear()
        cached, status = run()
        assert status == "HIT"
        assert json.loads(json.dumps(fresh))["hits"] == cached["hits"]


# ------------------------------------------------------------- pagination

class TestDeepPagination:
    def test_beyond_the_cap_is_rejected(self, redis, es, override):
        """from=10000 makes every shard collect 10,020 hits and throw away
        9,980. Refusing is cheaper than serving it slowly."""
        override(max_page=500)
        with pytest.raises(ValidationFailed) as exc:
            run(page=501)
        assert "cursor" in str(exc.value).lower()
        assert es.searches == []

    def test_the_last_allowed_page_still_works(self, redis, es, override):
        override(max_page=500)
        es.next_result = es_result([es_hit()])
        assert run(page=500)[1] == "MISS"

    def test_rejection_happens_before_any_cache_or_es_work(self, redis, es):
        with pytest.raises(ValidationFailed):
            run(page=99_999)
        assert es.searches == []
        assert search._l1 == {}
