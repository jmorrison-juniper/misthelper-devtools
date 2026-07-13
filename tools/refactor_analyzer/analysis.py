"""Orchestrator that produces AnalysisResult for one entrypoint file."""

from __future__ import annotations  # Enable modern annotation syntax.

import ast  # AST powers definition inventory + guideline scanning.
import logging  # Module-scoped logger for action logging.
from collections import Counter  # Counter drives the sole-caller heuristic.
from pathlib import Path  # Portable filesystem path handling.

from tools.refactor_analyzer.graph import ModuleGraphBuilder  # BFS import graph.
from tools.refactor_analyzer.models import (  # Data models produced/consumed here.
    CATEGORY_HOT,
    CATEGORY_LOW_USE,
    CATEGORY_SINGLE_USE,
    CATEGORY_SKIPPED,
    CATEGORY_UNUSED,
    FLAG_HARDCODED_SEPARATOR,
    FLAG_MISSING_ACTION_LOGGING,
    FLAG_MISSING_INLINE_COMMENTS,
    FLAG_NON_ASCII_LOGS,
    FLAG_OVERSIZE,
    FLAG_RAW_INPUT,
    FLAG_TOO_MANY_PARAMS,
    AnalysisResult,
    Candidate,
    Definition,
    Reference,
)
from tools.refactor_analyzer.references import ReferenceIndex  # Reference index.

logger = logging.getLogger(__name__)  # Module-scoped logger for action logging.


# Symbols that reference-count analysis would flag as movable but which MUST stay
# in the entrypoint because of module-load / bootstrap ordering. Static analysis
# cannot detect this; the list is curated. Extend via the CLI --skip flag.
SKIP_ALWAYS: frozenset[str] = frozenset(
    {
        "GlobalImportManager",  # Wires sys.path + import hooks before anything else loads; moving it breaks bootstrap.
    }
)


