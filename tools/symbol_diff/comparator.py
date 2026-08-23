"""The symbol table comparator.

Holds the ``SymbolTableComparator`` class and the ``SymbolDelta`` result record.
The class reads a base revision from git, reads the work tree from disk, and
reports the module-level names that the change lost and the names that it added.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import ast  # Parses source text and never runs it, so a broken file stays readable.
import logging  # Records each read and each comparison, per the action logging rule.
import shutil  # Resolves the git executable to an absolute path before the call.
import subprocess  # nosec B404 - The class queries git, and the call below uses shell=False.
from dataclasses import dataclass  # Builds the immutable per-file result record.
from pathlib import Path  # Holds every path, so no code hardcodes a separator.

# The statement types that define a module-level name through a name attribute.
# An annotated assignment parses as ast.AnnAssign, not as ast.Assign. A tool that
# matches ast.Assign alone misses every annotated global, which is the exact
# defect that issue #1796 reports.
_DEFINITION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# The repository root, derived from the location of this file. Every git call and
# every relative path resolves against this directory. A pre-commit hook or a CI
# step can then run the tool from any working directory and read the same files.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# A held git index lock or a credential prompt blocks a git call with no bound.
# This cap turns that stall into a clear message instead of a six-hour CI job.
_GIT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class SymbolDelta:
    """One comparison result for one file."""

    path: str  # The repository path that the tool compared.
    lost: tuple[str, ...] = ()  # Names present in the base revision and absent now.
    added: tuple[str, ...] = ()  # Names absent in the base revision and present now.


class SymbolTableComparator:
    """Compares the module-level symbol table of a base revision against the work tree."""

    def collect_names(self, source: str, label: str) -> set[str] | None:
        """Return the module-level names in the source, or None when it does not parse."""
        logging.debug("Parsing the module-level symbol table of %s", label)  # Log before the parse.
        try:
            tree = ast.parse(source)  # ast.parse reads text, so an uncompilable file stays readable.
        except SyntaxError as error:  # Defect 2 of issue #1796 left a file that does not compile.
            print(f"symbol_diff: {label} line {error.lineno or 0}: {error.msg}")  # Name the file and line.
            logging.warning("Skipped %s because the source does not parse", label)  # Log after the failure.
            return None  # A caller reads None as "unreadable", not as "no names".
        names: set[str] = set()  # Accumulates the module-level names.
        for node in tree.body:  # Only a top-level statement defines a module-level name.
            names.update(self._names_of(node))  # Each statement contributes zero or more names.
        logging.debug("Collected %d module-level names from %s", len(names), label)  # Log after the parse.
        return names  # The caller compares this set against the other revision.

    def read_revision(self, revision: str, path: Path) -> str | None:
        """Return the text of the path at the revision, or None when git cannot read it."""
        target = f"{revision}:{path.as_posix()}"  # git wants a forward-slash path on every platform.
        git_path = shutil.which("git")  # Resolve an absolute path, so the call passes no bare name.
        if git_path is None:  # A host without git cannot supply the base revision.
            print("symbol_diff: PATH holds no git executable")  # Tell the operator what is missing.
            return None  # The caller skips this file.
        logging.info("Reading %s from git", target)  # Log before the read.
        try:
            completed = subprocess.run(  # nosec B603 - shutil.which resolved the path and the rest are literals.
                [git_path, "show", target],  # A fixed argument list, so no shell parses the target.
                capture_output=True,  # Capture the file text from stdout.
                text=True,  # Decode to str, because ast.parse reads text.
                check=False,  # An unknown path returns 128, which this method reports itself.
                cwd=_REPOSITORY_ROOT,  # Read the repository, not the caller working directory.
                timeout=_GIT_TIMEOUT_SECONDS,  # A credential prompt must not stall the gate forever.
            )
        except subprocess.TimeoutExpired:  # git held the pipe past the bound.
            # Name the bound, so the operator can tell a stall from a crash.
            print(f"symbol_diff: git show passed {_GIT_TIMEOUT_SECONDS}s and was stopped")
            logging.warning("The git show command passed the %ds bound", _GIT_TIMEOUT_SECONDS)
            return None  # The caller skips this file.
        if completed.returncode != 0:  # git could not resolve the revision or the path.
            print(f"symbol_diff: cannot read {target}")  # Name the unreadable target.
            logging.warning("The git show command failed for %s", target)  # Log after the failure.
            return None  # The caller skips this file.
        logging.debug("Read %d characters from %s", len(completed.stdout), target)  # Log after the read.
        return completed.stdout  # The caller parses this text.

    def compare(self, base_names: set[str], head_names: set[str], path: str) -> SymbolDelta:
        """Return the names that the change lost and the names that it added."""
        logging.debug("Comparing the symbol table of %s", path)  # Log before the comparison.
        lost = tuple(sorted(base_names - head_names))  # A lost name is the defect of issue #1796.
        added = tuple(sorted(head_names - base_names))  # An added name can shadow an import.
        logging.info("Found %d lost and %d added names in %s", len(lost), len(added), path)  # Log after.
        return SymbolDelta(path=path, lost=lost, added=added)  # The report method prints this record.

    def report(self, deltas: list[SymbolDelta]) -> int:
        """Print every delta and return 1 when any file lost or added a name."""
        changed = [delta for delta in deltas if delta.lost or delta.added]  # A quiet file needs no line.
        for delta in changed:  # One group of lines for each changed file keeps the output readable.
            print(f"symbol_diff: {delta.path}")  # Name the file before its names.
            print(f"  lost:  {', '.join(delta.lost) or '(none)'}")  # A lost name blocks the sweep.
            print(f"  added: {', '.join(delta.added) or '(none)'}")  # An added name can shadow an import.
        if not changed:  # Every compared file holds the same module-level names.
            print("symbol_diff: no module-level name changed")  # State the clean result plainly.
            return 0  # Exit code 0 lets the sweep continue.
        logging.info("Reported a symbol table change in %d file(s)", len(changed))  # Log the outcome.
        return 1  # Exit code 1 stops a sweep that changed the symbol table.

    def run(self, base: str, paths: list[str]) -> int:
        """Compare every path against the base revision and return the process exit code."""
        logging.info("Comparing %d path(s) against base revision %s", len(paths), base)  # Log before.
        results = (self._inspect(base, Path(name)) for name in paths)  # One delta for each path.
        deltas = [delta for delta in results if delta is not None]  # Drop the paths that nobody could read.
        logging.debug("Collected %d comparable delta(s)", len(deltas))  # Log after the comparison.
        return self.report(deltas)  # The report method owns the exit code.

    def _inspect(self, base: str, path: Path) -> SymbolDelta | None:
        """Return the delta for one path, or None when either revision does not parse."""
        base_source = self.read_revision(base, path)  # The base text comes from git, not from disk.
        head_source = self._read_worktree(path)  # The head text comes from the work tree.
        if base_source is None or head_source is None:  # One side is missing, so no comparison is honest.
            return None  # Report nothing rather than a false lost-name list.
        base_names = self.collect_names(base_source, f"{base}:{path.as_posix()}")  # Names before the change.
        head_names = self.collect_names(head_source, path.as_posix())  # Names after the change.
        if base_names is None or head_names is None:  # A file that does not parse yields no trusted set.
            return None  # The collect_names method already named the file and the line.
        return self.compare(base_names, head_names, path.as_posix())  # Both sides parsed, so compare them.

    def _read_worktree(self, path: Path) -> str | None:
        """Return the work tree text of the path, or None when the read fails."""
        logging.debug("Reading %s from the work tree", path.as_posix())  # Log before the read.
        absolute = path if path.is_absolute() else _REPOSITORY_ROOT / path  # Match the git side.
        try:
            return absolute.read_text(encoding="utf-8")  # UTF-8 matches the project source encoding.
        except OSError as error:  # A deleted path or a permission error reaches this branch.
            print(f"symbol_diff: cannot read {path.as_posix()}: {error}")  # Name the unreadable path.
            return None  # The caller skips this file.

    def _names_of(self, node: ast.stmt) -> set[str]:
        """Return the module-level names that one top-level statement defines."""
        if isinstance(node, _DEFINITION_NODES):  # A function, an async function, or a class.
            return {node.name}  # Each of the three node types carries one name attribute.
        if isinstance(node, (ast.Import, ast.ImportFrom)):  # An import binds a name at module level.
            return {alias.asname or alias.name.split(".")[0] for alias in node.names}  # Bind the root name.
        if isinstance(node, ast.AnnAssign):  # An annotated assignment, such as NAME: bool = False.
            return self._target_names([node.target])  # One target only, per the Python grammar.
        if isinstance(node, ast.Assign):  # A plain assignment, such as NAME = False.
            return self._target_names(node.targets)  # A chained assignment carries several targets.
        return set()  # Any other statement defines no module-level name.

    def _target_names(self, targets: list[ast.expr]) -> set[str]:
        """Return the bound names inside an assignment target list."""
        names: set[str] = set()  # Accumulates the bound names.
        for target in targets:  # A target list holds one entry for each chained assignment.
            if isinstance(target, ast.Name):  # The common case binds one plain name.
                names.add(target.id)  # Record the bound name.
            elif isinstance(target, (ast.Tuple, ast.List)):  # An unpacking target binds several names.
                names.update(self._target_names(list(target.elts)))  # Recurse into the nested targets.
        return names  # The caller adds these names to the module set.
