"""Tests for the --changed-from scope of the test-quality analyzer.

Each test builds a small git repository in a temporary folder, commits a base
state, changes some files, and runs the resolver or the command line there.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import json  # Read the baseline that the seed run writes.
import shutil  # Find git and copy the analyzer fixture.
import subprocess  # Build the temporary repository with real git calls.
from pathlib import Path  # Filesystem primitives for hermetic paths.
from typing import Any  # Loose signature for the subprocess doubles.

import pytest  # Fixture primitives.

from misthelper_devtools.test_quality_analyzer import changed_scope  # Module under test, for monkeypatching.
from misthelper_devtools.test_quality_analyzer.__main__ import TestQualityCLI, main  # CLI entrypoint under test.
from misthelper_devtools.test_quality_analyzer.changed_scope import ChangedScopeError, ChangedScopeResolver

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="These tests need git on PATH.")

_BASELINE = ".github/test-quality-baseline.json"  # The default baseline path of the analyzer.
_CLEAN_TEST = '''"""A test module with one direct assertion."""


def test_sum_of_two_numbers() -> None:
    """The sum of 2 and 3 is 5."""
    assert sum([2, 3]) == 5
'''


def _git(repository: Path, *args: str) -> None:
    """Run one git command in the temporary repository, without user hooks or signing."""
    hooks = repository.parent / "no-hooks"  # An empty hooks folder keeps global hooks out of the test.
    hooks.mkdir(exist_ok=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={hooks.as_posix()}",
            *args,
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        timeout=60,
    )


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a repository with two committed test files, a baseline, and the tag "base"."""
    root = tmp_path / "repository"
    (root / "tests").mkdir(parents=True)
    (root / ".github").mkdir()
    (root / "tests" / "test_alpha.py").write_text(_CLEAN_TEST, encoding="utf-8")
    (root / "tests" / "test_beta.py").write_text(_CLEAN_TEST, encoding="utf-8")
    (root / _BASELINE).write_text("[]\n", encoding="utf-8")
    (root / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "tag", "base")
    monkeypatch.chdir(root)  # git diff --relative and the analyzer read paths from here.
    return root


def _commit(repository: Path, message: str) -> None:
    """Commit every change in the repository."""
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", message)


