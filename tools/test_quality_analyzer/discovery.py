"""Test-file discovery and Mist-API exclusion (T013 + T014).

Two classes, per plan.md Five-Item Rule for this module:

    TestFileDiscoverer  -- walks roots and returns POSIX-normalized test paths.
    MistApiExcluder     -- classifies whether a parsed test is Mist-API-excluded.

Both are stdlib-only, deterministic, and log info-before / debug-after per
Constitution Principle VII. Paths are always POSIX-normalized so the report
is byte-identical on Windows and Linux.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import ast  # Only ast is used to inspect the parsed module; test source not executed.
import logging  # info-before / debug-after logging plumbing.
from collections.abc import Sequence  # Sequence[Path] annotation for discover().
from pathlib import Path, PurePosixPath  # Path walks; PurePosixPath for normalization.

from tools.test_quality_analyzer.detection import (  # Types come from detection package.
    MistApiPredicate,  # Predicate parameters for classify().
    SkippedFile,  # Return record for classify().
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.


class TestFileDiscoverer:
    """Walk given roots and return POSIX-normalized paths of test files."""

    def discover(self, roots: Sequence[Path]) -> list[Path]:
        """Return a sorted list of test-file paths under the given roots."""
        # Announce the walk with the root list so operators can trace inputs.
        _LOGGER.info("Discovering test files under roots: %s", [str(r) for r in roots])
        # Collect matches into a set to deduplicate overlapping roots.
        matches: set[Path] = set()
        # Iterate each root; skip non-directory or non-existent roots gracefully.
        for root in roots:
            # Missing root: warn but continue -- do not fail the whole walk.
            if not root.exists():
                _LOGGER.warning("Skipping missing root: %s", root)
                continue
            # Root is a single file: check it directly.
            if root.is_file():
                if self._is_test_file(root):
                    matches.add(self._normalize(root))
                continue
            # Root is a directory: rglob all Python files and filter by name.
            for candidate in root.rglob("*.py"):
                if self._is_test_file(candidate):
                    matches.add(self._normalize(candidate))
        # Sort deterministically by POSIX string so callers get a stable order.
        result = sorted(matches, key=lambda p: p.as_posix())
        _LOGGER.debug("Discovery found %s test files", len(result))
        return result

    def _is_test_file(self, path: Path) -> bool:
        """Return True when `path` filename matches the pytest test pattern."""
        # Rule 1: filename starts with `test_`.
        name = path.name
        if name.startswith("test_") and name.endswith(".py"):
            return True
        # Rule 2: filename ends with `_test.py`.
        if name.endswith("_test.py"):
            return True
        # Any other filename: not a test file for our purposes.
        return False

    def _normalize(self, path: Path) -> Path:
        """Return `path` as a POSIX-forward-slash Path (Windows-safe)."""
        # Convert to PurePosixPath then back to Path; guarantees no backslashes.
        return Path(PurePosixPath(path.as_posix()))


class MistApiExcluder:
    """Classify whether a parsed test file is excluded by the Mist-API predicate."""

    def classify(
        self,
        test_path: Path,  # Repo-relative POSIX Path of the file being classified.
        tree: ast.Module,  # Pre-parsed AST of the file (source not executed).
        predicate: MistApiPredicate | None = None,  # Predicate params (defaults if None).
    ) -> SkippedFile | None:
        """Return SkippedFile if the file matches the predicate; else None."""
        # Fall back to the built-in defaults if the caller passes no predicate.
        effective = predicate or MistApiPredicate(
            banned_imports=("mistapi",),  # Default banned top-level import.
            excluded_src_prefixes=("src/api/",),  # Default excluded source prefix.
        )
        # Walk only module-scope statements per FR-002 -- nested imports do not count.
        for node in tree.body:
            # Case 1: `import mistapi` or `import mistapi as x` at module scope.
            if isinstance(node, ast.Import):
                if self._matches_import(node, effective.banned_imports):
                    return self._skip(test_path, "mist_api_excluded")
            # Case 2: `from mistapi import X` at module scope.
            # Case 3: `from src.api.foo import X` at module scope.
            elif isinstance(node, ast.ImportFrom):
                if self._matches_import_from(node, effective):
                    return self._skip(test_path, "mist_api_excluded")
        # No matching top-level import found: the file is NOT excluded.
        return None

    def _matches_import(self, node: ast.Import, banned: tuple[str, ...]) -> bool:
        """Return True when any alias in `import a, b, c` matches a banned name."""
        # Each ast.alias.name is the dotted module (e.g. "mistapi" or "mistapi.session").
        for alias in node.names:
            top = alias.name.split(".", 1)[0]
            if top in banned:
                return True
        return False

    def _matches_import_from(
        self,
        node: ast.ImportFrom,
        effective: MistApiPredicate,
    ) -> bool:
        """Return True when `from X import ...` matches banned modules or prefixes."""
        # Bare relative import with no module (e.g. `from . import x`): never matches.
        module = node.module or ""
        # Direct banned-import check: `from mistapi import ...` -> match.
        top = module.split(".", 1)[0] if module else ""
        if top and top in effective.banned_imports:
            return True
        # Prefix check: `from src.api.foo import ...` -> match "src/api/" prefix.
        # Convert dotted module path to POSIX path form so prefix comparison lines up.
        dotted_as_path = module.replace(".", "/") + "/" if module else ""
        for prefix in effective.excluded_src_prefixes:
            if dotted_as_path.startswith(prefix):
                return True
        return False

    def _skip(self, test_path: Path, reason: str) -> SkippedFile:
        """Build a SkippedFile record with a normalized POSIX path (R4 matched_rule)."""
        # POSIX-normalize the reported path so JSON output is Windows-safe.
        posix = PurePosixPath(test_path.as_posix()).as_posix()
        # Log the decision at info so audit runs surface the exclusion.
        _LOGGER.info("Mist-API predicate excluded %s (reason=%s)", posix, reason)
        # R4: matched_rule always names the predicate rule id that fired.
        return SkippedFile(file_path=posix, reason=reason, matched_rule="mist_api_predicate")
