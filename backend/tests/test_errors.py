"""RFC 7807 error responses.

One place builds them, so every service errors identically and every error
carries the trace id that ties it back to the logs.
"""
import pytest
from flask import Flask, g

from common import problem
from common.constants import H_REQUEST
from common.exceptions import (AppError, DependencyDown, Forbidden, NotFound,
                               PayloadTooLarge, RateLimited, Unauthorized,
                               ValidationFailed)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    return app


@pytest.fixture
def client(app):
    from conftest import RecordingLogger
    problem.register_error_handlers(app, RecordingLogger())
    return app


class TestStatusMapping:
    @pytest.mark.parametrize("exc,status,type_", [
        (ValidationFailed, 422, "/errors/validation"),
        (Unauthorized,     401, "/errors/unauthorized"),
        (Forbidden,        403, "/errors/forbidden"),
        (NotFound,         404, "/errors/not-found"),
        (PayloadTooLarge,  413, "/errors/too-large"),
        (DependencyDown,   503, "/errors/dependency"),
        (AppError,         500, "/errors/internal"),
    ])
    def test_each_domain_error_has_one_status(self, app, exc, status, type_):
        """Handlers never build status codes by hand, so the mapping lives
        here and nowhere else."""
        with app.test_request_context("/"):
            response = problem.problem(exc("something"))
        assert response.status_code == status
        assert response.get_json()["type"] == type_


class TestResponseShape:
    def test_the_media_type_is_problem_json(self, app):
        """application/json would let a client mistake an error for a
        successful payload of the same shape."""
        with app.test_request_context("/"):
            assert problem.problem(NotFound()).mimetype == "application/problem+json"

    def test_the_body_carries_type_title_and_status(self, app):
        with app.test_request_context("/"):
            body = problem.problem(ValidationFailed("page must be <= 500")).get_json()
        assert body["title"] == "Validation failed"
        assert body["status"] == 422
        assert body["detail"] == "page must be <= 500"

    def test_detail_is_omitted_when_there_is_nothing_to_add(self, app):
        with app.test_request_context("/"):
            assert "detail" not in problem.problem(NotFound()).get_json()


class TestTraceCorrelation:
    def test_the_trace_id_comes_from_the_request_context(self, app):
        """This is what makes an error report actionable — paste the id into
        Loki and the whole request is there, including the indexer's half."""
        with app.test_request_context("/"):
            g.request_id = "r-abc123"
            assert problem.problem(NotFound()).get_json()["trace_id"] == "r-abc123"

    def test_it_falls_back_to_the_inbound_header(self, app):
        """Errors raised before the middleware has run still need to be
        traceable."""
        with app.test_request_context("/", headers={H_REQUEST: "r-upstream"}):
            assert problem.problem(NotFound()).get_json()["trace_id"] == "r-upstream"

    def test_a_missing_trace_id_is_null_not_a_crash(self, app):
        with app.test_request_context("/"):
            assert problem.problem(NotFound()).get_json()["trace_id"] is None


class TestRateLimitHeaders:
    def test_a_429_tells_the_client_when_to_retry(self, app):
        with app.test_request_context("/"):
            response = problem.problem(RateLimited("too many", retry_after=42, limit=100))
        assert response.headers["Retry-After"] == "42"
        assert response.headers["X-RateLimit-Limit"] == "100"
        assert response.headers["X-RateLimit-Remaining"] == "0"

    def test_other_errors_carry_no_rate_limit_headers(self, app):
        with app.test_request_context("/"):
            assert "Retry-After" not in problem.problem(NotFound()).headers


class TestInformationLeaks:
    def test_an_unhandled_exception_does_not_expose_internals(self, client):
        """The message, the stack trace and the driver name all stay in the
        logs. What reaches the client is a generic 500."""
        @client.route("/boom")
        def boom():
            raise RuntimeError(
                "psycopg2.OperationalError: password authentication failed "
                "for user 'deeprunner' at 10.0.3.14:5432")

        response = client.test_client().get("/boom")
        body = response.get_data(as_text=True)

        assert response.status_code == 500
        for leak in ("psycopg2", "password", "10.0.3.14", "deeprunner"):
            assert leak not in body

    def test_an_unknown_route_is_a_normal_404(self, client):
        response = client.test_client().get("/no-such-route")
        assert response.status_code == 404
        assert response.mimetype == "application/problem+json"
        assert response.get_json()["type"] == "/errors/not-found"

    def test_not_found_is_documented_as_the_cross_tenant_answer(self):
        """Guards the reasoning as much as the code: if someone ever
        "corrects" this to 403, the leak comes back."""
        assert NotFound.status == 404
        assert "403" in NotFound.__doc__ and "enumeration" in NotFound.__doc__


class TestDomainErrors:
    def test_the_detail_defaults_to_the_title(self, app):
        assert str(NotFound()) == "Not found"

    def test_rate_limited_defaults_are_sane(self):
        exc = RateLimited()
        assert exc.retry_after == 60 and exc.limit == 0

    def test_every_domain_error_is_an_apperror(self):
        """The single errorhandler registration depends on it — an exception
        outside the hierarchy becomes an untyped 500."""
        for exc in (ValidationFailed, Unauthorized, Forbidden, NotFound,
                    RateLimited, PayloadTooLarge, DependencyDown):
            assert issubclass(exc, AppError)
