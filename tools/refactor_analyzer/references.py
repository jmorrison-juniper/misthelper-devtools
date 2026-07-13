"""Scope-aware reference indexing for top-level names across the module graph."""

from __future__ import annotations  # Enable modern annotation syntax.

import ast  # AST powers the visitor + alias table construction.
import logging  # Module-scoped logger for action logging.
from collections.abc import Iterable  # Typed protocol for the file iterator.
from pathlib import Path  # Portable filesystem path handling.

from tools.refactor_analyzer.models import Reference  # Data record produced by this module.

logger = logging.getLogger(__name__)  # Module-scoped logger for action logging.


class _NameCollector(ast.NodeVisitor):
    """AST visitor that records references to target names, honoring local scopes."""

    def __init__(self, targets: set[str], aliases: dict[str, str], file_path: str) -> None:
        """Store the target set and per-file alias map so visits stay stateless."""
        self._targets = targets  # Set of top-level names we want to count.
        self._aliases = aliases  # Local-alias -> canonical target-name mapping.
        self._file_path = file_path  # Path emitted on every recorded Reference.
        self._scopes: list[set[str]] = [set()]  # Stack of locally-bound names per scope.
        self._enclosing: list[str] = []  # Stack of enclosing symbol names (for reporting).
        self._refs: list[Reference] = []  # Accumulated reference records.

    def references(self) -> list[Reference]:
        """Expose the accumulated reference list after visiting the tree."""
        return self._refs  # Simple accessor; caller owns the list from here.

    def visit_Module(self, node: ast.Module) -> None:  # Module scope is scope[0].
        """Pre-bind every top-level name so intra-module refs resolve deterministically."""
        for child in node.body:  # Only look at top-level statements at this stage.
            self._bind_top_level(child)  # Pre-populate module scope with local names.
        self.generic_visit(node)  # Walk children as usual once bindings are known.

    def _bind_top_level(self, node: ast.AST) -> None:
        """Record module-level `def`/`class`/import/assignment names into scope[0]."""
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):  # Named def/class.
            self._scopes[0].add(node.name)  # Bind the local name at module scope.
        elif isinstance(node, ast.Import):  # `import foo` binds `foo` locally.
            for alias in node.names:  # Iterate each dotted import.
                self._scopes[0].add(alias.asname or alias.name.split(".")[0])  # Root name binding.
        elif isinstance(node, ast.ImportFrom):  # `from x import y as z` binds `z` (or `y`).
            for alias in node.names:  # Iterate each imported symbol.
                self._scopes[0].add(alias.asname or alias.name)  # Prefer alias then real name.
        elif isinstance(node, ast.Assign):  # Simple assignments bind their LHS names.
            for target in node.targets:  # Multiple targets: `a = b = expr`.
                self._bind_assign_target(target)  # Delegate name extraction.
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):  # Typed assign.
            self._scopes[0].add(node.target.id)  # Bind the annotated name.

    def _bind_assign_target(self, target: ast.AST) -> None:
        """Bind Name targets on the LHS of an assignment into the current scope."""
        if isinstance(target, ast.Name):  # `foo = ...` case.
            self._scopes[-1].add(target.id)  # Add to the current (top) scope.
        elif isinstance(target, ast.Tuple | ast.List):  # Tuple/list unpacking.
            for element in target.elts:  # Recurse into each element.
                self._bind_assign_target(element)  # Named elements get bound.

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Enter a function scope, bind params and inner defs, then walk the body."""
        self._enter_function_scope(node)  # Push scope + bind params/locals.
        self.generic_visit(node)  # Visit children within the new scope.
        self._exit_scope()  # Pop scope + enclosing symbol.

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Async functions behave identically for scope purposes."""
        self._enter_function_scope(node)  # Push + bind.
        self.generic_visit(node)  # Descend.
        self._exit_scope()  # Pop.

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Class body opens a scope; class name itself is bound in its parent."""
        self._scopes[-1].add(node.name)  # Bind the class name in the enclosing scope.
        self._scopes.append(set())  # New scope for the class body.
        self._enclosing.append(node.name)  # Track enclosing symbol name for reports.
        self.generic_visit(node)  # Visit methods and nested defs.
        self._exit_scope()  # Pop.

    def _enter_function_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Bind the function name in the parent scope, then push a fresh local scope."""
        self._scopes[-1].add(node.name)  # Function name binds in enclosing scope.
        scope: set[str] = set()  # Fresh scope for the function body.
        for arg_group in (node.args.args, node.args.kwonlyargs, node.args.posonlyargs):  # All arg groups.
            for arg in arg_group:  # Every positional/keyword-only arg.
                scope.add(arg.arg)  # Bind each argument name.
        if node.args.vararg:  # `*args` if present.
            scope.add(node.args.vararg.arg)  # Bind varargs name.
        if node.args.kwarg:  # `**kwargs` if present.
            scope.add(node.args.kwarg.arg)  # Bind kwargs name.
        for child in node.body:  # Pre-bind nested defs/imports so forward refs resolve.
            self._bind_top_level_into(child, scope)  # Use helper to pre-bind names.
        self._scopes.append(scope)  # Push the assembled scope.
        self._enclosing.append(node.name)  # Track for enclosing_symbol on refs.

    def _bind_top_level_into(self, node: ast.AST, scope: set[str]) -> None:
        """Same as _bind_top_level but writes into an explicitly-passed scope set."""
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):  # Named def/class.
            scope.add(node.name)  # Bind the nested definition name.
        elif isinstance(node, ast.Import):  # Local `import foo`.
            for alias in node.names:  # Each import target.
                scope.add(alias.asname or alias.name.split(".")[0])  # Root name binding.
        elif isinstance(node, ast.ImportFrom):  # Local `from x import y`.
            for alias in node.names:  # Each imported symbol.
                scope.add(alias.asname or alias.name)  # Prefer alias then real name.

    def _exit_scope(self) -> None:
        """Pop the last scope frame and its enclosing-symbol counterpart."""
        self._scopes.pop()  # Drop the top scope.
        if self._enclosing:  # Guard: only pop if we pushed.
            self._enclosing.pop()  # Drop matching enclosing symbol name.

    def visit_Name(self, node: ast.Name) -> None:
        """Record a reference when the Name resolves to one of our target symbols."""
        canonical = self._canonical_target(node.id)  # Map alias -> canonical target name.
        if canonical is None:  # Not one of our targets at all.
            return  # Nothing to record.
        if self._is_shadowed(node.id):  # Local scope hides the module-level binding.
            return  # Skip shadowed references.
        self._refs.append(  # Record a Reference row for this name-use site.
            Reference(
                target_name=canonical,  # Canonical name (post-alias resolution).
                file_path=self._file_path,  # Where the reference lives.
                lineno=node.lineno,  # 1-based line number of the site.
                enclosing_symbol=self._enclosing[-1] if self._enclosing else None,  # Nearest def/class.
            )
        )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Root-name attribute chains (e.g. Manager.method) count as one reference."""
        root = node  # Walk to the leftmost Name to find the chain root.
        while isinstance(root, ast.Attribute):  # Descend attribute nesting.
            root = root.value  # Follow the value chain leftward.
        if isinstance(root, ast.Name):  # Chain rooted at a bare name; check it.
            self.visit_Name(root)  # Delegate to visit_Name for the counting logic.
        self.generic_visit(node)  # Continue walking nested Attributes.

    def _canonical_target(self, name: str) -> str | None:
        """Return the canonical target name for `name`, or None if it's not a target."""
        if name in self._aliases:  # Locally aliased import of a target.
            return self._aliases[name]  # Return the canonical name.
        if name in self._targets:  # Direct reference to a target.
            return name  # Already canonical.
        return None  # Not one of our targets.

    def _is_shadowed(self, name: str) -> bool:
        """Return True when a scope above module level locally binds `name`."""
        for scope in self._scopes[1:]:  # Inspect every scope above module scope.
            if name in scope:  # Local binding shadows the module-level target.
                return True  # Shadowed; skip this reference.
        return False  # No local binding; reference is real.


