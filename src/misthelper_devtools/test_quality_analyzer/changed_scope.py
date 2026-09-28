"""Limit a test-quality run to the test files that changed since a git revision.

WHY: a pull request can add a test-quality finding only in a test file that it
changes. A full gate run reads every test file, so a large repository waits
for findings that the pull request cannot change. MistHelper did this scoping
with inline Python in its CI workflow. This module moves the logic into the
package, so each repository uses one option and not a copy of that script.

The rules:

- A change to a trigger path can change a finding in any test file. The
  baseline and the analyzer config are always trigger paths, and the caller
  can add more, for example the CI workflow or the pinned tool versions.
  When a trigger path changed, the run scans every test root.
- Otherwise the run scans only the changed test files that still exist.
- When no test file changed, the run has nothing to scan.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import logging  # info-before / debug-after logging plumbing.
import shutil  # Resolve the absolute path of the git executable.
import subprocess  # Run git diff with explicit arguments and a timeout.
from collections.abc import Sequence  # Structural annotation for the trigger paths.
from dataclasses import dataclass  # Frozen result record.
from pathlib import Path  # Path arithmetic for the changed files.

from misthelper_devtools.test_quality_analyzer.discovery import TestFileDiscoverer  # The scan's file-name rule.

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.
_GIT_TIMEOUT_SECONDS = 60  # A credential prompt or a held lock must not stall the gate forever.


class ChangedScopeError(RuntimeError):
    """Raised when git cannot list the files that changed."""


@dataclass(frozen=True)
class ChangedScope:
    """The scan scope that the changed files select."""

    changed_files: tuple[str, ...]  # Every changed path, relative to the current directory.
    test_files: tuple[str, ...]  # Changed test files that still exist.
    full_gate_trigger: str | None  # The first changed trigger path, or None.

    @property
    def scans_every_root(self) -> bool:
        """Return True when a trigger path changed, so the run must scan every test root."""
        return self.full_gate_trigger is not None  # A trigger path overrides the file scope.


class ChangedScopeResolver:
    """Read the changed files from git and select the scan scope."""

    def __init__(self, revision: str, trigger_paths: Sequence[str]) -> None:
        self._revision = revision  # The revision that the diff starts from.
        self._trigger_paths = self._normalize_triggers(trigger_paths)  # Relative POSIX trigger paths.

    def resolve(self) -> ChangedScope:
        """Return the scope for the files that changed between the revision and HEAD."""
        _LOGGER.info("Reading the files that changed since %s", self._revision)  # Log before the git call.
        changed = self._changed_files()  # Relative POSIX paths from git.
        trigger = next((path for path in changed if path in self._trigger_paths), None)  # First trigger hit.
        discoverer = TestFileDiscoverer()  # Reuse the scan's own file-name rule.
        tests = tuple(path for path in changed if Path(path).is_file() and discoverer.is_test_file(Path(path)))
        scope = ChangedScope(changed_files=changed, test_files=tests, full_gate_trigger=trigger)  # Freeze it.
        _LOGGER.debug(  # Log the scope decision so a CI log shows why the run scanned what it scanned.
            "Changed files: %d; changed test files: %d; trigger: %s", len(changed), len(tests), trigger
        )
        return scope  # Hand the decision back to the CLI.

    @staticmethod
    def _normalize_triggers(trigger_paths: Sequence[str]) -> frozenset[str]:
        """Return each trigger path as a POSIX path relative to the current directory."""
        cwd = Path.cwd().resolve()  # git diff --relative reports paths from this directory.
        normalized: set[str] = set()  # Accumulate the comparable trigger paths.
        for raw in trigger_paths:  # Absolute paths outside the checkout cannot appear in the diff.
            if not raw:  # An empty value, for example --baseline "", names no file.
                continue  # Skip it.
            path = Path(raw)  # Parse the caller value.
            if path.is_absolute():  # The package config path is absolute.
                try:
                    path = path.resolve().relative_to(cwd)  # Keep it when it lives in the checkout.
                except ValueError:
                    continue  # A file outside the checkout never shows in the diff.
            normalized.add(path.as_posix())  # as_posix also drops a leading "./".
        _LOGGER.debug("Trigger paths: %s", sorted(normalized))  # Log the final trigger set.
        return frozenset(normalized)  # Freeze the set for membership tests.

    def _changed_files(self) -> tuple[str, ...]:
        """Return the changed paths between the revision and HEAD, relative to the current directory."""
        if self._revision.startswith("-"):  # git would read the value as an option.
            raise ChangedScopeError("the revision %r starts with a hyphen" % self._revision)
        git_path = shutil.which("git")  # An absolute path stops an earlier PATH entry supplying another program.
        if git_path is None:  # PATH holds no git binary on this host.
            raise ChangedScopeError("git is not on PATH")
        command = [git_path, "diff", "--name-only", "--relative", "-z", self._revision, "HEAD"]  # NUL-separated.
        try:
            completed = subprocess.run(  # nosec B603 - shutil.which resolved the path and the rest are literals.
                command,
                capture_output=True,  # Read the path list from stdout and the reason from stderr.
                check=False,  # A failure becomes a named error below.
                timeout=_GIT_TIMEOUT_SECONDS,  # A held lock must not stall the gate forever.
            )
        except subprocess.TimeoutExpired as exc:  # git held the pipe past the bound.
            raise ChangedScopeError("git diff passed the %ds bound and was stopped" % _GIT_TIMEOUT_SECONDS) from exc
        except OSError as exc:  # The binary cannot start.
            raise ChangedScopeError("git diff cannot start: %s" % exc) from exc
        if completed.returncode != 0:  # An unknown revision or a folder outside a repository.
            reason = completed.stderr.decode("utf-8", errors="replace").strip()  # git states the cause.
            raise ChangedScopeError("git diff exited %d: %s" % (completed.returncode, reason))
        paths = completed.stdout.decode("utf-8").split("\0")  # -z output never quotes a path.
        return tuple(path for path in paths if path)  # Drop the empty entry after the last NUL.
