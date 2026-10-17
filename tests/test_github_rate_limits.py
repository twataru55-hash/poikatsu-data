"""Rate-limit recovery uses fake HTTP, clock and a temporary hold ledger."""
import json
from types import SimpleNamespace
import pytest
import requests
import holds


def setup_client(monkeypatch, responses):
    clock = {"now": 0.0, "sleeps": [], "calls": []}
    def sleep(seconds):
        clock["sleeps"].append(seconds)
        clock["now"] += seconds
    def request(method, url, **kwargs):
        clock["calls"].append((method, clock["now"], kwargs.get("json")))
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(holds.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(holds.time, "time", lambda: clock["now"])
    monkeypatch.setattr(holds.time, "sleep", sleep)
    monkeypatch.setattr(holds.requests, "request", request)
    gh = holds.GitHub()
    gh.enabled = True
    gh.repo = "example/repo"
    gh.token = "fake-test-token"
    return gh, clock


def response(code=201, message="", headers=None, number=1):
    body = {"message": message} if code >= 300 else {"number": number}
    return SimpleNamespace(status_code=code, text=json.dumps(body),
                           headers=headers or {}, json=lambda: body)


def limited(**kwargs):
    return response(403, "You have exceeded a secondary rate limit", **kwargs)


def test_writes_are_spaced_but_reads_are_not_delayed(monkeypatch):
    gh, clock = setup_client(monkeypatch, [response() for _ in range(4)])
    gh._req("POST", "/issues")
    gh._req("GET", "/issues")
    gh._req("PATCH", "/issues/1")
    gh._req("DELETE", "/issues/1/labels/test")
    assert [c[1] for c in clock["calls"]] == [0, 0, 8, 16]


def test_rejected_issue_retries_same_body_once_then_saves_and_deduplicates(monkeypatch, tmp_path):
    gh, clock = setup_client(monkeypatch, [limited(), response(number=940)])
    monkeypatch.setattr(holds, "DATA_DIR", tmp_path)
    monkeypatch.setattr(holds, "hold_body", lambda *args: "candidate evidence")
    candidate = {"id": "test-1", "title": "test"}
    assert holds.file_hold(gh, candidate, ["V02"], None)
    assert not holds.file_hold(gh, candidate, ["V02"], None)
    assert len(clock["calls"]) == 2
    assert clock["calls"][0][2] == clock["calls"][1][2]
    assert clock["sleeps"] == [60]
    assert json.loads((tmp_path / "holds.json").read_text())["test-1"]["issue"] == 940


@pytest.mark.parametrize("code,message", [
    (401, "Bad credentials"), (403, "Resource not accessible by integration"),
    (403, "Forbidden"), (422, "Validation failed"), (500, "Internal Server Error"),
])
def test_non_rate_errors_never_retry(monkeypatch, code, message):
    gh, clock = setup_client(monkeypatch, [response(code, message)])
    with pytest.raises(RuntimeError):
        gh._req("POST", "/issues")
    assert len(clock["calls"]) == 1
    assert clock["sleeps"] == []


@pytest.mark.parametrize("error", [requests.Timeout("unknown result"),
                                  requests.ConnectionError("unknown result")])
def test_ambiguous_write_never_retries(monkeypatch, error):
    gh, clock = setup_client(monkeypatch, [error])
    with pytest.raises(type(error)):
        gh._req("POST", "/issues")
    assert len(clock["calls"]) == 1


@pytest.mark.parametrize("headers,expected", [
    ({"retry-after": "90"}, 90),
    ({"retry-after": "5"}, 60),
    ({"retry-after": "Thu, 01 Jan 1970 00:02:00 GMT"}, 120),
    ({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "120"}, 121),
])
def test_server_wait_is_respected(monkeypatch, headers, expected):
    gh, clock = setup_client(monkeypatch, [limited(headers=headers), response()])
    gh._req("POST", "/issues")
    assert clock["sleeps"] == [expected]


@pytest.mark.parametrize("headers", [
    {"retry-after": "601"}, {"retry-after": "invalid"}, {"retry-after": "nan"},
    {"x-ratelimit-remaining": "0"},
    {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "900"},
])
def test_unsupported_or_excessive_wait_stops_without_early_retry(monkeypatch, headers):
    gh, clock = setup_client(monkeypatch, [limited(headers=headers)])
    with pytest.raises(RuntimeError):
        gh._req("POST", "/issues")
    assert len(clock["calls"]) == 1
    assert clock["sleeps"] == []


def test_retries_are_bounded(monkeypatch, tmp_path):
    gh, clock = setup_client(monkeypatch, [limited() for _ in range(4)])
    monkeypatch.setattr(holds, "DATA_DIR", tmp_path)
    monkeypatch.setattr(holds, "hold_body", lambda *args: "evidence")
    with pytest.raises(RuntimeError):
        holds.file_hold(gh, {"id": "not-created", "title": "test"}, ["V02"], None)
    assert len(clock["calls"]) == 4
    assert clock["sleeps"] == [60, 120, 240]
    assert not (tmp_path / "holds.json").exists()


def test_total_wait_budget_is_bounded(monkeypatch):
    gh, clock = setup_client(monkeypatch, [limited(headers={"retry-after": "300"}) for _ in range(3)])
    with pytest.raises(RuntimeError):
        gh._req("POST", "/issues")
    assert clock["sleeps"] == [300, 300]
    assert len(clock["calls"]) == 3


def test_429_rejection_recovers(monkeypatch):
    gh, clock = setup_client(monkeypatch, [response(429, "Too many requests"), response()])
    assert gh._req("POST", "/issues")["number"] == 1
    assert clock["sleeps"] == [60]

