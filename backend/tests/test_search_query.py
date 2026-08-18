"""The Elasticsearch query the repository builds.

These assert the shape of the outgoing call rather than the reply, because
the shape is where the design lives: routing is why the latency budget works,
and `filter` rather than `must` is why the tenant term is free.
"""
import pytest

from search_service.repositories import search_index


def build(es, tenant="acme", q="refund", page=1, size=20,
          facets=(), fuzzy=True, highlight=True):
    search_index.search(tenant, q, page, size, list(facets), fuzzy, highlight)
    return es.last_search


class TestTenantScoping:
    def test_the_tenant_filter_is_injected_by_the_repository(self, es):
        """Not by the handler. A handler cannot construct an unscoped query
        because building the query is not something handlers do."""
        call = build(es)
        assert call["body"]["query"]["bool"]["filter"] == [
            {"term": {"tenant": "acme"}}]

    def test_the_tenant_term_is_a_filter_not_a_must(self, es):
        """In `must` it would be scored — pointless work on a term that
        matches every candidate — and it would not be cached as a bitset."""
        bool_q = build(es)["body"]["query"]["bool"]
        must_terms = [c for c in bool_q["must"] if "term" in c]
        assert must_terms == []
        assert any("term" in c for c in bool_q["filter"])

    def test_routing_pins_the_query_to_one_shard(self, es):
        """The single biggest reason p95 fits in 500 ms: one shard answers
        instead of all of them."""
        assert build(es, tenant="globex")["routing"] == "globex"

    def test_routing_and_the_filter_always_agree(self, es):
        """Routing alone is an optimisation, not isolation — a document can
        be written to any shard. The filter is what enforces it."""
        call = build(es, tenant="globex")
        assert call["routing"] == "globex"
        assert call["body"]["query"]["bool"]["filter"] == [
            {"term": {"tenant": "globex"}}]


class TestRelevance:
    def test_the_title_is_boosted_over_the_body(self, es):
        mm = build(es)["body"]["query"]["bool"]["must"][0]["multi_match"]
        assert mm["fields"] == ["title^3", "body"]

    def test_the_user_query_goes_in_the_multi_match(self, es):
        mm = build(es, q="annual leave")["body"]["query"]["bool"]["must"][0]["multi_match"]
        assert mm["query"] == "annual leave"

    def test_fuzziness_is_opt_in(self, es):
        on = build(es, fuzzy=True)["body"]["query"]["bool"]["must"][0]["multi_match"]
        off = build(es, fuzzy=False)["body"]["query"]["bool"]["must"][0]["multi_match"]
        assert on["fuzziness"] == "AUTO"
        assert "fuzziness" not in off


class TestPagination:
    @pytest.mark.parametrize("page,size,expected_from", [
        (1, 20, 0), (2, 20, 20), (3, 50, 100), (1, 1, 0)])
    def test_from_is_derived_from_a_one_based_page(self, es, page, size, expected_from):
        call = build(es, page=page, size=size)
        assert call["body"]["from"] == expected_from
        assert call["body"]["size"] == size

    def test_total_hits_tracking_is_capped(self, es):
        """Counting every match exactly costs more than it is worth; "10,000+"
        is what a result page shows anyway."""
        assert build(es)["body"]["track_total_hits"] == 10_000


class TestOptionalClauses:
    def test_highlighting_is_only_requested_when_asked_for(self, es):
        assert "highlight" in build(es, highlight=True)["body"]
        assert "highlight" not in build(es, highlight=False)["body"]

    def test_highlight_returns_one_short_fragment(self, es):
        h = build(es, highlight=True)["body"]["highlight"]["fields"]["body"]
        assert h == {"fragment_size": 140, "number_of_fragments": 1}

    def test_facets_are_namespaced_under_metadata(self, es):
        """Facet names come from the client, so they must never be able to
        address a top-level field — `tenant` as a facet name would aggregate
        across the index."""
        aggs = build(es, facets=["dept", "year"])["body"]["aggs"]
        assert aggs["dept"]["terms"]["field"] == "metadata.dept"
        assert aggs["year"]["terms"]["field"] == "metadata.year"

    def test_a_facet_cannot_escape_the_metadata_prefix(self, es):
        aggs = build(es, facets=["tenant"])["body"]["aggs"]
        assert aggs["tenant"]["terms"]["field"] == "metadata.tenant"

    def test_no_aggs_key_when_no_facets_requested(self, es):
        assert "aggs" not in build(es, facets=[])["body"]

    def test_the_index_comes_from_configuration(self, es, override):
        override(es_index="docs-v2")
        assert build(es)["index"] == "docs-v2"
