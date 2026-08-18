"""The gateway hop.

The header allowlist is the first isolation layer, and it is the one that
fails open if it is written as a denylist — so these tests are mostly about
what does *not* get forwarded.
"""
import pytest
import requests
from flask import Flask, g

from common.constants import H_AUTH, H_REQUEST, H_SESSION, H_TENANT, H_USER
from common.context import RequestContext, set_context
from common.exceptions import DependencyDown, NotFound
from gateway.handlers import proxy


class FakeUpstream:
    """Stands in for requests.request; records the outgoing call."""

    def __init__(self, status=200, body=b'{"ok":true}', headers=None, raises=None):
        self.status, self.body = status, body
        self.headers = headers or {"Content-Type": "application/json"}
        self.raises = raises
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return type("Resp", (), {"status_code": self.status, "content": self.body,
                                 "headers": self.headers})()

    @property
    def sent(self):
        return self.calls[-1]["headers"]


@pytest.fixture
def gateway(monkeypatch):
    """A request context standing in for a verified token."""
    app = Flask(__name__)
    upstream = FakeUpstream()
    monkeypatch.setattr(proxy.requests, "request", upstream)
    monkeypatch.setattr(proxy.routing, "resolve", lambda m, p: "http://api:8081")

    def run(path="/documents", method="GET", headers=None, tenant="acme"):
        with app.test_request_context(path, method=method, headers=headers or {}):
            set_context(RequestContext(
                request_id="r-gateway", tenant=tenant, tenant_id="t-1",
                user_id="u-1", session_id="s-1", rate_limit_rpm=100))
            g.request_id = "r-gateway"
            return proxy.forward()

    run.upstream = upstream
    return run


# ------------------------------------------------------- identity spoofing

class TestIdentityCannotBeForged:
    def test_an_inbound_tenant_header_is_replaced_not_merged(self, gateway):
        """The whole isolation model rests on this. A caller sending
        X-Tenant: victim must not reach a service with it — the header is
        rebuilt from the verified token, not edited."""
        gateway(headers={H_TENANT: "victim-tenant"})
        assert gateway.upstream.sent[H_TENANT] == "acme"

    @pytest.mark.parametrize("header", [H_TENANT, H_USER, H_SESSION])
    def test_every_identity_header_comes_from_the_context(self, gateway, header):
        gateway(headers={header: "attacker-supplied"})
        assert gateway.upstream.sent[header] != "attacker-supplied"

    def test_case_variations_do_not_slip_through(self, gateway):
        """HTTP headers are case-insensitive, so a denylist keyed on the
        exact spelling would miss `x-tenant`. An allowlist cannot."""
        gateway(headers={"x-tenant": "victim", "X-TENANT": "victim"})
        assert gateway.upstream.sent[H_TENANT] == "acme"

    def test_an_arbitrary_header_is_not_forwarded(self, gateway):
        gateway(headers={"X-Admin": "true", "X-Debug": "1", "Cookie": "sid=x"})
        for name in ("X-Admin", "X-Debug", "Cookie"):
            assert name not in gateway.upstream.sent

    def test_the_authorization_header_is_passed_through_untouched(self, gateway):
        """Services verify it again rather than trusting the injected tenant,
        so a second ingress path cannot hand them a forged identity."""
        gateway(headers={H_AUTH: "Bearer the.original.token"})
        assert gateway.upstream.sent[H_AUTH] == "Bearer the.original.token"


# -------------------------------------------------------------- allowlist

class TestResponseAffectingHeaders:
    @pytest.mark.parametrize("name,value", [
        ("Accept", "application/pdf"),
        ("Accept-Language", "en-GB"),
        ("Range", "bytes=0-1023"),
        ("If-None-Match", '"abc123"'),
    ])
    def test_headers_that_change_the_response_survive_the_hop(
            self, gateway, name, value):
        """Accept in particular: /documents/{id}/raw content-negotiates on
        it, and dropping it here made downloads return JSON instead of the
        file."""
        gateway(headers={name: value})
        assert gateway.upstream.sent[name] == value

    def test_an_absent_optional_header_is_not_invented(self, gateway):
        gateway()
        assert "Range" not in gateway.upstream.sent


# ------------------------------------------------------------ correlation

class TestTracePropagation:
    def test_the_request_id_goes_downstream(self, gateway):
        gateway()
        assert gateway.upstream.sent[H_REQUEST] == "r-gateway"

    def test_the_request_id_comes_back_on_the_response(self, gateway):
        """So a client can quote it in a bug report and the whole request is
        recoverable from the logs."""
        assert gateway().headers[H_REQUEST] == "r-gateway"

    def test_the_gateways_id_wins_over_an_inbound_one(self, gateway):
        gateway(headers={H_REQUEST: "r-client-supplied"})
        assert gateway.upstream.sent[H_REQUEST] == "r-gateway"


# ------------------------------------------------------------- forwarding

class TestForwarding:
    def test_redirects_are_not_followed(self, gateway):
        """A 302 to a presigned S3 URL *is* the answer. Following it would
        stream 200 MB through the gateway."""
        assert gateway.upstream.calls == [] or True
        gateway()
        assert gateway.upstream.calls[-1]["allow_redirects"] is False

    def test_a_302_reaches_the_client_intact(self, gateway):
        gateway.upstream.status = 302
        gateway.upstream.headers = {"Location": "https://s3.test/acme/x/raw?sig=abc"}
        response = gateway()
        assert response.status_code == 302
        assert response.headers["Location"].startswith("https://s3.test/")

    def test_hop_by_hop_headers_are_stripped_from_the_response(self, gateway):
        """Forwarding Transfer-Encoding from upstream produces a response
        that contradicts the body the gateway actually sends."""
        gateway.upstream.headers = {"Transfer-Encoding": "chunked",
                                    "Connection": "keep-alive",
                                    "Content-Type": "application/json"}
        response = gateway()
        assert "Transfer-Encoding" not in response.headers
        assert "Connection" not in response.headers

    def test_an_unroutable_path_is_404(self, gateway, monkeypatch):
        monkeypatch.setattr(proxy.routing, "resolve", lambda m, p: None)
        with pytest.raises(NotFound):
            gateway(path="/nope")

    def test_a_timeout_becomes_503_not_500(self, gateway):
        """An upstream that is slow is a dependency problem, and the status
        code should say so — a 500 sends the on-call to the wrong service."""
        gateway.upstream.raises = requests.Timeout()
        with pytest.raises(DependencyDown):
            gateway()

    def test_a_connection_failure_becomes_503(self, gateway):
        gateway.upstream.raises = requests.ConnectionError()
        with pytest.raises(DependencyDown):
            gateway()


class TestRateLimitHeaders:
    def test_the_remaining_allowance_is_reported(self, gateway, monkeypatch):
        app = Flask(__name__)
        with app.test_request_context("/documents"):
            set_context(RequestContext(request_id="r-1", tenant="acme",
                                       tenant_id="t-1", user_id="u-1",
                                       session_id="s-1", rate_limit_rpm=100))
            g.request_id = "r-1"
            g.rate_remaining = 97
            response = proxy.forward()
        assert response.headers["X-RateLimit-Limit"] == "100"
        assert response.headers["X-RateLimit-Remaining"] == "97"
