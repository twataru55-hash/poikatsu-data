"""Read-only pagination coverage. All GitHub responses are fake; no network or Issue mutations."""
import pytest
from holds import GitHub


def make_github(monkeypatch, pages):
    gh = GitHub.__new__(GitHub)
    gh.enabled = True
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        page = int(path.split("&page=")[-1]) if "&page=" in path else 1
        response = pages[page - 1]
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(gh, "_req", request)
    return gh, calls


def test_empty_first_page_stops(monkeypatch):
    gh, calls = make_github(monkeypatch, [[]])
    assert gh.list_issues("hold") == []
    assert len(calls) == 1


def test_146_issues_reads_second_page(monkeypatch):
    expected = [{"number": i} for i in range(1, 147)]
    gh, calls = make_github(monkeypatch, [expected[:100], expected[100:]])
    assert gh.list_issues("hold") == expected
    assert calls[-1][1] == "/issues?labels=hold&state=open&per_page=100&page=2"


def test_exact_200_issues_reads_empty_third_page(monkeypatch):
    expected = [{"number": i} for i in range(1, 201)]
    gh, calls = make_github(monkeypatch, [expected[:100], expected[100:], []])
    assert gh.list_issues("hold", state="closed") == expected
    assert calls[-1][1] == "/issues?labels=hold&state=closed&per_page=100&page=3"


def test_second_page_failure_is_not_partial_success(monkeypatch):
    gh, calls = make_github(monkeypatch, [
        [{"number": i} for i in range(100)], RuntimeError("Fake second-page error")
    ])
    with pytest.raises(RuntimeError, match="Fake second-page error"):
        gh.list_issues("hold")
    assert len(calls) == 2
