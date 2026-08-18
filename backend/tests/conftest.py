"""Shared fixtures.

Every external dependency is replaced by an in-process double, so the suite
runs with nothing started — no docker, no network. The doubles are
deliberately thin: they record what was asked of them, because most of what
is worth asserting here is *the shape of the call*, not the reply.
"""
import sys
from pathlib import Path

import fakeredis
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import config  # noqa: E402
from common.clients import elastic, redis_client, s3  # noqa: E402


# --------------------------------------------------------------------- redis

@pytest.fixture
def redis(monkeypatch):
    """decode_responses=True matches the real client — without it every
    cached value comes back as bytes and json.loads quietly still works,
    which would hide a real mismatch."""
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_client, "_client", fake)
    return fake


# ------------------------------------------------------------------ elastic

class FakeES:
    """Records calls; replies with whatever was queued."""

    def __init__(self):
        self.searches, self.indexed, self.deleted = [], [], []
        self.next_result = _empty_result()
        self.index_raises = None
        self.delete_raises = None

    def search(self, **kwargs):
        self.searches.append(kwargs)
        return self.next_result

    def index(self, **kwargs):
        if self.index_raises:
            raise self.index_raises
        self.indexed.append(kwargs)

    def delete(self, **kwargs):
        if self.delete_raises:
            raise self.delete_raises
        self.deleted.append(kwargs)

    # convenience for assertions
    @property
    def last_search(self):
        return self.searches[-1]

    @property
    def last_query(self):
        return self.searches[-1]["body"]["query"]["bool"]


def _empty_result():
    return {"hits": {"total": {"value": 0}, "hits": []}}


@pytest.fixture
def es(monkeypatch):
    fake = FakeES()
    monkeypatch.setattr(elastic, "client", lambda: fake)
    return fake


# ----------------------------------------------------------------------- s3

class FakeS3:
    """A dict with an ordered call log, so ordering rules can be asserted."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []

    def put(self, key, data, content_type="text/plain"):
        self.calls.append(("put", key))
        self.objects[key] = data

    def get(self, key):
        self.calls.append(("get", key))
        if key not in self.objects:
            raise KeyError(key)          # stands in for botocore NoSuchKey
        return self.objects[key]

    def presigned_url(self, key):
        self.calls.append(("presign", key))
        return f"https://s3.test/{key}?X-Amz-Signature=deadbeef"


@pytest.fixture
def fake_s3(monkeypatch):
    fake = FakeS3()
    for name in ("put", "get", "presigned_url"):
        monkeypatch.setattr(s3, name, getattr(fake, name))
    return fake


# ----------------------------------------------------------------- settings

@pytest.fixture
def override(monkeypatch):
    """settings() is lru_cached, so a test that needs a different tunable
    has to replace the cached object rather than the environment."""
    def _apply(**kwargs):
        current = config.settings()
        patched = current.model_copy(update=kwargs)
        monkeypatch.setattr(config, "settings", lambda: patched)
        for module in _SETTINGS_IMPORTERS:
            monkeypatch.setattr(module, "settings", lambda: patched, raising=False)
        return patched
    return _apply


def _settings_importers():
    from common.clients import s3 as s3mod
    from document_service.managers import documents as docmgr
    from index_service.managers import documents as idxmgr
    from indexer.managers import consumer
    from search_service.managers import search as searchmgr
    from search_service.repositories import search_index
    return (s3mod, docmgr, idxmgr, consumer, searchmgr, search_index)


_SETTINGS_IMPORTERS = _settings_importers()


# -------------------------------------------------------------------- misc

class RecordingLogger:
    def __init__(self):
        self.records: list[tuple[str, str, dict]] = []

    def _log(self, level):
        def fn(msg, extra=None, **_):
            self.records.append((level, str(msg), extra or {}))
        return fn

    def __getattr__(self, name):
        return self._log(name)

    def messages(self, level=None):
        return [m for lvl, m, _ in self.records if level in (None, lvl)]


@pytest.fixture
def logger():
    return RecordingLogger()
