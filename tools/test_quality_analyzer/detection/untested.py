"""UntestedDetector (T024): flag public functions missing test coverage.

Scans one or more "source paths" for public function definitions (function
name does NOT start with `_`) and diffs against the set of `(module, name)`
pairs referenced anywhere in the analyzed test corpus. Every source-side
public function that has ZERO references in the corpus becomes a
`Finding(rule_id="untested_public_function", severity=HIGH)`.

The detector conforms to the `Detector` structural protocol from
`tools.test_quality_analyzer.detection.__init__`. It exposes an aggregate
`analyze()` helper for the meta-tests and CLI cross-file orchestration.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import ast  # AST inspection for def/attribute/name references.
import logging  # info-before / debug-after logging per Principle VII.
from collections.abc import Sequence  # Structural annotation for accepting sequences.
from pathlib import Path  # Path arithmetic for module-name derivation.

from tools.test_quality_analyzer.detection import (  # Registry + shared types.
    Category,
    DetectorRegistry,
    Finding,
    Severity,
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.


class UntestedDetector:
    """Detects public functions declared in source modules but never referenced in tests."""

    def __init__(self, source_paths: Sequence[Path] | None = None) -> None:
        """Store the source roots whose public functions must be checked."""
        # Default to () when omitted so CLI can late-bind before analyze() is called.
        self._source_paths: tuple[Path, ...] = tuple(source_paths or ())  # Frozen input list.
        # Test-corpus reference set is built up across detect() calls.
        self._referenced_names: set[str] = set()  # Names seen in analyzed test files.

    # --- Detector protocol ---------------------------------------------------

    def detect(
        self,
        test_path: Path,  # POSIX path of the test file (unused for this detector).
        tree: ast.Module,  # AST of the test file to scan for references.
        source: str,  # Raw source text (unused; kept to satisfy the protocol).
    ) -> list[Finding]:
        """Record references from `tree`; emit findings only on `analyze()`."""
        # UntestedDetector is cross-file: per-file detect() is a no-op emitter.
        _LOGGER.info("Recording references from test file %s", test_path)
        # Walk every Name/Attribute node and record simple identifiers.
        for node in ast.walk(tree):
            # `foo(...)` -> Name("foo"); `mod.foo(...)` -> Attribute(value=Name("mod"), attr="foo").
            if isinstance(node, ast.Name):
                self._referenced_names.add(node.id)  # Bare name reference.
            elif isinstance(node, ast.Attribute):
                self._referenced_names.add(node.attr)  # Attribute access name.
        # Debug-after with running total so log skims show corpus growth.
        _LOGGER.debug("Reference set size after %s: %s", test_path, len(self._referenced_names))
        # Return an empty list; emit_findings() (via analyze()) produces the real output.
        return []

    def emit_findings(self) -> list[Finding]:
        """Return the deferred findings computed from all recorded references."""
        # Log the scan announcement so operators can time this pass separately.
        _LOGGER.info("Scanning %s source path(s) for untested public functions", len(self._source_paths))
        # Aggregate findings across every source file.
        findings: list[Finding] = []  # Accumulator for the return list.
        for source_path in self._source_paths:
            # Skip missing paths gracefully with a warning rather than crashing.
            if not source_path.exists():
                _LOGGER.warning("Untested source path missing: %s", source_path)
                continue
            # Iterate source files: a single file or every .py under a directory root.
            for module_path in self._iter_source_files(source_path):
                # Parse the source module; skip on syntax errors (surfaced elsewhere).
                try:
                    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
                except SyntaxError as exc:
                    _LOGGER.warning("Skipping unparseable source %s: %s", module_path, exc)
                    continue
                # Extract public function names declared at module scope only.
                findings.extend(self._diff_public_functions(module_path, tree))
        # Debug-after with the untested count for quick log skimming.
        _LOGGER.debug("Untested finding count: %s", len(findings))
        return findings

    def analyze(
        self,
        test_files: Sequence[tuple[Path, ast.Module, str]] | None = None,
    ) -> list[Finding]:
        """Convenience helper: record references then emit findings in one call."""
        # Empty default so callers can pass no tests to produce the maximum finding set.
        for test_path, tree, source in test_files or ():
            self.detect(test_path, tree, source)  # Record references from each file.
        # After all test files are recorded, compute the untested diff.
        return self.emit_findings()

    # --- Internal helpers ----------------------------------------------------

    def _iter_source_files(self, path: Path) -> list[Path]:
        """Return the list of `.py` files rooted at `path` (single-file or dir)."""
        # File input: return it as a singleton if the extension matches.
        if path.is_file():
            return [path] if path.suffix == ".py" else []
        # Directory input: expand every .py file recursively.
        return sorted(path.rglob("*.py"))  # Sorted for deterministic iteration.

    def _diff_public_functions(
        self,
        module_path: Path,  # Path of the source module being scanned.
        tree: ast.Module,  # AST of that module.
    ) -> list[Finding]:
        """Return findings for every module-scope public function absent from the corpus."""
        # POSIX-normalize the module path for cross-platform stability.
        posix = module_path.as_posix()  # Report field uses POSIX separators.
        # Collect findings for this module.
        module_findings: list[Finding] = []  # Per-module accumulator.
        # Only inspect module-scope statements per FR-002 style (top-level defs).
        for node in tree.body:
            # Both sync and async public function defs qualify.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name  # Function identifier being checked.
                if name.startswith("_"):
                    continue  # Skip private / dunder names entirely.
                if name.startswith("test_"):
                    continue  # Pytest test functions are tests themselves, not SUT code.
                if name in self._referenced_names:
                    continue  # Referenced somewhere in the test corpus -> not untested.
                # Emit a HIGH-severity finding with a clear explanation + remediation.
                module_findings.append(
                    Finding(
                        category=Category.UNTESTED,
                        rule_id="untested_public_function",
                        severity=Severity.HIGH,
                        file_path=posix,
                        line_number=node.lineno,
                        explanation=("Public function '%s' has no reference in the analyzed test corpus." % name),
                        remediation=("Add a test that imports and exercises '%s' or mark it private." % name),
                        heuristic=False,
                        related_source=posix,
                    ),
                )
        return module_findings


# Register a default instance on import (T019 registry contract).
# The CLI replaces this instance with a properly configured one at runtime;
# the registry entry primarily documents that the detector exists.
DetectorRegistry.append(UntestedDetector())  # Singleton registration.