class ReferenceIndex:
    """Index references to a set of target names across many files."""

    def __init__(self, targets: set[str], entrypoint: Path) -> None:
        """Store targets and entrypoint metadata used during per-file indexing."""
        self._targets = targets  # Names we are counting references to.
        self._entrypoint = entrypoint.resolve()  # Normalised entrypoint path.
        self._entrypoint_module = entrypoint.stem  # Dotted module name for alias lookup.
        logger.debug("ReferenceIndex targets=%d entrypoint=%s", len(targets), self._entrypoint)

    def index_all(self, files: Iterable[Path]) -> dict[str, list[Reference]]:
        """Return {target_name: [Reference, ...]} across every file in the graph."""
        logger.info("Indexing references across module graph")  # Log before traversal.
        aggregated: dict[str, list[Reference]] = {name: [] for name in self._targets}  # Pre-seed keys.
        for file_path in files:  # Iterate every discovered first-party file.
            for reference in self.index_file(file_path):  # Collect refs from this file.
                aggregated.setdefault(reference.target_name, []).append(reference)  # Bucket by target.
        logger.debug("Reference index built: %d targets", len(aggregated))  # Post-log the size.
        return aggregated  # Return the full mapping to the caller.

    def index_file(self, path: Path) -> list[Reference]:
        """Parse one file, build its alias map, run the collector, and return refs."""
        tree = self._safe_parse(path)  # Best-effort AST parse; None on failure.
        if tree is None:  # Parse failed; nothing to collect.
            return []  # Empty list keeps callers simple.
        aliases = self._build_alias_map(tree)  # Map local aliases -> canonical target names.
        collector = _NameCollector(  # Instantiate the visitor with all context.
            targets=self._targets,  # Names to count.
            aliases=aliases,  # Alias map for `from X import Y as Z`.
            file_path=str(path),  # Absolute path emitted on every Reference.
        )
        collector.visit(tree)  # Walk the AST to gather references.
        return collector.references()  # Return the raw list of references found.

    @staticmethod
    def _safe_parse(path: Path) -> ast.Module | None:
        """Parse a Python file to AST, logging any read/syntax error."""
        logger.debug("Parsing %s for references", path)  # Log before reading the file.
        try:
            source = path.read_text(encoding="utf-8")  # Read source as UTF-8.
        except OSError as exc:  # Missing or unreadable file.
            logger.warning("Cannot read %s: %s", path, exc)  # Warn and continue.
            return None  # Signal the caller to skip.
        try:
            return ast.parse(source, filename=str(path))  # Return a Module AST.
        except SyntaxError as exc:  # Not valid Python; skip.
            logger.warning("Skipping %s: %s", path, exc)  # Warn and continue.
            return None  # Signal the caller to skip.

    def _build_alias_map(self, tree: ast.Module) -> dict[str, str]:
        """Return {local_alias: canonical_target_name} for imports of our targets."""
        aliases: dict[str, str] = {}  # Local-name -> canonical-target-name mapping.
        for node in ast.walk(tree):  # ast.walk catches lazy imports inside functions.
            if not isinstance(node, ast.ImportFrom):  # Only from-imports carry aliases we care about.
                continue  # Skip Import / other nodes here.
            if node.module != self._entrypoint_module or node.level != 0:  # Wrong module or relative.
                continue  # Only aliases against the entrypoint module count.
            for alias in node.names:  # Every imported symbol from the entrypoint.
                if alias.name in self._targets:  # Only track aliases for our targets.
                    aliases[alias.asname or alias.name] = alias.name  # Local -> canonical.
        return aliases  # Return the assembled alias table.
