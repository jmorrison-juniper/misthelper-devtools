"""BFS-discover first-party Python modules reachable from an entrypoint file."""

from __future__ import annotations  # Enable modern annotation syntax.

import ast  # AST module powers import extraction.
import logging  # Module-scoped logger for action logging.
from collections import deque  # Deque backs the BFS frontier.
from pathlib import Path  # Portable filesystem path handling.

logger = logging.getLogger(__name__)  # Module-scoped logger for action logging.


class ModuleGraphBuilder:
    """Walk imports (top-level and lazy) to enumerate first-party modules."""

    def __init__(self, src_root: Path, extra_packages: tuple[str, ...]) -> None:
        """Store the first-party roots so import resolution stays deterministic."""
        self._src_root = src_root.resolve()  # Anchor resolution at the repo's src directory.
        self._repo_root = self._src_root.parent  # Repo root houses top-level packages.
        self._extra_packages = extra_packages  # Additional first-party top-level names.
        logger.debug("ModuleGraphBuilder anchored at %s (extras=%s)", self._src_root, extra_packages)

    def build(self, entrypoint: Path) -> set[Path]:
        """Return every first-party .py file reachable via imports (BFS)."""
        logger.info("Building module graph starting at %s", entrypoint)  # Log before traversal.
        entrypoint = entrypoint.resolve()  # Normalise to an absolute path for the visited set.
        visited: set[Path] = {entrypoint}  # Track visited files to bound the traversal.
        frontier: deque[Path] = deque([entrypoint])  # BFS frontier begins at the entrypoint.
        while frontier:  # Continue until every reachable module is expanded.
            current = frontier.popleft()  # Pop the next module to inspect.
            self._expand_module(current, visited, frontier)  # Add its imports to the frontier.
        logger.debug("Module graph closure size: %d files", len(visited))  # Log final size.
        return visited  # Return the full first-party closure.

    def _expand_module(self, path: Path, visited: set[Path], frontier: deque[Path]) -> None:
        """Parse one module and push any new first-party imports onto the frontier."""
        tree = self._safe_parse(path)  # Best-effort AST parse; None on failure.
        if tree is None:  # Parse failed; skip expansion for this file.
            return  # Nothing further to do for a broken source file.
        for module in self._collect_imports(tree):  # Walk every import in the AST.
            resolved = self._resolve(module)  # Try to map the dotted module name to a file.
            if resolved is None or resolved in visited:  # Skip stdlib/third-party/already-seen.
                continue  # Nothing new to enqueue.
            visited.add(resolved)  # Record before enqueue to prevent duplicate queueing.
            frontier.append(resolved)  # Queue the newly discovered file for expansion.

    @staticmethod
    def _safe_parse(path: Path) -> ast.Module | None:
        """Parse a Python file to AST, logging (and swallowing) any syntax error."""
        logger.debug("Parsing %s for imports", path)  # Log before reading the file.
        try:
            source = path.read_text(encoding="utf-8")  # Read source as UTF-8.
        except OSError as exc:  # Missing or unreadable file.
            logger.warning("Cannot read %s: %s", path, exc)  # Warn and continue.
            return None  # Signal the caller to skip this file.
        try:
            return ast.parse(source, filename=str(path))  # Parse into an AST module.
        except SyntaxError as exc:  # File is not valid Python; likely template/test fixture.
            logger.warning("Skipping %s: %s", path, exc)  # Warn and continue.
            return None  # Signal the caller to skip this file.

    @staticmethod
    def _collect_imports(tree: ast.AST) -> list[str]:
        """Return every dotted module name referenced by Import/ImportFrom (incl. lazy imports)."""
        modules: list[str] = []  # Accumulate dotted module names.
        for node in ast.walk(tree):  # ast.walk catches lazy imports inside function bodies.
            if isinstance(node, ast.Import):  # `import foo, bar.baz` form.
                modules.extend(alias.name for alias in node.names)  # Each alias.name is dotted.
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:  # Absolute from-import.
                modules.append(node.module)  # Record the module portion; symbol names ignored.
        return modules  # Return the raw dotted names for downstream resolution.

    def _resolve(self, module: str) -> Path | None:
        """Map a dotted module name to a first-party .py file, or None if external."""
        if not self._is_first_party(module):  # Reject stdlib / third-party imports quickly.
            return None  # Caller will skip external modules.
        parts = module.split(".")  # Split dotted name into path segments.
        candidate_module = self._repo_root.joinpath(*parts).with_suffix(".py")  # module.py form.
        if candidate_module.is_file():  # Direct-module hit.
            return candidate_module.resolve()  # Normalise before returning.
        candidate_pkg = self._repo_root.joinpath(*parts, "__init__.py")  # Package __init__ form.
        if candidate_pkg.is_file():  # Package hit.
            return candidate_pkg.resolve()  # Normalise before returning.
        logger.debug("Unresolvable first-party module: %s", module)  # Log gap without failing.
        return None  # Nothing on disk maps to this dotted name.

    def _is_first_party(self, module: str) -> bool:
        """Return True when the dotted module lives under src/ or an extra-package root."""
        head = module.split(".", 1)[0]  # Top-level package name drives the classification.
        if head == self._src_root.name:  # Anything under src/... is first-party by definition.
            return True  # Fast-path the common case.
        return head in self._extra_packages  # Otherwise consult the caller-supplied extras.