def _append_comment(path: Path) -> None:
    """Change a file without moving any line that a finding can name."""
    path.write_text(path.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")


_API_CLIENT_TEST = '''"""A test module that imports the Mist API client at module scope."""

import mistapi


def test_client_module_name() -> None:
    """The client module keeps its name."""
    assert mistapi.__name__ == "mistapi"
'''


def _add_api_client_test(repository: Path) -> None:
    """Write an API-client test module, which the Mist-API predicate excludes."""
    (repository / "tests" / "test_api_client.py").write_text(_API_CLIENT_TEST, encoding="utf-8")


@pytest.fixture
def no_source_scope_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the full-scope guard, because the temporary repository has no module that a detector measures."""
    monkeypatch.setattr(TestQualityCLI, "_zero_detector_scope_metric", lambda *_args, **_kwargs: None)


class TestChangedScopeResolver:
    """The resolver selects the scan scope from the git diff."""

    def test_changed_test_files_are_the_scope(self, repository: Path) -> None:
        _append_comment(repository / "tests" / "test_alpha.py")
        (repository / "tests" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")  # Not a test file.
        (repository / "notes.md").write_text("text\n", encoding="utf-8")  # Not a Python file.
        _commit(repository, "change")

        scope = ChangedScopeResolver("base", [_BASELINE]).resolve()

        assert scope.test_files == ("tests/test_alpha.py",)
        assert set(scope.changed_files) == {"tests/test_alpha.py", "tests/helper.py", "notes.md"}
        assert scope.scans_every_root is False

    def test_a_deleted_test_file_leaves_the_scope(self, repository: Path) -> None:
        (repository / "tests" / "test_beta.py").unlink()
        _commit(repository, "delete")

        scope = ChangedScopeResolver("base", [_BASELINE]).resolve()

        assert scope.changed_files == ("tests/test_beta.py",)  # git reports the deletion.
        assert scope.test_files == ()  # The scan cannot read a file that is gone.

    def test_a_changed_trigger_path_scans_every_root(self, repository: Path) -> None:
        _append_comment(repository / "tests" / "test_alpha.py")
        (repository / "requirements-dev.txt").write_text("pytest>=8\n", encoding="utf-8")
        _commit(repository, "bump")

        scope = ChangedScopeResolver("base", ["./requirements-dev.txt"]).resolve()  # A "./" prefix still matches.

        assert scope.full_gate_trigger == "requirements-dev.txt"
        assert scope.scans_every_root is True

    def test_an_absolute_trigger_path_in_the_checkout_matches(self, repository: Path) -> None:
        (repository / _BASELINE).write_text("[]\n\n", encoding="utf-8")
        _commit(repository, "baseline")

        scope = ChangedScopeResolver("base", [str(repository / _BASELINE)]).resolve()

        assert scope.full_gate_trigger == _BASELINE

    def test_a_trigger_path_outside_the_checkout_is_ignored(self, repository: Path, tmp_path: Path) -> None:
        _append_comment(repository / "tests" / "test_alpha.py")
        _commit(repository, "change")

        scope = ChangedScopeResolver("base", [str(tmp_path / "config.toml"), ""]).resolve()

        assert scope.scans_every_root is False
        assert scope.test_files == ("tests/test_alpha.py",)

    def test_an_unknown_revision_raises_a_named_error(self, repository: Path) -> None:
        with pytest.raises(ChangedScopeError, match="git diff exited"):
            ChangedScopeResolver("no-such-revision", []).resolve()

    def test_a_revision_that_starts_with_a_hyphen_is_rejected(self, repository: Path) -> None:
        with pytest.raises(ChangedScopeError, match="hyphen"):
            ChangedScopeResolver("--output=x", []).resolve()

    def test_missing_git_raises_a_named_error(self, repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(changed_scope.shutil, "which", lambda _name: None)

        with pytest.raises(ChangedScopeError, match="not on PATH"):
            ChangedScopeResolver("base", []).resolve()

    def test_git_diff_passes_a_timeout(self, repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def record(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            captured.update(kwargs)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(changed_scope.subprocess, "run", record)

        ChangedScopeResolver("base", []).resolve()

        assert captured["timeout"] == changed_scope._GIT_TIMEOUT_SECONDS

    def test_a_stalled_git_diff_raises_a_named_error(self, repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def stall(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            raise subprocess.TimeoutExpired(cmd="git", timeout=1)

        monkeypatch.setattr(changed_scope.subprocess, "run", stall)

        with pytest.raises(ChangedScopeError, match="bound"):
            ChangedScopeResolver("base", []).resolve()


class TestChangedFromCommandLine:
    """The --changed-from option scopes the gate like the old MistHelper CI script."""

    def test_no_changed_test_file_prints_the_zero_summary(
        self, repository: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (repository / "notes.md").write_text("text\n", encoding="utf-8")
        _commit(repository, "docs")

        rc = main(["--gate", "--changed-from", "base"])

        assert rc == 0
        assert capsys.readouterr().out.splitlines() == [
            "gate_scope: 0 files checked, 0 findings checked",
            "test_quality_analyzer: 0 findings (0/0/0/0), 0 skipped, 0 parse errors",
            "gate: 0 new findings vs baseline",
        ]

    def test_no_changed_test_file_still_needs_the_baseline(
        self, repository: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (repository / "notes.md").write_text("text\n", encoding="utf-8")
        _commit(repository, "docs")

        rc = main(["--gate", "--changed-from", "base", "--baseline", "missing.json"])

        assert rc == 2  # The gate fails closed when its comparator is gone.
        assert "missing.json does not exist" in capsys.readouterr().err

    def test_an_audit_run_with_no_changed_test_file_prints_the_summary(
        self, repository: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["--changed-from", "HEAD"])

        assert rc == 0
        assert capsys.readouterr().out == "test_quality_analyzer: 0 findings (0/0/0/0), 0 skipped, 0 parse errors\n"

    def test_the_gate_scans_only_the_changed_test_file(
        self, repository: Path, repo_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = repo_root / "src" / "misthelper_devtools" / "test_quality_analyzer" / "fixtures" / "bad"
        shutil.copyfile(bad / "test_weak_assertion_bad.py", repository / "tests" / "test_weak.py")
        seed_rc = main(["--write-baseline", "--report", str(tmp_path / "r.json"), "--summary", str(tmp_path / "s.md")])
        assert seed_rc == 0
        _commit(repository, "accept the weak test")
        _git(repository, "tag", "seeded")
        _append_comment(repository / "tests" / "test_weak.py")
        _commit(repository, "change")
        capsys.readouterr()  # Drop the seed output.

        rc = main(["--gate", "--changed-from", "seeded", "--report", str(tmp_path / "r.json")])

        out = capsys.readouterr().out
        assert rc == 0  # Each finding in the changed file is in the baseline.
        assert "gate_scope: 1 files checked" in out
        assert "gate_scope: 1 files checked, 0 findings checked" not in out  # The scan read real findings.
        report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
        assert report["scanned_roots"] == ["tests/test_weak.py"]

    def test_a_new_finding_in_a_changed_test_file_fails_the_gate(
        self, repository: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = repo_root / "src" / "misthelper_devtools" / "test_quality_analyzer" / "fixtures" / "bad"
        shutil.copyfile(bad / "test_weak_assertion_bad.py", repository / "tests" / "test_weak.py")
        _commit(repository, "add a weak test")

        rc = main(["--gate", "--changed-from", "base"])

        out = capsys.readouterr().out
        assert rc == 1  # The committed baseline is empty, so each finding is new.
        assert "gate_scope: 1 files checked" in out
        assert "gate_new: " in out

    def test_a_changed_trigger_path_scans_every_root(
        self, repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The temporary repository holds no source module, so no detector can measure real scope.
        monkeypatch.setattr(TestQualityCLI, "_zero_detector_scope_metric", lambda *_args, **_kwargs: None)
        _append_comment(repository / "tests" / "test_alpha.py")
        (repository / "requirements-dev.txt").write_text("pytest>=8\n", encoding="utf-8")
        _commit(repository, "bump")

        rc = main(["--gate", "--changed-from", "base", "--full-gate-path", "requirements-dev.txt"])

        out = capsys.readouterr().out
        assert "gate_scope: 2 files checked" in out  # Both test files, not only the changed one.
        assert rc == 0

    @pytest.mark.parametrize("option", ["--baseline", "--config"])
    def test_a_changed_gate_input_scans_every_root(
        self,
        repository: Path,
        repo_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        option: str,
    ) -> None:
        # The temporary repository holds no source module, so no detector can measure real scope.
        monkeypatch.setattr(TestQualityCLI, "_zero_detector_scope_metric", lambda *_args, **_kwargs: None)
        settings = ".github/test-quality-config.toml"  # A caller-owned settings file, as in MistHelper.
        package_settings = repo_root / "src" / "misthelper_devtools" / "test_quality_analyzer" / "config.toml"
        shutil.copyfile(package_settings, repository / settings)
        _commit(repository, "own the settings")
        _git(repository, "tag", "owned")
        _append_comment(repository / "tests" / "test_alpha.py")
        if option == "--baseline":
            (repository / _BASELINE).write_text("[]\n\n", encoding="utf-8")  # The JSON keeps its value.
        else:
            _append_comment(repository / settings)
        _commit(repository, "change a gate input")

        rc = main(["--gate", "--changed-from", "owned", "--baseline", _BASELINE, "--config", settings])

        out = capsys.readouterr().out
        assert "gate_scope: 2 files checked" in out  # No --full-gate-path option names the changed input.
        assert rc == 0

    def test_an_unknown_revision_exits_two(self, repository: Path, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--gate", "--changed-from", "no-such-revision"])

        assert rc == 2  # An unknown scope must not pass the gate.
        assert "changed-file scope error" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("extra", "named"),
        [
            (["--roots", "tests"], "--roots"),
            (["--write-baseline"], "--write-baseline"),
            (["--prune-baseline"], "--prune-baseline"),
        ],
    )
    def test_options_that_cannot_be_scoped_exit_two(
        self, repository: Path, capsys: pytest.CaptureFixture[str], extra: list[str], named: str
    ) -> None:
        rc = main(["--changed-from", "base", *extra])

        assert rc == 2
        assert "--changed-from cannot be used with %s" % named in capsys.readouterr().err


class TestChangedFromMistApiExclusion:
    """A changed test that the Mist-API predicate excludes must not leave the gate without a scan."""

    def test_only_excluded_changed_tests_run_the_full_gate(
        self, repository: Path, no_source_scope_guard: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _add_api_client_test(repository)
        _commit(repository, "add an API-client test")

        rc = main(["--gate", "--changed-from", "base"])

        out = capsys.readouterr().out
        assert rc == 0  # The full scan holds no finding that the baseline lacks.
        assert "changed_scope: the Mist-API predicate excludes 1 changed test file(s)." in out
        assert "gate_scope: 3 files checked" in out  # The full scan reads both clean tests and the excluded module.
        assert "1 skipped" in out  # The excluded module stays out of every detector.

    def test_a_new_finding_in_the_full_scan_fails_after_the_fallback(
        self,
        repository: Path,
        repo_root: Path,
        no_source_scope_guard: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        bad = repo_root / "src" / "misthelper_devtools" / "test_quality_analyzer" / "fixtures" / "bad"
        shutil.copyfile(bad / "test_weak_assertion_bad.py", repository / "tests" / "test_weak.py")
        _commit(repository, "add a weak test")
        _git(repository, "tag", "seeded")
        _add_api_client_test(repository)
        _commit(repository, "add an API-client test")

        rc = main(["--gate", "--changed-from", "seeded"])

        assert rc == 1  # The weak test is outside the changed scope, but the full scan still reads it.
        assert "gate_new: " in capsys.readouterr().out

    def test_a_full_run_keeps_the_mist_api_exclusion(
        self, repository: Path, no_source_scope_guard: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _add_api_client_test(repository)
        _commit(repository, "add an API-client test")

        rc = main(["--gate"])

        out = capsys.readouterr().out
        assert rc == 0
        assert "changed_scope:" not in out  # Only a changed-file run can widen to the full scope.
        assert "1 skipped" in out

    def test_an_eligible_changed_test_keeps_the_changed_scope(
        self, repository: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = repo_root / "src" / "misthelper_devtools" / "test_quality_analyzer" / "fixtures" / "bad"
        shutil.copyfile(bad / "test_weak_assertion_bad.py", repository / "tests" / "test_weak.py")
        _add_api_client_test(repository)
        _commit(repository, "add a weak test and an API-client test")

        rc = main(["--gate", "--changed-from", "base"])

        out = capsys.readouterr().out
        assert rc == 1  # The weak test is new, so its finding still fails the mixed scope.
        assert "gate_scope: 2 files checked" in out  # Only the two changed files are read.
        assert "changed_scope:" not in out

    def test_an_unparseable_changed_test_fails_without_widening(
        self, repository: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _add_api_client_test(repository)
        (repository / "tests" / "test_broken.py").write_text("def broken(:\n", encoding="utf-8")
        _commit(repository, "add an API-client test and a broken test")

        rc = main(["--gate", "--changed-from", "base"])

        out = capsys.readouterr().out
        assert rc == 2  # An unparseable required input fails closed.
        assert "gate_scope: 2 files checked" in out  # The run keeps the changed scope.
        assert "changed_scope:" not in out

    def test_an_unreadable_changed_test_exits_two(
        self, repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _add_api_client_test(repository)
        _commit(repository, "add an API-client test")
        real_read_text = Path.read_text

        def deny_read(self: Path, *args: Any, **kwargs: Any) -> str:
            """Refuse to read the API-client test, and read every other file normally."""
            if self.name == "test_api_client.py":
                raise PermissionError(13, "Permission denied", str(self))
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", deny_read)

        rc = main(["--gate", "--changed-from", "base"])

        assert rc == 2  # An unreadable required input fails closed.
        assert "IO error" in capsys.readouterr().err

    def test_a_malformed_baseline_still_fails_after_the_fallback(
        self, repository: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (repository / _BASELINE).write_text("{not-json\n", encoding="utf-8")
        _commit(repository, "corrupt the baseline")
        _git(repository, "tag", "seeded")
        _add_api_client_test(repository)
        _commit(repository, "add an API-client test")

        rc = main(["--gate", "--changed-from", "seeded"])

        assert rc == 2  # The gate cannot compare against a damaged baseline.
        assert "baseline error" in capsys.readouterr().err

    def test_a_fallback_scan_with_no_eligible_module_fails_the_zero_scope_guard(
        self, repository: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (repository / "tests" / "test_alpha.py").unlink()
        (repository / "tests" / "test_beta.py").unlink()
        _add_api_client_test(repository)
        _commit(repository, "keep only an API-client test")
        _git(repository, "tag", "seeded")
        _append_comment(repository / "tests" / "test_api_client.py")
        _commit(repository, "change the API-client test")

        rc = main(["--gate", "--changed-from", "seeded"])

        assert rc == 2  # A full scan that inspects no module must not pass.
        assert "inspected zero real modules" in capsys.readouterr().err
