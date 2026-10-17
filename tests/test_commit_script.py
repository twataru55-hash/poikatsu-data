"""Exercise the save script against disposable local Git repositories only."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(os.environ.get("PK_COMMIT_SCRIPT", Path(__file__).parents[1] / "scripts" / "commit.sh")).resolve()
BASH = (r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash"))


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    root = tmp_path / "checkout"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Local Test")
    git(root, "config", "user.email", "test@example.invalid")
    for name in ("data", "dist", "reports", "master"):
        (root / name).mkdir()
        (root / name / "test.txt").write_text("before", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-u", "origin", "main")
    return root, remote


def run(root):
    return subprocess.run([BASH, str(SCRIPT), "test save"], cwd=root,
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_missing_inbox_saves_and_pushes(repo):
    root, remote = repo
    (root / "data/test.txt").write_text("recovered", encoding="utf-8")
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert git(remote, "show", "main:data/test.txt") == "recovered"
    assert git(root, "status", "--porcelain") == ""


def test_deleted_tracked_inbox_is_saved(repo):
    root, remote = repo
    (root / "inbox").mkdir()
    (root / "inbox/answer.json").write_text("{}", encoding="utf-8")
    git(root, "add", "inbox")
    git(root, "commit", "-m", "answer")
    git(root, "push")
    (root / "inbox/answer.json").unlink()
    (root / "inbox").rmdir()
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert git(remote, "ls-tree", "-r", "--name-only", "main", "inbox") == ""


def test_new_inbox_is_saved_but_work_is_not(repo):
    root, remote = repo
    (root / "inbox").mkdir()
    (root / "inbox/answer.json").write_text("{}", encoding="utf-8")
    (root / "work").mkdir()
    (root / "work/body.txt").write_text("local only", encoding="utf-8")
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert git(remote, "show", "main:inbox/answer.json") == "{}"
    assert git(remote, "ls-tree", "-r", "--name-only", "main", "work") == ""


def test_no_changes_does_not_create_commit(repo):
    root, remote = repo
    before = git(root, "rev-parse", "HEAD")
    assert run(root).returncode == 0
    assert git(remote, "rev-parse", "main") == before


def test_staging_error_is_not_success(repo):
    root, remote = repo
    before = git(remote, "rev-parse", "main")
    (root / "data/test.txt").write_text("changed", encoding="utf-8")
    (root / ".git/index.lock").write_text("test lock", encoding="utf-8")
    assert run(root).returncode != 0
    assert git(remote, "rev-parse", "main") == before


def test_commit_error_is_not_success(repo):
    root, remote = repo
    before = git(remote, "rev-parse", "main")
    (root / "data/test.txt").write_text("changed", encoding="utf-8")
    (root / "inbox").mkdir()
    (root / "inbox/answer.json").write_text("{}", encoding="utf-8")
    hook = root / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    assert run(root).returncode != 0
    assert git(remote, "rev-parse", "main") == before