class RefactorAnalyzer:
    """Glue the graph builder, definition inventory, and reference index together."""

    _CLASS_NAME_SUFFIXES = (
        "Manager",
        "Utils",
        "Helper",
        "Handler",
        "Runner",
        "Exporter",
        "Processor",
        "Builder",
    )  # Existing suffixes to strip so we don't double them (e.g. FirmwareManagerManager).

    def __init__(
        self,
        src_root: Path,
        extra_packages: tuple[str, ...],
        min_lines: int,
        include_private: bool,
        skip_names: tuple[str, ...] = (),
    ) -> None:
        """Store configuration used across analyze() invocations."""
        self._src_root = src_root.resolve()  # First-party source root.
        self._extra_packages = extra_packages  # Additional first-party top-level names.
        self._min_lines = min_lines  # Skip trivial defs below this LOC.
        self._include_private = include_private  # Whether `_leading_underscore` names are analyzed.
        self._skip_names = SKIP_ALWAYS | frozenset(skip_names)  # Curated bootstrap pins plus user overrides.
        self._entrypoint_path: Path | None = None  # Set during analyze() so home-suggestion can detect self-refs.
        logger.debug(
            "RefactorAnalyzer configured (min_lines=%d, private=%s, skip=%d)",
            min_lines,
            include_private,
            len(self._skip_names),
        )

    def analyze(self, entrypoint: Path) -> AnalysisResult:
        """Run the full pipeline: graph -> definitions -> references -> candidates."""
        logger.info("Analyzing %s", entrypoint)  # Log before starting.
        entrypoint = entrypoint.resolve()  # Normalise for consistent path comparisons.
        self._entrypoint_path = entrypoint  # Remember so home-suggestion can detect self-references.
        source_lines = entrypoint.read_text(encoding="utf-8").splitlines()  # Full source for LOC counting.
        tree = ast.parse("\n".join(source_lines), filename=str(entrypoint))  # Parse the entrypoint.
        definitions = self._inventory_definitions(tree, source_lines)  # Column-0 defs/classes/assigns.
        logger.debug("Inventoried %d definitions", len(definitions))  # Post-log the count.
        graph = ModuleGraphBuilder(self._src_root, self._extra_packages).build(entrypoint)  # Reachable files.
        targets = {defn.name for defn in definitions}  # Names to count references for.
        refs_by_name = ReferenceIndex(targets, entrypoint).index_all(graph)  # Aggregate refs.
        candidates = self._build_candidates(definitions, refs_by_name, source_lines, tree)  # Assemble output.
        candidates.sort(key=lambda c: c.definition.line_count, reverse=True)  # Biggest wins first.
        loc_saveable = sum(  # Sum LOC across easily-removable categories.
            c.definition.line_count for c in candidates if c.category in {CATEGORY_UNUSED, CATEGORY_SINGLE_USE}
        )
        logger.info("Analysis complete: %d candidates, %d LOC saveable", len(candidates), loc_saveable)
        return AnalysisResult(  # Wrap everything in the result dataclass.
            entrypoint=str(entrypoint),  # Path of analyzed file.
            module_graph_size=len(graph),  # First-party files reached.
            definitions=definitions,  # Full definition inventory.
            candidates=candidates,  # Ranked candidates.
            loc_saveable=loc_saveable,  # Line total removable via unused + single-use.
        )

    def _inventory_definitions(self, tree: ast.Module, source_lines: list[str]) -> list[Definition]:
        """Return every column-0 def/class/assignment eligible for reference counting."""
        results: list[Definition] = []  # Accumulate matching definitions.
        for node in tree.body:  # Only top-level statements count.
            defn = self._definition_from_node(node, source_lines)  # Try to build a Definition.
            if defn is None:  # Node type not handled or filtered out.
                continue  # Skip and move on.
            if defn.is_private and not self._include_private:  # Respect private-name filter.
                continue  # Skip private names unless flag set.
            if defn.line_count < self._min_lines:  # Skip trivial defs shorter than threshold.
                continue  # Ignore small defs.
            results.append(defn)  # Keep this definition.
        return results  # Full inventory.

    def _definition_from_node(self, node: ast.AST, source_lines: list[str]) -> Definition | None:
        """Turn a top-level AST node into a Definition record, or None if unsupported."""
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):  # Function definitions.
            kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"  # Async flag.
            return self._make_definition(node.name, kind, node, source_lines, node.decorator_list)  # Build.
        if isinstance(node, ast.ClassDef):  # Class definitions.
            return self._make_definition(node.name, "class", node, source_lines, node.decorator_list)  # Build.
        if isinstance(node, ast.Assign):  # Simple `NAME = ...` assignment.
            return self._definition_from_assign(node, source_lines)  # Delegate specialised handling.
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):  # `NAME: T = ...`.
            return self._make_definition(node.target.id, "assignment", node, source_lines, [])  # Build.
        return None  # Unsupported node type.

    def _definition_from_assign(self, node: ast.Assign, source_lines: list[str]) -> Definition | None:
        """Handle single-target `NAME = expr` assignments; skip dunder/multi-target cases."""
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):  # Multi-target or non-Name.
            return None  # Skip complex assignments.
        name = node.targets[0].id  # Extract the bound name.
        if name.startswith("__") and name.endswith("__"):  # Dunder like __all__.
            return None  # Skip dunders which aren't refactor candidates.
        return self._make_definition(name, "assignment", node, source_lines, [])  # Build the record.

    @staticmethod
    def _make_definition(
        name: str, kind: str, node: ast.AST, source_lines: list[str], decorators: list[ast.expr]
    ) -> Definition:
        """Assemble a Definition from an AST node's location + decorator list."""
        lineno = getattr(node, "lineno", 1)  # 1-based start line.
        end_lineno = getattr(node, "end_lineno", lineno) or lineno  # Fallback if end_lineno missing.
        line_count = max(1, end_lineno - lineno + 1)  # Inclusive physical line count.
        decorator_names = tuple(RefactorAnalyzer._decorator_name(dec) for dec in decorators)  # Best-effort names.
        _ = source_lines  # Reserved for future span-based analysis; kept in signature for clarity.
        return Definition(  # Immutable record ready for downstream stages.
            name=name,  # Public symbol name.
            kind=kind,  # function / async_function / class / assignment.
            lineno=lineno,  # Start line (1-based).
            end_lineno=end_lineno,  # End line (inclusive).
            line_count=line_count,  # Physical line count.
            is_private=name.startswith("_") and not name.startswith("__"),  # Single-underscore private.
            decorators=decorator_names,  # Best-effort dotted attribute strings.
        )

    @staticmethod
    def _decorator_name(node: ast.expr) -> str:
        """Return a dotted string for a decorator expression (best-effort)."""
        if isinstance(node, ast.Name):  # Bare `@foo`.
            return node.id  # Return the name.
        if isinstance(node, ast.Attribute):  # `@module.foo`.
            parts: list[str] = []  # Accumulate attribute chain in reverse.
            current: ast.AST = node  # Walk down the attribute chain.
            while isinstance(current, ast.Attribute):  # Descend attributes.
                parts.append(current.attr)  # Append current attribute name.
                current = current.value  # Move to inner node.
            if isinstance(current, ast.Name):  # Root is a bare name.
                parts.append(current.id)  # Add root name.
            return ".".join(reversed(parts))  # Reverse to get dotted order.
        if isinstance(node, ast.Call):  # `@foo(...)` — recurse into func.
            return RefactorAnalyzer._decorator_name(node.func)  # Return the callable name.
        return "<expr>"  # Fallback for exotic expressions.

    def _build_candidates(
        self,
        definitions: list[Definition],
        refs_by_name: dict[str, list[Reference]],
        source_lines: list[str],
        tree: ast.Module,
    ) -> list[Candidate]:
        """Combine definitions + references + guideline flags into Candidate records."""
        nodes_by_name = self._index_nodes_by_name(tree)  # {name: ast.AST} for guideline scans.
        candidates: list[Candidate] = []  # Accumulate the output list.
        for defn in definitions:  # One candidate per inventoried definition.
            refs = refs_by_name.get(defn.name, [])  # References found for this name.
            reference_files = self._group_refs_by_file(refs)  # For PR-per-cluster planning.
            flags = self._scan_guideline_flags(defn, nodes_by_name.get(defn.name), source_lines)  # Body flags.
            if defn.name in self._skip_names:  # Curated bootstrap pin overrides normal categorization.
                category = CATEGORY_SKIPPED  # Force the skipped bucket regardless of reference count.
                suggested_class = None  # No landing target; the symbol stays put.
                suggested_module = None  # No landing target; the symbol stays put.
                rationale = (  # Explicit rationale so the report clearly warns operators.
                    f"PINNED: `{defn.name}` must remain in the entrypoint because of "
                    f"module-load / bootstrap ordering; static analysis cannot detect this "
                    f"but moving it would break import wiring. Do NOT extract."
                )
            else:  # Normal reference-count-driven categorization.
                category = self._categorize(refs)  # Bucket by reference count.
                suggested_class, suggested_module, rationale = self._suggest_home(defn, refs)  # Landing target.
            candidates.append(  # Package the assembled candidate.
                Candidate(
                    definition=defn,  # Symbol being evaluated.
                    references=refs,  # Full reference list.
                    category=category,  # Bucket name.
                    suggested_class=suggested_class,  # Semantic class landing target.
                    suggested_module=suggested_module,  # File-level landing target.
                    move_rationale=rationale,  # Human-readable justification.
                    reference_files=reference_files,  # Refs grouped by file.
                    guideline_flags=flags,  # Pre-existing guideline violations.
                )
            )
        return candidates  # Return the ranked-shape output.

    @staticmethod
    def _index_nodes_by_name(tree: ast.Module) -> dict[str, ast.AST]:
        """Return {top_level_name: node} to hand the guideline scanner."""
        mapping: dict[str, ast.AST] = {}  # Name -> node lookup.
        for node in tree.body:  # Only top-level nodes matter here.
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):  # Named defs.
                mapping[node.name] = node  # Store the def node.
            elif (
                isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            ):  # Simple assign.
                mapping[node.targets[0].id] = node  # Store the assign node.
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):  # Typed assign.
                mapping[node.target.id] = node  # Store the annotated assign node.
        return mapping  # Return the assembled index.

    @staticmethod
    def _categorize(refs: list[Reference]) -> str:
        """Bucket by reference count: 0=unused, 1=single-use, 2-3=low-use, 4+=hot."""
        count = len(refs)  # Reference footprint size.
        if count == 0:  # No callers at all.
            return CATEGORY_UNUSED  # Dead code.
        if count == 1:  # Exactly one caller.
            return CATEGORY_SINGLE_USE  # Move it next to its sole caller.
        if count <= 3:  # 2 or 3 callers.
            return CATEGORY_LOW_USE  # Evaluate before moving.
        return CATEGORY_HOT  # 4+ callers; leave alone.

    @staticmethod
    def _group_refs_by_file(refs: list[Reference]) -> dict[str, list[Reference]]:
        """Group references by their file_path for PR-per-cluster rewrites."""
        grouped: dict[str, list[Reference]] = {}  # File -> list-of-refs mapping.
        for ref in refs:  # Bucket each reference by its file.
            grouped.setdefault(ref.file_path, []).append(ref)  # Append into the file's list.
        return grouped  # Return the assembled mapping.

    def _suggest_home(self, defn: Definition, refs: list[Reference]) -> tuple[str | None, str | None, str | None]:
        """Propose (class, module, rationale) for a move; None-triple for hot/unused."""
        category = self._categorize(refs)  # Bucket to decide suggestion strategy.
        if category == CATEGORY_UNUSED:  # No callers -> delete instead of moving.
            return (None, None, "No references found; delete this symbol rather than moving it")
        if category == CATEGORY_HOT:  # 4+ callers -> leave alone.
            return (None, None, "Widely used; leave in place until dependencies decouple")
        caller_files = Counter(ref.file_path for ref in refs)  # Ranking by file frequency.
        top_file, _top_count = caller_files.most_common(1)[0]  # Dominant caller file.
        enclosing_counter: Counter[str] = Counter(  # Rank enclosing symbols to guess class targets.
            ref.enclosing_symbol for ref in refs if ref.enclosing_symbol is not None
        )
        top_enclosing = enclosing_counter.most_common(1)[0][0] if enclosing_counter else None  # Dominant caller.
        if self._is_self_reference(top_file):  # Sole caller lives inside the entrypoint we're extracting FROM.
            return self._suggest_new_src_home(defn, category, top_enclosing)  # Propose a fresh /src landing target.
        suggested_module = self._suggested_module_for(top_file, top_enclosing)  # File-level landing target.
        suggested_class = self._suggested_class_for(top_enclosing, top_file, defn)  # Class-level landing target.
        rationale = self._rationale(category, top_file, top_enclosing, defn)  # Human-readable why.
        return (suggested_class, suggested_module, rationale)  # Full triple for the report.

    def _is_self_reference(self, caller_file: str) -> bool:
        """Return True when the dominant caller lives inside the entrypoint being analyzed."""
        if self._entrypoint_path is None:  # Defensive guard; should always be set during analyze().
            return False  # Cannot compare without an entrypoint path.
        try:
            return Path(caller_file).resolve() == self._entrypoint_path  # Compare canonical paths.
        except OSError:  # Path resolution can fail on missing intermediate dirs on Windows.
            logger.debug("Path resolve failed for %s", caller_file)  # Log for diagnosis.
            return False  # Fall back to non-self-reference behaviour.

    def _suggest_new_src_home(
        self, defn: Definition, category: str, enclosing_symbol: str | None
    ) -> tuple[str, str, str]:
        """Propose a brand-new /src module + class when the sole caller is the entrypoint itself."""
        snake_name = self._to_snake_case(defn.name)  # Snake-case module filename derived from symbol.
        suggested_module = f"src/refactors/{snake_name}.py"  # Landing target inside the extraction root.
        suggested_class = self._propose_class_name(defn)  # Fresh semantic class name for the new module.
        entrypoint_label = self._entrypoint_path.name if self._entrypoint_path else "the entrypoint"  # Friendly.
        enclosing_hint = (
            f" from `{enclosing_symbol}()`" if enclosing_symbol else ""
        )  # Optional context; backticks avoid markdown emphasis on dunders.
        rationale = (  # Explicit rationale that names the extraction intent.
            f"{category}: sole caller lives inside {entrypoint_label}{enclosing_hint}; "
            f"extract `{defn.name}` OUT of the entrypoint into a new `{suggested_module}` module "
            f"and rewrite the callsite(s) to import from there"
        )
        return (suggested_class, suggested_module, rationale)  # Return the assembled proposal.

    @classmethod
    def _propose_class_name(cls, defn: Definition) -> str:
        """Derive a semantic PascalCase class name from the symbol being extracted."""
        if defn.kind == "class":  # Already a class; reuse its name verbatim.
            return defn.name  # Class body relocates as-is.
        pascal = "".join(part.capitalize() for part in defn.name.split("_") if part)  # snake -> Pascal.
        pascal = pascal or defn.name.capitalize()  # Fallback if splitting produced nothing usable.
        return cls._append_manager_suffix(pascal)  # Append 'Manager' unless one is already there.

    @classmethod
    def _append_manager_suffix(cls, pascal: str) -> str:
        """Append 'Manager' to a PascalCase name unless a semantic suffix is already present."""
        for suffix in cls._CLASS_NAME_SUFFIXES:  # Check every known semantic suffix.
            if pascal.endswith(suffix):  # Already ends in Manager/Utils/Handler/etc.
                return pascal  # Keep as-is; do not double the suffix.
        return f"{pascal}Manager"  # No semantic suffix yet; append the default.

    @staticmethod
    def _to_snake_case(name: str) -> str:
        """Convert a PascalCase or camelCase name to snake_case for filenames."""
        if "_" in name and name == name.lower():  # Already snake_case with lowercase letters.
            return name  # Nothing to convert.
        buffer: list[str] = []  # Accumulate output characters.
        for index, char in enumerate(name):  # Iterate letter-by-letter.
            if char.isupper() and index > 0 and not name[index - 1].isupper():  # Word boundary detected.
                buffer.append("_")  # Insert separator before the uppercase letter.
            buffer.append(char.lower())  # Append the lowercased character.
        return "".join(buffer)  # Return the assembled snake_case string.

    @staticmethod
    def _suggested_module_for(caller_file: str, enclosing_symbol: str | None) -> str:
        """Return the target module path where the symbol should land."""
        caller_path = Path(caller_file)  # Convert to Path for portable manipulation.
        if enclosing_symbol and caller_path.suffix == ".py":  # Prefer the caller's file if it's a src module.
            return str(caller_path)  # Landing next to the sole caller is the default.
        return str(caller_path.with_suffix(".py"))  # Fallback: same file, ensured .py suffix.

    @classmethod
    def _suggested_class_for(cls, enclosing_symbol: str | None, caller_file: str, defn: Definition) -> str:
        """Return the semantic class name the move should land inside."""
        if enclosing_symbol and enclosing_symbol[:1].isupper():  # Caller looks like a class (PascalCase).
            return enclosing_symbol  # Use the existing class name directly.
        stem = Path(caller_file).stem  # File-name-derived class hint as a fallback.
        pascal = "".join(part.capitalize() for part in stem.split("_") if part)  # Convert snake_case -> Pascal.
        _ = defn  # Reserved for future definition-based hinting; keeps signature stable.
        return cls._append_manager_suffix(pascal) if pascal else "RefactoredManager"  # Avoid double-Manager.

    @staticmethod
    def _rationale(category: str, caller_file: str, enclosing_symbol: str | None, defn: Definition) -> str:
        """Compose a human-readable rationale sentence for the report."""
        caller_label = Path(caller_file).name  # Short caller filename for readability.
        if category == CATEGORY_SINGLE_USE:  # Exactly one caller anywhere.
            enclosing_hint = (
                f" inside `{enclosing_symbol}()`" if enclosing_symbol else ""
            )  # Backticks avoid markdown emphasis on dunders.
            return (
                f"Sole caller lives in `{caller_label}`{enclosing_hint}; "
                f"move `{defn.name}` into that module's semantic class so callers rewrite in one PR"
            )
        return (
            f"{len(caller_file)} caller files detected; group callers under a shared class "
            f"in `{caller_label}` and rewrite references per file cluster"
        )

    def _scan_guideline_flags(self, defn: Definition, node: ast.AST | None, source_lines: list[str]) -> list[str]:
        """Return the list of guideline violations present in the candidate's body."""
        flags: list[str] = []  # Accumulate flag identifiers.
        if defn.line_count > 25:  # 5-Item Rule: functions must stay under 25 physical lines.
            flags.append(FLAG_OVERSIZE)  # Flag oversize for downstream decomposition.
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and self._param_count(node) > 5:  # >5 params.
            flags.append(FLAG_TOO_MANY_PARAMS)  # 5-Item Rule: max 5 params.
        body_lines = source_lines[defn.lineno - 1 : defn.end_lineno]  # Physical lines for scanning.
        if not self._has_inline_comment_coverage(body_lines):  # Non-negotiable inline commenting.
            flags.append(FLAG_MISSING_INLINE_COMMENTS)  # Flag for the SpecKit spec to remediate.
        if not self._has_action_logging(body_lines):  # Non-negotiable action logging.
            flags.append(FLAG_MISSING_ACTION_LOGGING)  # Flag for the SpecKit spec to remediate.
        if self._has_non_ascii(body_lines):  # ASCII-only logs / literals.
            flags.append(FLAG_NON_ASCII_LOGS)  # Flag for cleanup during the move.
        if self._has_raw_input(body_lines):  # Bare input() must be safe_input().
            flags.append(FLAG_RAW_INPUT)  # Flag for cleanup during the move.
        if self._has_hardcoded_separator(body_lines):  # Path separator literal check.
            flags.append(FLAG_HARDCODED_SEPARATOR)  # Flag for pathlib migration during the move.
        return flags  # Return the assembled list.

    @staticmethod
    def _param_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        """Return total parameter count across positional/keyword-only slots."""
        args = node.args  # Cached reference to the arguments record.
        total = len(args.args) + len(args.kwonlyargs) + len(args.posonlyargs)  # Regular slots.
        if args.vararg:  # *args counts as one parameter.
            total += 1  # Add varargs.
        if args.kwarg:  # **kwargs counts as one parameter.
            total += 1  # Add kwargs.
        return total  # Report full arity.

    @staticmethod
    def _has_inline_comment_coverage(body_lines: list[str]) -> bool:
        """Return True when >=50% of executable lines carry an inline `#` comment."""
        code = [line for line in body_lines if line.strip() and not line.strip().startswith("#")]  # Exec lines.
        if not code:  # No executable lines to evaluate.
            return True  # Trivially satisfies the guideline.
        commented = sum(1 for line in code if "#" in line)  # Count lines with an inline hash.
        return commented / len(code) >= 0.5  # 50% threshold aligns with the project rule.

    @staticmethod
    def _has_action_logging(body_lines: list[str]) -> bool:
        """Return True when the body contains at least one logging.info/debug call."""
        joined = "\n".join(body_lines)  # Concatenate for a single substring scan.
        return (
            "logging.info(" in joined
            or "logging.debug(" in joined
            or "logger.info(" in joined
            or "logger.debug(" in joined
        )  # Common forms.

    @staticmethod
    def _has_non_ascii(body_lines: list[str]) -> bool:
        """Return True when any body line contains a non-ASCII character."""
        return any(any(ord(ch) > 127 for ch in line) for line in body_lines)  # Byte-scan each line.

    @staticmethod
    def _has_raw_input(body_lines: list[str]) -> bool:
        """Return True when the body calls bare input() instead of safe_input()."""
        return any("input(" in line and "safe_input(" not in line for line in body_lines)  # Naive but effective.

    @staticmethod
    def _has_hardcoded_separator(body_lines: list[str]) -> bool:
        """Return True when a body line contains a hardcoded path separator literal."""
        return any(
            ('"/' in line or "'/" in line or '"\\\\' in line or "'\\\\" in line) for line in body_lines
        )  # Heuristic.
