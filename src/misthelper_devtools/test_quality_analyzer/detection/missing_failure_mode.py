"""MissingFailureModeDetector (T036): flag uncovered HTTP failure modes.

The detector inspects a test module only when the source under test has an
inferred network or JSON body risk. It reads repository source imports instead
of a hand-added opt-in marker. Missing modes produce a `missing_fm_*` finding.

Sub-rules and markers:

- missing_fm_connection_timeout: `Timeout` / `ReadTimeout`.
- missing_fm_connection_error:   `ConnectionError` / `ConnectError`.
- missing_fm_http_4xx:           any 3-digit int literal 400-499 in source.
- missing_fm_http_5xx:           any 3-digit int literal 500-599 in source.
- missing_fm_malformed_json:     `JSONDecodeError`.
- missing_fm_empty_body:         empty body literal such as `b""` or `data=""`.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import ast  # AST inspection for import + literal scanning.
import copy  # Preserve the source tree while normalizing proved helper calls.
import logging  # Principle VII structured logging.
import re  # Regex for status numbers inside explicit HTTP exception messages.
from dataclasses import dataclass  # Store inferred failure-mode obligations.
from pathlib import Path  # Path metadata.

from misthelper_devtools.test_quality_analyzer.detection import (  # Registry + shared types.
    Category,
    DetectorRegistry,
    Finding,
    Severity,
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.

# Markers used to detect coverage of each failure mode.
# The detector accepts a match on ANY string in the tuple to consider the mode covered.
_TIMEOUT_MARKERS: tuple[str, ...] = ("Timeout", "ReadTimeout")  # Timeout exception names.
_CONNECTION_ERROR_MARKERS: tuple[str, ...] = ("ConnectionError", "ConnectError")  # Conn err names.
_MALFORMED_JSON_MARKERS: tuple[str, ...] = ("JSONDecodeError", "bad json")  # Malformed-JSON proof literals.
_EMPTY_BODY_MARKERS: tuple[str, ...] = ('b""', "b''", 'data=""', "data=''")  # Empty body literals.

_SOURCE_HTTP_STATUS_MODULES = frozenset(  # Libraries that can expose HTTP status failures to callers.
    {"aiohttp", "httpx", "mistapi", "requests"}  # Mistapi callers can inspect status_code without an exception.
)
_SOURCE_EXCEPTION_MODULES = frozenset({"aiohttp", "httpx", "requests"})  # Libraries that can raise to the caller.
_SOURCE_JSON_MODULES = frozenset({"json"})  # JSON parser modules that can fail on malformed bodies.


def _resolve_test_path(test_path: Path) -> Path:
    """Resolve a test path from the invocation directory before checking its project root."""
    return test_path.resolve() if test_path.is_absolute() else (Path.cwd() / test_path).resolve()


@dataclass
class FailureModeRisk:
    """Store failure modes that can occur in the source under test."""

    http_status: bool = False  # True when the source can expose HTTP 4xx and 5xx status results.
    network_exception: bool = False  # True when the source can raise connection failures to the caller.
    json_parse: bool = False  # True when the source can raise body parsing failures.
    sdk_connection_error: bool = False  # SDK token validation can expose connection errors, but not timeouts.

    @property
    def applies(self) -> bool:
        """Return True when the rule can measure this test module."""
        return (
            self.http_status or self.network_exception or self.json_parse or self.sdk_connection_error
        )  # Any source risk puts the module in scope.


@dataclass
class FailureModeCoverage:
    """Store failure modes that the test module exercises."""

    connection_timeout: bool = False  # True when a timeout path reaches the caller.
    connection_error: bool = False  # True when a connection path reaches the caller.
    http_4xx: bool = False  # True when a client error path reaches the caller.
    http_5xx: bool = False  # True when a server error path reaches the caller.
    malformed_json: bool = False  # True when invalid JSON reaches the caller.
    empty_body: bool = False  # True when an empty body reaches the caller.


@dataclass
class HttpStatusCoverage:
    """Store which HTTP status-code families a test exercises."""

    http_4xx: bool = False  # True when an AST status context contains a 4xx integer.
    http_5xx: bool = False  # True when an AST status context contains a 5xx integer.


class HttpStatusCoverageInferer(ast.NodeVisitor):
    """Infer HTTP status coverage from AST contexts, not raw source text."""

    _STATUS_WORDS = frozenset({"http", "status", "statuscode", "code"})  # Accepted status-context words.
    _EXCEPTION_NAMES = frozenset({"Exception", "RuntimeError", "ValueError"})  # Explicit failure-signal wrappers.
    _HTTP_STATUS_TEXT_RE = re.compile(r"\bHTTP\s+([45]\d\d)\b")  # Explicit HTTP status in an exception message.

    def __init__(self) -> None:
        """Initialize an empty coverage result."""
        self.coverage = HttpStatusCoverage()  # Accumulate one result while visiting the tree.
        self._status_arg_positions: dict[str, tuple[int, ...]] = {}  # Keep helper ownership in each signature key.
        self._integer_constants: dict[str, int] = {}  # Map module constants to status-code integers.

    @classmethod
    def from_source(cls, source: str) -> HttpStatusCoverage:
        """Return status coverage proved by syntax contexts in one test file."""
        try:
            tree = ast.parse(source)  # Parse so comments and unrelated strings cannot prove coverage.
        except SyntaxError:
            return HttpStatusCoverage()  # Keep malformed test text from fabricating status coverage.
        inferer = cls()  # Build a fresh visitor for one source file.
        inferer._status_arg_positions = inferer._collect_status_arg_positions(tree)  # Learn local helper signatures.
        inferer._integer_constants = inferer._collect_integer_constants(tree)  # Learn named status constants.
        inferer.visit(tree)  # Visit every status-related expression.
        return inferer.coverage  # Return the accumulated status families.

    def visit_Assign(self, node: ast.Assign) -> None:
        """Record status-code assignments such as `response.status_code = 503`."""
        if any(self._is_status_context(target) for target in node.targets):  # Require a status-like target.
            self._mark_status_family(node.value)  # Mark only integer values in that status context.
        self.generic_visit(node)  # Continue visiting nested expressions.

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Record annotated status-code assignments."""
        if node.value is not None and self._is_status_context(node.target):  # Require a status target and value.
            self._mark_status_family(node.value)  # Mark only integer values in that status context.
        self.generic_visit(node)  # Continue visiting nested expressions.

    def visit_Call(self, node: ast.Call) -> None:
        """Record status-code keyword arguments and pytest status parameters."""
        for keyword in node.keywords:  # Keyword arguments can bind a fake response status.
            if keyword.arg and self._is_status_name(keyword.arg):  # Require a status-like keyword name.
                self._mark_status_family(keyword.value)  # Mark only integer values in that status context.
        self._visit_status_position_args(node)  # Handle helpers such as `_response(503)`.
        self._visit_exception_status_text(node)  # Handle explicit `RuntimeError("HTTP 503")` signals.
        self._visit_parametrize_call(node)  # Handle `pytest.mark.parametrize("status_code", [503])`.
        self.generic_visit(node)  # Continue visiting nested expressions.

    def visit_Compare(self, node: ast.Compare) -> None:
        """Record status-code comparisons such as `response.status_code == 404`."""
        if self._is_status_context(node.left):  # Left side can be a status attribute or name.
            for comparator in node.comparators:  # Each comparison can carry a status integer.
                self._mark_status_family(comparator)  # Mark only integer values in that status context.
        for index, comparator in enumerate(node.comparators):  # Right side can also be the status expression.
            left_peer = node.left if index == 0 else node.comparators[index - 1]  # Pair with its left operand.
            if self._is_status_context(comparator):  # Require the compared expression to be status-like.
                self._mark_status_family(left_peer)  # Mark only integer values in the peer expression.
        self.generic_visit(node)  # Continue visiting nested expressions.

    def visit_Dict(self, node: ast.Dict) -> None:
        """Record mapping entries such as `{\"status_code\": 503}`."""
        for key, value in zip(node.keys, node.values, strict=True):  # Pair each key with its value.
            if self._constant_string(key) and self._is_status_name(str(self._constant_string(key))):  # Status key.
                self._mark_status_family(value)  # Mark only integer values in that status context.
        self.generic_visit(node)  # Continue visiting nested expressions.

    def _visit_parametrize_call(self, node: ast.Call) -> None:
        """Record pytest parameters whose argument name is status-like."""
        if not self._is_parametrize_call(node):  # Only pytest parametrize assigns literal values to parameters.
            return
        if len(node.args) < 2:  # Parametrize needs names and values before it can prove coverage.
            return
        names = self._parametrize_names(node.args[0])  # Read the parameter names from the first argument.
        if not any(self._is_status_name(name) for name in names):  # Require at least one status-like parameter.
            return
        self._mark_status_family(node.args[1])  # Mark integer values assigned to the status parameter set.

    def _visit_status_position_args(self, node: ast.Call) -> None:
        """Record positional arguments passed to module or qualified class status helpers."""
        call_name = self._status_call_key(node.func)  # Keep same-named class helpers distinct.
        if call_name not in self._status_arg_positions:  # Unknown helpers do not prove a status context.
            return
        for position in self._status_arg_positions[call_name]:  # Only parameters named for status can prove coverage.
            if position < len(node.args):  # The call supplied this positional argument.
                self._mark_status_family(node.args[position])  # Mark only integer values in that status context.

    def _visit_exception_status_text(self, node: ast.Call) -> None:
        """Record explicit HTTP status text passed to an exception constructor."""
        if self._call_name(node.func) not in self._EXCEPTION_NAMES:  # Keep unrelated strings out of coverage.
            return
        for argument in node.args:  # Exception messages are positional in the existing tests.
            text = self._constant_string(argument)  # Read only literal strings, never comments.
            if text:
                self._mark_status_text(text)  # Mark only explicit `HTTP 503` style messages.

    def _collect_status_arg_positions(self, tree: ast.Module) -> dict[str, tuple[int, ...]]:
        """Return status parameter positions with class-qualified method identities."""
        positions: dict[str, tuple[int, ...]] = {}  # Store only helpers with status-like parameters.
        for node in tree.body:  # Preserve the existing module-helper contract.
            if isinstance(node, ast.FunctionDef):  # Module helpers retain their simple call names.
                self._add_status_positions(positions, node.name, node)  # Record a module-level helper.
            if isinstance(node, ast.ClassDef):  # Class methods require their owning class in the key.
                for member in node.body:  # Ignore unrelated nested classes and module definitions.
                    if isinstance(member, ast.FunctionDef):  # Only direct class methods can be resolved.
                        self._add_status_positions(positions, f"{node.name}.{member.name}", member)
        return positions  # Return the completed, ownership-aware map.

    def _add_status_positions(
        self, positions: dict[str, tuple[int, ...]], identity: str, node: ast.FunctionDef
    ) -> None:
        """Add one helper signature when a positional parameter names a status."""
        status_positions = tuple(  # Keep only positional arguments with status-like names.
            index for index, argument in enumerate(node.args.args) if self._is_status_name(argument.arg)
        )
        if status_positions:  # Helpers without status parameters cannot establish coverage.
            positions[identity] = status_positions  # Preserve the caller's full helper identity.

    def _status_call_key(self, node: ast.AST) -> str:
        """Return a key only for bare module calls or explicit class-qualified calls."""
        if isinstance(node, ast.Name):  # Keep existing module-level helper matching.
            return node.id
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            return ""
        identity = f"{node.value.id}.{node.attr}"  # Require explicit class qualification, not an object attribute.
        return identity if identity in self._status_arg_positions else ""

    def _collect_integer_constants(self, tree: ast.Module) -> dict[str, int]:
        """Return module constants that bind one integer value."""
        constants: dict[str, int] = {}  # Build lookup for status constants such as HTTP_SERVER_ERROR.
        for node in tree.body:  # Only module-level constants describe shared status values.
            if not isinstance(node, ast.Assign):  # Keep annotated and computed values outside this narrow proof.
                continue
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, int):
                continue
            if isinstance(node.value.value, bool):  # Boolean constants are not status codes.
                continue
            for target in node.targets:  # One assignment can bind several names.
                if isinstance(target, ast.Name):  # Only plain names are safe to resolve later.
                    constants[target.id] = node.value.value  # Save the literal value for coverage inference.
        return constants  # Return constants for this one test module.

    def _is_parametrize_call(self, node: ast.Call) -> bool:
        """Return True when a call is `pytest.mark.parametrize` or `.parametrize`."""
        func = node.func  # Keep the call target in a local for readable checks.
        if isinstance(func, ast.Attribute) and func.attr == "parametrize":  # Covers pytest.mark.parametrize.
            return True
        return isinstance(func, ast.Name) and func.id == "parametrize"  # Covers an imported helper.

    def _call_name(self, node: ast.AST) -> str:
        """Return the simple name of a called function or constructor."""
        if isinstance(node, ast.Name):  # Plain call such as `RuntimeError(...)`.
            return node.id
        if isinstance(node, ast.Attribute):  # Qualified call such as `pytest.raises(...)`.
            return node.attr
        return ""  # Other call forms do not identify a local helper.

    def _parametrize_names(self, node: ast.AST) -> tuple[str, ...]:
        """Return the parameter names from a pytest parametrize name expression."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):  # Common comma-separated string form.
            return tuple(name.strip() for name in node.value.split(",") if name.strip())
        if isinstance(node, (ast.List, ast.Tuple)):  # Pytest also accepts a list or tuple of names.
            return tuple(str(value) for item in node.elts if (value := self._constant_string(item)))
        return ()  # Unknown name forms do not prove status coverage.

    def _is_status_context(self, node: ast.AST) -> bool:
        """Return True when an expression names an HTTP status or code."""
        if isinstance(node, ast.Name):  # Plain variable such as `status_code`.
            return self._is_status_name(node.id)
        if isinstance(node, ast.Attribute):  # Attribute such as `response.status_code`.
            return self._is_status_name(node.attr)
        if isinstance(node, ast.Subscript):  # Mapping access such as `row["status_code"]`.
            key = self._constant_string(node.slice)  # Read only literal keys.
            return bool(key and self._is_status_name(str(key)))
        return False  # Other expressions do not name a status target.

    def _is_status_name(self, name: str) -> bool:
        """Return True when a name token identifies a status-code context."""
        normalized = name.lower().replace("-", "_")  # Normalize common separators.
        parts = tuple(part for part in normalized.split("_") if part)  # Split snake-case names into tokens.
        compact = "".join(parts)  # Also recognize `statusCode` after lowercasing.
        return compact in self._STATUS_WORDS or any(part in self._STATUS_WORDS for part in parts)

    def _mark_status_family(self, node: ast.AST) -> None:
        """Mark each 4xx or 5xx integer literal contained in a status context."""
        for value in self._integer_literals(node):  # Walk nested containers, such as parametrize lists.
            if 400 <= value <= 499:  # Client-error status code.
                self.coverage.http_4xx = True  # Mark client-error coverage.
            if 500 <= value <= 599:  # Server-error status code.
                self.coverage.http_5xx = True  # Mark server-error coverage.

    def _mark_status_text(self, text: str) -> None:
        """Mark each explicit HTTP status value contained in an exception message."""
        for match in self._HTTP_STATUS_TEXT_RE.finditer(text):  # Only explicit HTTP status text is accepted.
            value = int(match.group(1))  # Convert the matched status digits to an integer.
            if 400 <= value <= 499:  # Client-error status code.
                self.coverage.http_4xx = True  # Mark client-error coverage.
            if 500 <= value <= 599:  # Server-error status code.
                self.coverage.http_5xx = True  # Mark server-error coverage.

    def _integer_literals(self, node: ast.AST) -> tuple[int, ...]:
        """Return all integer literals nested inside an AST node."""
        values: list[int] = []  # Accumulate status-code candidates.
        if isinstance(node, ast.Name) and node.id in self._integer_constants:
            values.append(self._integer_constants[node.id])  # Resolve shared status constants in status contexts.
        for child in ast.walk(node):  # Walk nested tuples, lists, and calls.
            if isinstance(child, ast.Constant) and isinstance(child.value, int) and not isinstance(child.value, bool):
                values.append(child.value)  # Keep plain integer literals only.
            if isinstance(child, ast.Name) and child.id in self._integer_constants:
                values.append(self._integer_constants[child.id])  # Resolve constants inside tuples or calls.
        return tuple(values)  # Return an immutable sequence to callers.

    def _constant_string(self, node: ast.AST | None) -> str | None:
        """Return a literal string from an AST node when one exists."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):  # Plain string literal.
            return node.value
        return None  # Non-literal keys and names do not prove status context.


class FailureModeApplicabilityInferer:
    """Infer failure-mode scope from the source code that a test imports."""

    def __init__(self, test_path: Path, tree: ast.Module) -> None:
        """Build source lookup state for one test module."""
        self._test_path = test_path  # Keep the test path so source roots can be resolved.
        self._tree = tree  # Keep the parsed test module for import inspection.
        self._root = self._repository_root(test_path)  # Resolve imports against the nearest source root.
        self._source_cache: dict[Path, tuple[ast.Module, str] | None] = {}  # Avoid reparsing source modules.

    def infer(self) -> FailureModeRisk:
        """Return the failure-mode risk that imported source code exposes."""
        risk = FailureModeRisk()  # Accumulate risk across all source imports.
        self._merge(risk, self._local_source_risk())  # Analyzer fixtures can define their SUT locally.
        for node in self._tree.body:  # Only module imports define the test contract.
            if isinstance(node, ast.ImportFrom) and node.module:  # Direct imports can name source symbols.
                self._merge(risk, self._risk_from_import_from(node))  # Add risk from imported callables.
            if isinstance(node, ast.Import):  # Module imports can expose source functions by attribute.
                self._merge(risk, self._risk_from_import(node))  # Add risk from imported modules.
        return risk  # Return the measured source risk.

    def _risk_from_import_from(self, node: ast.ImportFrom) -> FailureModeRisk:
        risk = FailureModeRisk()  # Accumulate risk for one import statement.
        module_path = self._module_path(node.module or "")  # Resolve only repository source modules.
        module_source = self._source_tree(module_path)  # Parse the module once when it exists.
        for alias in node.names:  # Each imported symbol can be the source under test.
            if module_source is not None:
                tree, source = module_source
                self._merge(risk, self._risk_from_symbol(tree, source, alias.name, alias.asname or alias.name))
            submodule_path = self._module_path(f"{node.module}.{alias.name}")
            self._merge(risk, self._risk_from_module_alias(submodule_path, alias.asname or alias.name))
        return risk  # Return merged risk for the statement.

    def _risk_from_import(self, node: ast.Import) -> FailureModeRisk:
        risk = FailureModeRisk()  # Accumulate risk for one module import statement.
        for alias in node.names:  # One import statement can import several modules.
            module_path = self._module_path(alias.name)  # Resolve only repository source modules.
            alias_name = alias.asname or alias.name.split(".")[-1]  # Tests call this module name.
            self._merge(risk, self._risk_from_module_alias(module_path, alias_name))  # Inspect called symbols.
        return risk  # Return merged risk for the statement.

    def _risk_from_symbol(
        self, tree: ast.Module, source: str, symbol: str, imported_name: str | None = None
    ) -> FailureModeRisk:
        for node in tree.body:  # Top-level symbols hold public functions and classes.
            if isinstance(node, ast.FunctionDef) and node.name == symbol:  # Direct function import.
                return self._risk_from_source(ast.get_source_segment(source, node) or "", tree)  # Keep SDK aliases.
            if isinstance(node, ast.ClassDef) and node.name == symbol:  # Direct class import.
                if imported_name is not None:
                    selected = self._static_class_source(tree, node, imported_name)
                    if selected is not None:
                        return self._risk_from_source(selected, tree)
                return self._risk_from_source(ast.get_source_segment(source, node) or "", tree)  # Keep SDK aliases.
        return FailureModeRisk()  # Constants and unknown names do not prove network or parse risk.

    def _static_class_source(self, tree: ast.Module, node: ast.ClassDef, imported_name: str) -> str | None:
        """Limit explicit static calls while keeping uncertain instance and class behavior conservative."""
        if node.bases or node.decorator_list or node.keywords:
            return None
        slicer = SourceDrivenCoverageSlicer(self._test_path, self._tree)
        if any(
            slicer._statement_bindings(statement) & {node.name, "staticmethod", "classmethod"}
            for statement in tree.body
            if statement is not node
        ):
            return None
        parents = {id(child): parent for parent in ast.walk(self._tree) for child in ast.iter_child_nodes(parent)}
        names: set[str] = set()
        for reference in ast.walk(self._tree):
            if (
                not isinstance(reference, ast.Name)
                or reference.id != imported_name
                or not isinstance(reference.ctx, ast.Load)
            ):
                continue
            attribute = parents.get(id(reference))
            call = parents.get(id(attribute)) if attribute is not None else None
            if not isinstance(attribute, ast.Attribute) or not isinstance(call, ast.Call) or call.func is not attribute:
                return None
            names.add(attribute.attr)
        if not names:
            return None
        methods = {member.name: member for member in node.body if isinstance(member, ast.FunctionDef)}
        if len(methods) != sum(isinstance(member, ast.FunctionDef) for member in node.body):
            return None
        if any(not isinstance(member, (ast.FunctionDef, ast.Pass, ast.Expr)) for member in node.body):
            return None
        for method in methods.values():
            if method.args.defaults or method.args.kw_defaults or method.returns is not None:
                return None
            if any(
                argument.annotation is not None for argument in ast.walk(method.args) if isinstance(argument, ast.arg)
            ):
                return None
            if any(
                not isinstance(decorator, ast.Name) or decorator.id not in {"staticmethod", "classmethod"}
                for decorator in method.decorator_list
            ):
                return None
        selected = self._static_method_closure(node.name, methods, names)
        if selected is None:
            return None
        return "\n".join(
            ast.unparse(member)
            for member in node.body
            if not isinstance(member, ast.FunctionDef) or member.name in selected
        )

    def _static_method_closure(
        self, class_name: str, methods: dict[str, ast.FunctionDef], pending: set[str]
    ) -> set[str] | None:
        """Follow named static helpers and reject unresolved class-member access."""
        selected: set[str] = set()
        while pending:
            name = pending.pop()
            if name in selected:
                continue
            method = methods.get(name)
            if method is None or len(method.decorator_list) != 1:
                return None
            decorator = method.decorator_list[0]
            if not isinstance(decorator, ast.Name) or decorator.id != "staticmethod":
                return None
            selected.add(name)
            for child in ast.walk(method):
                if isinstance(child, ast.Name) and child.id == class_name:
                    if not any(
                        isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.value is child
                        for call in ast.walk(method)
                    ):
                        return None
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    if isinstance(child.func.value, ast.Name) and child.func.value.id == class_name:
                        pending.add(child.func.attr)
        return selected

    def _risk_from_module_alias(self, module_path: Path | None, alias_name: str) -> FailureModeRisk:
        risk = FailureModeRisk()  # Accumulate risk for symbols called through a module alias.
        module_source = self._source_tree(module_path) if module_path is not None else None  # Parse if possible.
        if module_source is None:  # Unknown modules cannot prove source-under-test behavior.
            return risk  # Keep the module outside scope.
        tree, source = module_source  # Split the source module into parsed and raw forms.
        slicer = SourceDrivenCoverageSlicer(self._test_path, self._tree)
        if self._module_scope_replaces(self._tree, alias_name, slicer):
            return risk
        for call in self._module_alias_calls(alias_name, slicer):
            if isinstance(call.func, ast.Attribute) and self._root_name(call.func.value) == alias_name:  # Alias call.
                self._merge(risk, self._risk_from_symbol(tree, source, call.func.attr))  # Inspect called symbol.
        return risk  # Return only risk from symbols the test calls.

    def _module_alias_calls(self, alias_name: str, slicer: SourceDrivenCoverageSlicer) -> list[ast.Call]:
        """Find module calls without accepting test parameters or replacement bindings."""
        calls: list[ast.Call] = []
        scopes: list[ast.Module | ast.FunctionDef] = [
            self._tree,
            *(node for node in ast.walk(self._tree) if isinstance(node, ast.FunctionDef)),
        ]
        for scope in scopes:
            if self._module_scope_replaces(scope, alias_name, slicer):
                continue
            for statement in scope.body:
                calls.extend(self._module_expression_calls(statement, alias_name))
        return calls

    def _module_scope_replaces(
        self, scope: ast.Module | ast.FunctionDef, alias_name: str, slicer: SourceDrivenCoverageSlicer
    ) -> bool:
        """Check enclosing replacements without collecting expression-local targets."""
        if isinstance(scope, ast.FunctionDef):
            arguments = [*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs]
            arguments.extend(argument for argument in (scope.args.vararg, scope.args.kwarg) if argument is not None)
            if any(argument.arg == alias_name for argument in arguments):
                return True
        for node in self._sdk_scope_nodes(scope.body):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == alias_name:
                return True
            if isinstance(node, (ast.Import, ast.ImportFrom)) and alias_name in slicer._non_source_bindings(node):
                return True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == alias_name:
                return True
            if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name == alias_name:
                return True
            if isinstance(node, ast.MatchMapping) and node.rest == alias_name:
                return True
        return False

    def _module_expression_calls(self, node: ast.AST, alias_name: str) -> list[ast.Call]:
        """Visit expression calls with lambda and comprehension alias visibility."""
        if isinstance(node, ast.FunctionDef):
            expressions = [
                *node.decorator_list,
                *node.args.defaults,
                *(value for value in node.args.kw_defaults if value is not None),
            ]
            return [call for value in expressions for call in self._module_expression_calls(value, alias_name)]
        if isinstance(node, ast.ClassDef):
            return self._module_class_calls(node, alias_name)
        if isinstance(node, ast.AsyncFunctionDef):
            return []
        if isinstance(node, ast.Lambda):
            defaults = [*node.args.defaults, *(value for value in node.args.kw_defaults if value is not None)]
            calls = [call for value in defaults for call in self._module_expression_calls(value, alias_name)]
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            arguments.extend(argument for argument in (node.args.vararg, node.args.kwarg) if argument is not None)
            if not any(argument.arg == alias_name for argument in arguments):
                calls.extend(self._module_expression_calls(node.body, alias_name))
            return calls
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return self._module_comprehension_calls(node, alias_name)
        calls = [node] if isinstance(node, ast.Call) else []
        calls.extend(
            call for child in ast.iter_child_nodes(node) for call in self._module_expression_calls(child, alias_name)
        )
        return calls

    def _module_class_calls(self, node: ast.ClassDef, alias_name: str) -> list[ast.Call]:
        """Read definition expressions before class statements replace their enclosing module alias."""
        expressions = [*node.decorator_list, *node.bases, *(keyword.value for keyword in node.keywords)]
        calls = [call for value in expressions for call in self._module_expression_calls(value, alias_name)]
        slicer = SourceDrivenCoverageSlicer(self._test_path, self._tree)
        for statement in node.body:
            calls.extend(self._module_expression_calls(statement, alias_name))
            scope = ast.Module(body=[statement], type_ignores=[])
            if self._module_scope_replaces(scope, alias_name, slicer):
                break
        return calls

    def _module_comprehension_calls(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp, alias_name: str
    ) -> list[ast.Call]:
        """Check each iterable before its target changes comprehension-local visibility."""
        calls: list[ast.Call] = []
        for generator in node.generators:
            calls.extend(self._module_expression_calls(generator.iter, alias_name))
            if any(isinstance(target, ast.Name) and target.id == alias_name for target in ast.walk(generator.target)):
                return calls
            calls.extend(
                call for condition in generator.ifs for call in self._module_expression_calls(condition, alias_name)
            )
        values = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        calls.extend(call for value in values for call in self._module_expression_calls(value, alias_name))
        return calls

    def _local_source_risk(self) -> FailureModeRisk:
        if not self._allows_local_source():  # Real test helpers must not create scope by themselves.
            return FailureModeRisk()  # Only imported production source can put real tests in scope.
        source = "\n".join(ast.unparse(node) for node in self._tree.body if not self._is_test_node(node))  # Local SUT.
        return self._risk_from_source(source)  # Analyzer fixtures keep their historical coverage contract.

    def _allows_local_source(self) -> bool:
        resolved = _resolve_test_path(self._test_path)
        try:
            resolved.relative_to((self._root / "tests").resolve())  # Real tests usually define local helpers.
            return False  # Do not infer failure-mode scope from real test helper functions.
        except ValueError:
            return True  # Analyzer fixtures and synthetic tests can define a local source under test.

    def _risk_from_source(self, source: str, module_tree: ast.Module | None = None) -> FailureModeRisk:
        try:
            tree = ast.parse(source)  # Parse the source slice so docstrings do not create false scope.
        except SyntaxError:
            return FailureModeRisk()  # Unparseable slices cannot establish applicability.
        http_status = any(self._node_has_http_status_risk(node) for node in ast.walk(tree))  # Find response risks.
        network_exception = any(self._node_has_exception_risk(node) for node in ast.walk(tree))  # Find raised risks.
        json_parse = any(self._node_has_json_risk(node) for node in ast.walk(tree))  # Find executable JSON parsing.
        sdk_connection_error = self._has_sdk_constructor(tree, module_tree or tree)
        return FailureModeRisk(  # Return each failure channel separately so unreachable exceptions stay out of scope.
            http_status=http_status,  # HTTP status remains visible through mistapi APIResponse objects.
            network_exception=network_exception,  # Mistapi swallows request exceptions before callers can catch them.
            json_parse=json_parse,  # Empty and malformed body findings require an in-process parser.
            sdk_connection_error=sdk_connection_error,
        )

    def _has_sdk_constructor(self, tree: ast.Module, module_tree: ast.Module) -> bool:
        """Find SDK constructors using imports visible in each calling scope."""
        bindings = self._sdk_scope_bindings(module_tree.body, {})
        return self._sdk_scope_calls(tree.body, bindings)

    def _sdk_scope_nodes(self, nodes: list[ast.stmt]) -> list[ast.AST]:
        """Visit compound statements without entering a different lexical scope."""
        queue: list[ast.AST] = list(nodes)
        scoped: list[ast.AST] = []
        while queue:
            node = queue.pop()
            scoped.append(node)
            if not isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Lambda,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                ),
            ):
                queue.extend(ast.iter_child_nodes(node))
        return scoped

    def _sdk_import_bindings(self, node: ast.AST) -> dict[str, str]:
        """Map imported names to SDK module, SDK constructor, or unrelated identities."""
        if isinstance(node, ast.Import):
            return {
                alias.asname
                or alias.name.split(".")[0]: (
                    "module"
                    if alias.name == "mistapi" or (alias.name.startswith("mistapi.") and not alias.asname)
                    else ""
                )
                for alias in node.names
            }
        if isinstance(node, ast.ImportFrom):
            return {
                alias.asname
                or alias.name: ("constructor" if node.module == "mistapi" and alias.name == "APISession" else "")
                for alias in node.names
            }
        return {}

    def _sdk_scope_bindings(self, nodes: list[ast.stmt], inherited: dict[str, str]) -> dict[str, str]:
        """Keep imports only when no competing binding replaces their local names."""
        bindings = dict(inherited)
        imports: dict[str, set[str]] = {}
        replaced: set[str] = set()
        for node in self._sdk_scope_nodes(nodes):
            for name, identity in self._sdk_import_bindings(node).items():
                imports.setdefault(name, set()).add(identity)
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                replaced.add(node.id)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                replaced.add(node.name)
            if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
                replaced.add(node.name)
            if isinstance(node, ast.MatchMapping) and node.rest:
                replaced.add(node.rest)
        bindings.update({name: next(iter(values)) if len(values) == 1 else "" for name, values in imports.items()})
        return {name: identity for name, identity in bindings.items() if identity and name not in replaced}

    def _sdk_scope_calls(self, nodes: list[ast.stmt], inherited: dict[str, str], class_scope: bool = False) -> bool:
        """Check calls and descend into functions with parameter-aware import bindings."""
        bindings = self._sdk_scope_bindings(nodes, inherited)
        for index, statement in enumerate(nodes):
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                restored = self._sdk_import_bindings(statement)
                for name, identity in restored.items():
                    if (
                        identity
                        and bindings.get(name) != identity
                        and self._sdk_scope_calls(nodes[index + 1 :], {**bindings, name: identity}, class_scope)
                    ):
                        return True
        for node in self._sdk_scope_nodes(nodes):
            if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                if self._sdk_expression_calls(node, bindings):
                    return True
            if isinstance(node, ast.Call) and self._is_sdk_constructor_call(node, bindings):
                return True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parameters = {argument.arg for argument in ast.walk(node.args) if isinstance(argument, ast.arg)}
                local = self._sdk_scope_bindings(node.body, inherited if class_scope else bindings)
                local = {name: identity for name, identity in local.items() if name not in parameters}
                if self._sdk_scope_calls(node.body, local):
                    return True
            if isinstance(node, ast.ClassDef) and self._sdk_scope_calls(node.body, inherited, class_scope=True):
                return True
        return False

    def _sdk_expression_calls(self, node: ast.AST, bindings: dict[str, str]) -> bool:
        """Resolve lambda defaults and comprehension targets in their own expression scopes."""
        if isinstance(node, ast.Lambda):
            defaults = [*node.args.defaults, *(value for value in node.args.kw_defaults if value is not None)]
            if any(self._sdk_expression_calls(value, bindings) for value in defaults):
                return True
            parameters = {argument.arg for argument in ast.walk(node.args) if isinstance(argument, ast.arg)}
            local = {name: identity for name, identity in bindings.items() if name not in parameters}
            return self._sdk_expression_calls(node.body, local)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            local = dict(bindings)
            for generator in node.generators:
                if self._sdk_expression_calls(generator.iter, local):
                    return True
                targets = {child.id for child in ast.walk(generator.target) if isinstance(child, ast.Name)}
                local = {name: identity for name, identity in local.items() if name not in targets}
                if any(self._sdk_expression_calls(condition, local) for condition in generator.ifs):
                    return True
            values = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            return any(self._sdk_expression_calls(value, local) for value in values)
        if isinstance(node, ast.Call) and self._is_sdk_constructor_call(node, bindings):
            return True
        return any(self._sdk_expression_calls(child, bindings) for child in ast.iter_child_nodes(node))

    def _is_sdk_constructor_call(self, node: ast.Call, bindings: dict[str, str]) -> bool:
        """Match a constructor only against its resolved import identity."""
        if isinstance(node.func, ast.Name):
            return bindings.get(node.func.id) == "constructor"
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "APISession"
            and isinstance(node.func.value, ast.Name)
            and bindings.get(node.func.value.id) == "module"
        )

    def _node_has_http_status_risk(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Import):  # Direct imports identify source dependencies.
            return any(self._module_has_http_status_risk(alias.name) for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:  # From-imports identify source dependencies.
            if node.module == "mistapi":
                return any(self._module_has_http_status_risk(f"mistapi.{alias.name}") for alias in node.names)
            return self._module_has_http_status_risk(node.module)
        if isinstance(node, ast.Attribute):  # Dotted names catch annotations and direct SDK calls.
            parts: list[str] = []
            current: ast.AST = node
            while isinstance(current, ast.Attribute):
                parts.insert(0, current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                return self._module_has_http_status_risk(".".join([current.id, *parts]))
            return False
        if isinstance(node, ast.Constant) and isinstance(node.value, str):  # Dynamic imports often use strings.
            return "mistapi.api." in node.value  # Mist SDK import paths mean cloud API scope.
        return False  # Other syntax does not prove an HTTP status result.

    def _module_has_http_status_risk(self, module_name: str) -> bool:
        """Distinguish SDK logging support from modules that can expose HTTP responses."""
        parts = module_name.split(".")
        if parts[:2] == ["mistapi", "__logger"]:
            return False  # SDK log sanitization does not perform an HTTP request.
        return parts[0] in _SOURCE_HTTP_STATUS_MODULES

    def _node_has_exception_risk(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Import):  # Direct imports identify exception-raising clients.
            return any(alias.name.split(".")[0] in _SOURCE_EXCEPTION_MODULES for alias in node.names)  # Direct client.
        if isinstance(node, ast.ImportFrom) and node.module:  # From-imports identify exception-raising clients.
            return node.module.split(".")[0] in _SOURCE_EXCEPTION_MODULES  # Direct client module.
        if isinstance(node, ast.Attribute):  # Dotted names catch direct requests/httpx/aiohttp calls.
            return self._root_name(node) in _SOURCE_EXCEPTION_MODULES  # Mistapi is excluded because it catches them.
        return False  # Other syntax does not prove an exception reaches the caller.

    def _node_has_json_risk(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):  # Require parser execution.
            return False  # Attribute names alone do not prove JSON parsing.
        if node.func.attr == "loads":  # json.loads parses response bodies.
            return self._root_name(node.func) in _SOURCE_JSON_MODULES  # Require the json module root.
        return node.func.attr == "json"  # response.json() can raise JSONDecodeError.

    def _root_name(self, node: ast.AST) -> str:
        current = node  # Walk left through an attribute chain.
        while isinstance(current, ast.Attribute):  # Attributes hold dotted module or object paths.
            current = current.value  # Move toward the root name.
        return current.id if isinstance(current, ast.Name) else ""  # Return a comparable root name.

    def _module_path(self, module_name: str) -> Path | None:
        candidate = self._root / Path(*module_name.split(".")).with_suffix(".py")  # Build a portable source path.
        return candidate if candidate.exists() else None  # Only repository modules are valid evidence.

    def _source_tree(self, module_path: Path | None) -> tuple[ast.Module, str] | None:
        if module_path is None:  # Unknown modules cannot establish source-under-test behavior.
            return None  # The detector must not guess from third-party imports.
        if module_path not in self._source_cache:  # Parse each module at most once.
            self._source_cache[module_path] = self._parse_source(module_path)  # Cache success or failure.
        return self._source_cache[module_path]  # Return the cached parse result.

    def _parse_source(self, module_path: Path) -> tuple[ast.Module, str] | None:
        try:
            source = module_path.read_text(encoding="utf-8")  # Read source so AST nodes can map to text.
            return ast.parse(source, filename=str(module_path)), source  # Return parsed and raw source.
        except (OSError, SyntaxError) as exc:
            _LOGGER.debug("Cannot inspect failure-mode source %s: %s", module_path, exc)  # Explain skipped source.
            return None  # Unreadable source cannot establish applicability.

    def _repository_root(self, test_path: Path) -> Path:
        resolved = (Path.cwd() / test_path).resolve() if not test_path.is_absolute() else test_path.resolve()
        for parent in (resolved.parent, *resolved.parents):  # Walk upward from the test module path.
            if (parent / "pyproject.toml").exists() and (parent / "src").exists():  # Find the nearest project root.
                return parent  # Nested projects must resolve their own source imports.
        return Path.cwd()  # Fall back to the current checkout used by the CLI.

    def _is_test_node(self, node: ast.AST) -> bool:
        return isinstance(node, ast.FunctionDef) and node.name.startswith("test_")  # Exclude pytest cases.

    def _merge(self, target: FailureModeRisk, source: FailureModeRisk) -> None:
        target.http_status = target.http_status or source.http_status  # Preserve any HTTP status risk already found.
        target.network_exception = target.network_exception or source.network_exception  # Preserve raised network risk.
        target.sdk_connection_error = target.sdk_connection_error or source.sdk_connection_error
        target.json_parse = target.json_parse or source.json_parse  # Preserve any parse risk already found.


class SourceDrivenCoverageSlicer:
    """Return only tests that drive repository source under test."""

    def __init__(self, test_path: Path, tree: ast.Module) -> None:
        """Build source-call lookup state for one test module."""
        self._test_path = test_path  # Keep the file path so fixture files can use local source functions.
        self._tree = tree  # Keep the parsed test module for import and call inspection.
        self._root = FailureModeApplicabilityInferer(test_path, tree)._repository_root(test_path)  # Reuse root logic.
        self._source_class_names = self._collect_imported_source_class_names()  # Require imported source class proof.
        self._pytest_fixtures = self._collect_pytest_fixtures()  # Resolve names using pytest decorator semantics.
        self._source_fixtures = self._collect_source_fixtures()  # Keep fixtures with executable source returns.
        self._test_class_owners = self._collect_test_class_owners()  # Retain class identity for static helpers.
        self._source_call_roots = self._collect_source_call_roots()  # Resolve imported source aliases once.
        self._local_source_names = self._collect_local_source_names()  # Resolve fixture-local SUT names once.
        self._source_driver_names = self._collect_source_driver_names()  # Resolve local helpers that call source.
        self._top_level_defs = self._collect_top_level_defs()  # Resolve local helpers, fakes, and constants once.

    def source_text(self) -> str:
        """Return source text from test functions that call source under test."""
        test_sources = [self._source_with_dependencies(node) for node in self._test_nodes() if self._calls_source(node)]
        return "\n\n".join(test_sources)  # Join only source-driving tests for coverage inference.

    def _source_with_dependencies(self, node: ast.FunctionDef) -> str:
        """Return a test function plus local definitions that it uses."""
        owner = self._test_class_owners.get(id(node))  # Keep a method's class identity for helper resolution.
        class_helpers = self._class_helper_nodes(owner, node)  # Select only static helpers called by this method.
        test_node = copy.deepcopy(node)  # Normalize proved static calls without changing the analyzed tree.
        self._normalize_class_helper_calls(test_node, owner, {helper.name for helper in class_helpers})
        dependencies = self._dependency_nodes(node, class_helpers, {owner.name} if owner else set())
        nodes = [*dependencies]  # Put module helper definitions before class-scoped helpers.
        if owner and class_helpers:  # Keep only referenced members, never the full enclosing test class.
            class_body: list[ast.stmt] = [*class_helpers]
            nodes.append(ast.ClassDef(name=owner.name, bases=[], keywords=[], body=class_body, decorator_list=[]))
        nodes.append(test_node)  # Preserve only the selected test function or method.
        return "\n\n".join(ast.unparse(item) for item in nodes)  # Convert the closure back to Python source.

    def _dependency_nodes(
        self, node: ast.AST, extra_nodes: list[ast.FunctionDef], excluded_names: set[str]
    ) -> list[ast.stmt]:
        """Return top-level definitions referenced by a node, recursively."""
        dependencies: list[ast.stmt] = []  # Preserve discovery order for readable synthetic source.
        seen: set[str] = set()  # Avoid repeated helper or constant definitions.
        queue = list(self._referenced_names(node))  # Start with names used directly by the test.
        for extra_node in extra_nodes:  # A selected static helper can use module-level definitions.
            queue.extend(self._referenced_names(extra_node))  # Add only names from proven helper definitions.
        while queue:  # Follow helper-to-helper and helper-to-class references.
            name = queue.pop(0)  # Process names breadth-first for stable output.
            if name in excluded_names:  # Do not re-add the enclosing class with unrelated members.
                continue
            if name in seen or name not in self._top_level_defs:  # Skip repeated or external names.
                continue
            seen.add(name)  # Mark this top-level definition as emitted.
            dependency = self._top_level_defs[name]  # Fetch the helper, fake class, or constant definition.
            dependencies.append(dependency)  # Include the definition in the coverage source.
            queue.extend(item for item in self._referenced_names(dependency) if item not in seen)  # Recurse.
        return dependencies  # Return every local definition used by the test.

    def _class_helper_nodes(self, owner: ast.ClassDef | None, node: ast.FunctionDef) -> list[ast.FunctionDef]:
        """Return the transitive closure of referenced static helpers in one test class."""
        if owner is None:  # Module-level tests have no owning class.
            return []
        helpers = {
            member.name: member
            for member in owner.body
            if isinstance(member, ast.FunctionDef)
            and self._is_static_method(member)
            and not any(
                member.name in self._statement_bindings(statement)
                for statement in owner.body
                if statement is not member
            )
        }
        selected: list[ast.FunctionDef] = []  # Preserve helper order for stable synthetic source.
        seen: set[str] = set()  # Stop recursive helper cycles and duplicate references.
        queue = list(self._class_helper_calls(node, owner.name))  # Start from calls in the selected test only.
        while queue:  # Follow only static helper calls within the same class.
            name = queue.pop(0)
            if name in seen or name not in helpers:
                continue
            seen.add(name)
            helper = helpers[name]
            selected.append(helper)
            queue.extend(self._class_helper_calls(helper, owner.name))
        return selected

    def _class_helper_calls(self, node: ast.AST, owner_name: str) -> set[str]:
        """Return calls explicitly owned by self, cls, or the test class."""
        names: set[str] = set()
        rebound_names = self._body_bindings(node)  # A replaced receiver no longer identifies the owning class.
        for call in (child for child in ast.walk(node) if isinstance(child, ast.Call)):
            target = call.func
            if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
                continue
            if target.value.id in {"self", "cls", owner_name} and target.value.id not in rebound_names:
                names.add(target.attr)
        return names

    def _normalize_class_helper_calls(
        self, node: ast.FunctionDef, owner: ast.ClassDef | None, helper_names: set[str]
    ) -> None:
        """Qualify only proven static calls through the selected test instance."""
        if owner is None or not helper_names:
            return
        for call in (child for child in ast.walk(node) if isinstance(child, ast.Call)):
            target = call.func
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr in helper_names
                and "self" not in self._body_bindings(node)
            ):
                target.value = ast.Name(id=owner.name, ctx=ast.Load())

    def _body_bindings(self, node: ast.AST) -> set[str]:
        """Return names replaced in a function body, without counting its parameters."""
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return set()
        return set().union(*(self._statement_bindings(statement) for statement in node.body))

    def _is_static_method(self, node: ast.stmt) -> bool:
        """Return True only for a directly declared static method."""
        return isinstance(node, ast.FunctionDef) and any(
            isinstance(decorator, ast.Name) and decorator.id == "staticmethod" for decorator in node.decorator_list
        )

    def _is_test_class(self, node: ast.ClassDef) -> bool:
        """Return True when a class owns pytest test methods and must stay sliced."""
        return any(isinstance(member, ast.FunctionDef) and member.name.startswith("test_") for member in node.body)

    def _collect_test_class_owners(self) -> dict[int, ast.ClassDef]:
        """Map each direct test method to its declaring class."""
        owners: dict[int, ast.ClassDef] = {}
        for node in self._tree.body:
            if isinstance(node, ast.ClassDef):
                owners.update(
                    {
                        id(member): node
                        for member in node.body
                        if isinstance(member, ast.FunctionDef) and member.name.startswith("test_")
                    }
                )
        return owners

    def _referenced_names(self, node: ast.AST) -> set[str]:
        """Return loaded names referenced by one AST node."""
        return {child.id for child in ast.walk(node) if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)}

    def _collect_top_level_defs(self) -> dict[str, ast.stmt]:
        """Return top-level helpers, fake classes, and constants by name."""
        definitions: dict[str, ast.stmt] = {}  # Accumulate definitions that test closures can use.
        fixture_nodes = {id(node) for node in self._pytest_fixtures.values()}  # Fixture setup is not test evidence.
        for node in self._tree.body:  # Only module-level definitions are reusable across tests.
            if (
                isinstance(node, ast.FunctionDef)
                and not node.name.startswith("test_")
                and id(node) not in fixture_nodes
            ):  # Keep reusable helpers, not fixtures or sibling tests.
                definitions[node.name] = node  # Store by declared name.
            if isinstance(node, ast.ClassDef) and not self._is_test_class(node):
                definitions[node.name] = node  # Store by declared name.
            if isinstance(node, ast.Assign):  # Constants used in parametrize decorators or response bodies.
                for target in node.targets:  # One assignment can bind several names.
                    if isinstance(target, ast.Name):  # Only simple names can be referenced directly.
                        definitions[target.id] = node  # Store the constant assignment.
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):  # Annotated constants.
                definitions[node.target.id] = node  # Store the annotated constant assignment.
        return definitions  # Return all local definitions.

    def _test_nodes(self) -> list[ast.FunctionDef]:
        """Return every test function or method in the module."""
        return [
            node for node in ast.walk(self._tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        ]

    def _collect_imported_source_class_names(self) -> set[str]:
        """Return unshadowed aliases for classes declared in repository source modules."""
        names: set[str] = set()
        for node in self._tree.body:
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            module_path = self._module_path(node.module)
            if module_path is None:
                continue
            try:
                source_tree = ast.parse(module_path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError) as exc:
                _LOGGER.debug("Cannot inspect failure-mode source %s: %s", module_path, exc)
                continue
            class_names = {item.name for item in source_tree.body if isinstance(item, ast.ClassDef)}
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name in class_names and self._source_import_is_unshadowed(local_name, node):
                    names.add(local_name)
        return names

    def _source_import_is_unshadowed(self, local_name: str, import_node: ast.ImportFrom) -> bool:
        """Reject a source-class alias replaced by another module-level binding."""
        if self._module_level_rebinds(local_name):
            return False
        for node in self._tree.body:
            if node is import_node:
                continue
            if isinstance(node, ast.Import) and any(
                (alias.asname or alias.name.split(".")[0]) == local_name for alias in node.names
            ):
                return False
            if isinstance(node, ast.ImportFrom) and any(
                (alias.asname or alias.name) == local_name for alias in node.names
            ):
                return False
        return True

    def _collect_pytest_fixtures(self) -> dict[str, ast.FunctionDef]:
        """Return same-module pytest fixtures by their declared pytest names."""
        fixtures: dict[str, ast.FunctionDef] = {}
        for node in self._tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            fixture_name = self._pytest_fixture_name(node)
            if fixture_name and not any(
                node.name in self._statement_bindings(statement)
                for statement in self._tree.body
                if statement is not node
            ):
                fixtures[fixture_name] = node
        return fixtures

    def _pytest_fixture_name(self, node: ast.FunctionDef) -> str | None:
        """Return a fixture's effective name only when its decorator resolves to pytest."""
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if self._is_pytest_fixture_target(target):
                has_override, declared_name = self._fixture_name_argument(decorator)
                if has_override:
                    return declared_name
                return node.name
        return None

    def _is_pytest_fixture_target(self, target: ast.AST) -> bool:
        """Resolve a fixture decorator through one unshadowed pytest import."""
        if isinstance(target, ast.Name):
            return self._is_pytest_import(target.id, "fixture")
        return (
            isinstance(target, ast.Attribute)
            and target.attr == "fixture"
            and isinstance(target.value, ast.Name)
            and self._is_pytest_import(target.value.id, None)
        )

    def _is_pytest_import(self, local_name: str, symbol: str | None) -> bool:
        """Return True when one module binding imports the expected pytest name."""
        matches = 0
        for node in self._tree.body:
            if isinstance(node, ast.Import):
                result = self._matches_pytest_module_import(node, local_name, symbol)
                if result < 0:
                    return False
                matches += result
            if isinstance(node, ast.ImportFrom):
                result = self._matches_pytest_symbol_import(node, local_name, symbol)
                if result < 0:
                    return False
                matches += result
        return matches == 1 and not self._module_level_rebinds(local_name)

    def _matches_pytest_module_import(self, node: ast.Import, local_name: str, symbol: str | None) -> int:
        """Count a direct pytest module import that binds the requested local name."""
        matches = 0
        for alias in node.names:
            if (alias.asname or alias.name.split(".")[0]) == local_name:
                if symbol is not None or alias.name != "pytest":
                    return -1
                matches += 1
        return matches

    def _matches_pytest_symbol_import(self, node: ast.ImportFrom, local_name: str, symbol: str | None) -> int:
        """Count a pytest symbol import that binds the requested local name."""
        matches = 0
        for alias in node.names:
            if (alias.asname or alias.name) == local_name:
                if node.module != "pytest" or symbol is None or alias.name != symbol:
                    return -1
                matches += 1
        return matches

    def _module_level_rebinds(self, name: str) -> bool:
        """Reject module rebindings, including those inside control-flow statements."""
        return any(
            name in self._statement_bindings(node)
            for node in self._tree.body
            if not isinstance(node, (ast.Import, ast.ImportFrom))
        )  # Direct imports have separate identity checks, but conditional imports can replace them.

    def _fixture_name_argument(self, decorator: ast.AST) -> tuple[bool, str | None]:
        """Read a literal fixture-name override without evaluating expressions."""
        if not isinstance(decorator, ast.Call):
            return False, None
        for keyword in decorator.keywords:
            if keyword.arg == "name":
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    return True, keyword.value.value
                return True, None
        return False, None

    def _collect_source_fixtures(self) -> dict[str, ast.FunctionDef]:
        """Keep pytest fixtures whose executable return proves an imported source instance."""
        return {
            fixture_name: node
            for fixture_name, node in self._pytest_fixtures.items()
            if self._fixture_returns_source_instance(node)
        }

    def _fixture_returns_source_instance(self, node: ast.FunctionDef) -> bool:
        """Require a direct source-class construction on an executable return path."""
        parameter_names = {
            argument.arg for argument in ast.walk(node.args) if isinstance(argument, ast.arg)
        }  # Fixture dependencies bind locally before the fixture body executes.
        local_bindings = set().union(*(self._statement_bindings(statement) for statement in node.body))
        source_class_names = {
            name for name in self._source_class_names if name not in parameter_names and name not in local_bindings
        }
        if not source_class_names:
            return False
        instance_names: set[str] = set()
        for statement in node.body:
            instance_names.difference_update(
                self._statement_bindings(statement)
            )  # Nested writes invalidate proof without guessing which branch executes.
            if isinstance(statement, ast.Assign):
                self._update_fixture_instances(statement, instance_names, source_class_names)
            if isinstance(statement, ast.Return):
                return self._returns_source_instance(statement.value, instance_names, source_class_names)
        return False

    def _statement_bindings(self, statement: ast.AST) -> set[str]:
        """Return bindings in the current scope without entering function or class bodies."""
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return {statement.name}
        if isinstance(statement, ast.Lambda):
            return set()
        bindings: set[str] = set()
        if isinstance(statement, ast.Name) and isinstance(statement.ctx, (ast.Store, ast.Del)):
            bindings.add(statement.id)
        if isinstance(statement, ast.alias):
            bindings.add(statement.asname or statement.name.split(".")[0])
        if isinstance(statement, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and statement.name:
            bindings.add(statement.name)
        if isinstance(statement, ast.MatchMapping) and statement.rest:
            bindings.add(statement.rest)
        for child in ast.iter_child_nodes(statement):
            bindings.update(self._statement_bindings(child))
        return bindings

    def _update_fixture_instances(
        self, node: ast.Assign, instance_names: set[str], source_class_names: set[str]
    ) -> None:
        """Track simple assignments that construct an imported source class."""
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            instance_names.discard(target.id)
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                if node.value.func.id in source_class_names:
                    instance_names.add(target.id)

    def _returns_source_instance(
        self, value: ast.AST | None, instance_names: set[str], source_class_names: set[str]
    ) -> bool:
        """Accept only a direct source construction or its tracked local name."""
        if isinstance(value, ast.Name):
            return value.id in instance_names
        return isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in source_class_names

    def _calls_source_fixture(self, node: ast.FunctionDef) -> bool:
        """Require a matching, unshadowed pytest parameter to call a source fixture."""
        if id(node) not in self._test_class_owners and node not in self._tree.body:
            return False  # Nested scopes cannot reuse module fixture proof without an owner.
        parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        for parameter in parameters:
            if parameter.arg not in self._source_fixtures or self._parameter_is_shadowed(node, parameter.arg):
                continue
            if self._fixture_scope_overrides(node, parameter.arg):
                continue
            if any(
                self._call_uses_parameter(call, parameter.arg) for call in ast.walk(node) if isinstance(call, ast.Call)
            ):
                return True
        return False

    def _fixture_scope_overrides(self, node: ast.FunctionDef, name: str) -> bool:
        """Reject class overrides and parameters before using a module fixture."""
        scopes: list[ast.FunctionDef | ast.ClassDef | ast.Module] = [node, self._tree]
        owner = self._test_class_owners.get(id(node))
        if owner is not None:
            if owner.bases:  # Inherited fixtures require resolution beyond same-module fixture proof.
                return True
            scopes.append(owner)
            for member in owner.body:
                if isinstance(member, ast.FunctionDef) and self._pytest_fixture_name(member) == name:
                    return True
                if name in self._statement_bindings(member):
                    return True
        return any(self._parameter_is_parametrized(scope, name) for scope in scopes)

    def _parameter_is_shadowed(self, node: ast.FunctionDef, name: str) -> bool:
        """Reject a fixture parameter that is assigned inside the selected test."""
        return any(
            isinstance(child, ast.Name) and child.id == name and isinstance(child.ctx, ast.Store)
            for child in ast.walk(node)
        )

    def _parameter_is_parametrized(self, node: ast.FunctionDef | ast.ClassDef | ast.Module, name: str) -> bool:
        """Reject parameter replacement from decorators or scope-level pytest marks."""
        marks: list[ast.AST] = list(node.decorator_list) if not isinstance(node, ast.Module) else []
        marks.extend(
            statement
            for statement in node.body
            if isinstance(statement, (ast.Assign, ast.AnnAssign))
            and "pytestmark" in self._statement_bindings(statement)
        )
        for decorator in marks:
            for call in (child for child in ast.walk(decorator) if isinstance(child, ast.Call)):
                if not isinstance(call.func, ast.Attribute) or call.func.attr != "parametrize":
                    continue
                if call.args and name in self._parametrize_names(call.args[0]):
                    return True
        return False

    def _parametrize_names(self, node: ast.AST) -> tuple[str, ...]:
        """Return literal names from pytest's supported parametrize forms."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return tuple(name.strip() for name in node.value.split(",") if name.strip())
        if isinstance(node, (ast.List, ast.Tuple)):
            return tuple(
                item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
        return ()

    def _call_uses_parameter(self, call: ast.Call, name: str) -> bool:
        """Return True only when a call target is rooted at the fixture parameter."""
        return isinstance(call.func, ast.Attribute) and self._call_root(call.func) == name

    def _calls_source(self, node: ast.FunctionDef) -> bool:
        """Return True when a test function calls source under test."""
        source_roots = set(self._source_call_roots)  # Start with module-level source imports.
        source_roots.update(self._local_source_roots(node))  # Add source imports scoped inside this test function.
        replaced_names = self._replaced_call_roots(node)
        calls = (
            child
            for child in ast.walk(node)
            if isinstance(child, ast.Call) and self._call_root(child.func) not in replaced_names
        )  # Replaced callable and helper bindings cannot establish source evidence.
        if any(self._call_drives_source(call, source_roots) for call in calls):
            return True
        return self._calls_source_fixture(node)

    def _replaced_call_roots(self, node: ast.FunctionDef) -> set[str]:
        """Return local bindings other than verified direct source imports."""
        names = {argument.arg for argument in ast.walk(node.args) if isinstance(argument, ast.arg)}
        for statement in node.body:
            names.update(self._non_source_bindings(statement))
        return names

    def _non_source_bindings(self, node: ast.AST) -> set[str]:
        """Exclude source imports individually without hiding adjacent replacement bindings."""
        if isinstance(node, ast.ImportFrom) and node.module:
            return self._statement_bindings(node) - self._roots_from_import_from(node)
        if isinstance(node, ast.Import):
            return self._statement_bindings(node) - self._roots_from_import(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return self._statement_bindings(node)
        bindings: set[str] = set()
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bindings.add(node.id)
        if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            bindings.add(node.name)
        if isinstance(node, ast.MatchMapping) and node.rest:
            bindings.add(node.rest)
        for child in ast.iter_child_nodes(node):
            bindings.update(self._non_source_bindings(child))
        return bindings

    def _call_drives_source(self, call: ast.Call, source_roots: set[str]) -> bool:
        """Return True when one call targets a source alias or a local SUT."""
        called_root = self._call_root(call.func)  # Resolve the leftmost callable root for import aliases.
        called_name = self._call_name(call.func)  # Resolve direct function calls for local fixture SUTs.
        return (
            called_root in source_roots
            or called_name in self._local_source_names
            or called_name in self._source_driver_names
        )

    def _local_source_roots(self, node: ast.FunctionDef) -> set[str]:
        """Return source import roots declared inside one test function."""
        roots: set[str] = set()  # Accumulate function-scoped source aliases.
        for child in ast.walk(node):  # Function bodies can import source modules near the assertion.
            if isinstance(child, ast.ImportFrom) and child.module:  # Direct imports can expose source symbols.
                roots.update(self._roots_from_import_from(child))  # Add each imported source symbol alias.
            if isinstance(child, ast.Import):  # Module imports can expose source modules by alias.
                roots.update(self._roots_from_import(child))  # Add each source module alias.
        return roots  # Return every source alias local to this test.

    def _collect_source_call_roots(self) -> set[str]:
        """Return local names that identify repository source imports."""
        roots: set[str] = set()  # Accumulate names a test can call to reach source.
        for node in self._tree.body:  # Only module imports define source aliases.
            if isinstance(node, ast.ImportFrom) and node.module:  # Direct imports can expose source symbols.
                roots.update(self._roots_from_import_from(node))  # Add each imported source symbol alias.
            if isinstance(node, ast.Import):  # Module imports can expose source modules by alias.
                roots.update(self._roots_from_import(node))  # Add each source module alias.
        return {name for name in roots if not self._module_level_rebinds(name)}  # Reject replaced source imports.

    def _collect_source_driver_names(self) -> set[str]:
        """Return local helper names that call repository source."""
        helpers = {
            node.name: node
            for node in self._tree.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("test_")
        }  # Only local helper functions can bridge a test to source code.
        driver_names: set[str] = set()  # Accumulate helpers known to reach source.
        changed = True  # Iterate because one helper can call another helper.
        while changed:  # Continue until transitive helper discovery stabilizes.
            changed = False  # Reset the fixed-point flag for this pass.
            for name, helper in helpers.items():  # Examine each local helper.
                if name not in driver_names and self._helper_calls_source(helper, driver_names):  # New driver found.
                    driver_names.add(name)  # Mark the helper as source-driving.
                    changed = True  # Another pass can now find helpers that call this helper.
        return driver_names  # Return every source-driving helper.

    def _helper_calls_source(self, node: ast.FunctionDef, driver_names: set[str]) -> bool:
        """Return True when a helper calls source or a source-driving helper."""
        source_roots = set(self._source_call_roots)  # Start with module-level source imports.
        source_roots.update(self._local_source_roots(node))  # Add imports scoped inside the helper.
        for call in (child for child in ast.walk(node) if isinstance(child, ast.Call)):  # Inspect helper calls.
            called_root = self._call_root(call.func)  # Resolve the leftmost callable root for imports.
            called_name = self._call_name(call.func)  # Resolve direct helper calls.
            if called_root in source_roots or called_name in driver_names:  # Source or transitive helper call.
                return True  # This helper reaches source under test.
        return False  # No source-driving call was found.

    def _roots_from_import_from(self, node: ast.ImportFrom) -> set[str]:
        """Return callable roots from one ``from x import y`` source import."""
        module_path = self._module_path(node.module or "")  # Resolve only repository modules.
        roots: set[str] = set()  # Accumulate imported source symbols.
        for alias in node.names:  # Each alias can name a symbol or a submodule.
            submodule_path = (
                self._module_path(f"{node.module}.{alias.name}") if node.module else None
            )  # Package import.
            if module_path is not None or submodule_path is not None:  # Direct module or package submodule import.
                roots.add(alias.asname or alias.name)  # Tests call the imported source by this name.
        return roots  # Return only aliases that resolve to repository source.

    def _roots_from_import(self, node: ast.Import) -> set[str]:
        """Return callable roots from one ``import x`` source import."""
        roots: set[str] = set()  # Accumulate source module aliases.
        for alias in node.names:  # One import statement can hold several modules.
            module_path = self._module_path(alias.name)  # Resolve only repository modules.
            if module_path is not None:  # Only repository source imports can clear failure-mode coverage.
                roots.add(alias.asname or alias.name.split(".")[0])  # Tests call this root or alias.
        return roots  # Return all module roots from this import statement.

    def _collect_local_source_names(self) -> set[str]:
        """Return local SUT names for analyzer fixture files only."""
        if not self._allows_local_source():  # Real tests must import repository source to prove coverage.
            return set()  # Do not let local helpers satisfy the source-call requirement in real tests.
        return {
            node.name
            for node in self._tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not node.name.startswith("test_")
        }  # Analyzer fixtures can keep their local SUT contract.

    def _allows_local_source(self) -> bool:
        """Return True when the test path belongs to the analyzer fixture corpus."""
        resolved = _resolve_test_path(self._test_path)
        try:
            resolved.relative_to((self._root / "tests").resolve())  # Real repository tests live under tests/.
            return False  # Real tests must call imported product source.
        except ValueError:
            return True  # Analyzer fixtures live outside tests/ and can define local source under test.

    def _module_path(self, module_name: str) -> Path | None:
        """Return a repository module path for source imports."""
        candidate = self._root / Path(*module_name.split(".")).with_suffix(".py")  # Build portable module path.
        return candidate if candidate.exists() else None  # Only existing repository modules are valid source roots.

    def _call_root(self, node: ast.AST) -> str:
        """Return the leftmost root name of a call target."""
        current = node  # Walk left through attributes to the import alias.
        while isinstance(current, ast.Attribute):  # Attribute chains hold module or class access.
            current = current.value  # Move toward the root.
        return current.id if isinstance(current, ast.Name) else ""  # Return a simple comparable root.

    def _call_name(self, node: ast.AST) -> str:
        """Return the direct callable name of a call target."""
        return node.id if isinstance(node, ast.Name) else ""  # Local fixture SUT calls use plain names.


class MissingFailureModeDetector:
    """Detects HTTP-style tests that omit standard failure-mode coverage."""

    def __init__(self) -> None:
        """Initialize per-run inspection state."""
        self._inspected_paths: set[str] = set()  # Track files where inferred applicability exists.

    def reset_inspection(self) -> None:
        """Clear per-run inspection state before a CLI scan."""
        _LOGGER.info("Resetting missing-failure-mode inspection state")  # Log before resetting singleton state.
        self._inspected_paths.clear()  # Registry instances persist across in-process test invocations.
        _LOGGER.debug("Missing-failure-mode inspection state reset")  # Confirm there is no stale count.

    def inspected_module_count(self) -> int:
        """Return the number of test modules with inferred failure-mode applicability."""
        return len(self._inspected_paths)  # Count unique modules, not findings.

    # --- Detector protocol ---------------------------------------------------

    def detect(
        self,
        test_path: Path,  # File under analysis.
        tree: ast.Module,  # Parsed AST.
        source: str,  # Raw source text -- primary substrate for marker matching.
    ) -> list[Finding]:
        """Return one Finding per uncovered failure mode in an HTTP-style test file."""
        _LOGGER.info("Scanning %s for missing failure modes", test_path)
        posix = test_path.as_posix()  # Cross-platform stable path.
        risk = FailureModeApplicabilityInferer(test_path, tree).infer()  # Infer from the imported source under test.
        coverage_source = SourceDrivenCoverageSlicer(test_path, tree).source_text()  # Keep only source-driving tests.
        coverage = self._coverage_from_source(coverage_source)  # Read evidence only from source-driving tests.
        _LOGGER.debug("Failure-mode risk for %s: %s", test_path, risk)  # Log the inferred rule scope.
        if not risk.applies:  # Files that test DTOs or route tables do not own network failure tests.
            return []  # Do not emit findings when the source cannot fail this way.
        self._inspected_paths.add(posix)  # Count this module as measured by the detector.
        findings = self._findings_for_coverage(posix, risk, coverage)  # Convert missing evidence to findings.
        _LOGGER.debug("Missing-failure-mode finding count for %s: %s", test_path, len(findings))
        return findings

    # --- Helpers -------------------------------------------------------------

    def _coverage_from_source(self, source: str) -> FailureModeCoverage:
        """Return the failure modes that one test source exercises."""
        status_coverage = HttpStatusCoverageInferer.from_source(source)  # Read status evidence from syntax only.
        return FailureModeCoverage(
            connection_timeout=self._matches_any(source, _TIMEOUT_MARKERS),  # Timeout marker proves timeout path.
            connection_error=self._matches_any(source, _CONNECTION_ERROR_MARKERS),  # Connection marker proof.
            http_4xx=status_coverage.http_4xx,  # Syntax-tied 4xx status proves client-error coverage.
            http_5xx=status_coverage.http_5xx,  # Syntax-tied 5xx status proves server-error coverage.
            malformed_json=self._matches_any(source, _MALFORMED_JSON_MARKERS),  # JSONDecodeError marker proof.
            empty_body=self._matches_any(source, _EMPTY_BODY_MARKERS),  # Empty body marker proof.
        )

    def _findings_for_coverage(
        self,
        posix: str,  # POSIX file path.
        risk: FailureModeRisk,  # Source-under-test failure modes.
        coverage: FailureModeCoverage,  # Test evidence for this module.
    ) -> list[Finding]:
        """Return findings for each required failure mode that lacks evidence."""
        findings: list[Finding] = []  # Accumulate file-level rule findings.
        if risk.network_exception and not coverage.connection_timeout:  # Direct network clients can time out.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_connection_timeout",
                    "No connection-timeout failure mode is exercised.",
                    "Add a test that raises `requests.exceptions.Timeout` (or equivalent).",
                )
            )
        if (
            risk.network_exception or risk.sdk_connection_error
        ) and not coverage.connection_error:  # Direct network clients can fail before a response.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_connection_error",
                    "No connection-error failure mode is exercised.",
                    "Add a test that raises `requests.exceptions.ConnectionError` (or equivalent).",
                )
            )
        if risk.http_status and not coverage.http_4xx:  # Cloud APIs can refuse or reject the request.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_http_4xx",
                    "No HTTP 4xx failure mode is exercised.",
                    "Add a test whose fake response has a 4xx status_code (e.g. 400, 404).",
                )
            )
        if risk.http_status and not coverage.http_5xx:  # Cloud APIs can return a server error.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_http_5xx",
                    "No HTTP 5xx failure mode is exercised.",
                    "Add a test whose fake response has a 5xx status_code (e.g. 500, 503).",
                )
            )
        if risk.json_parse and not coverage.malformed_json:  # JSON parsers can reject malformed response bodies.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_malformed_json",
                    "No malformed-JSON failure mode is exercised.",
                    "Add a test where `.json()` raises `json.JSONDecodeError`.",
                )
            )
        if risk.json_parse and not coverage.empty_body:  # Empty response bodies can break JSON parsing.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_empty_body",
                    "No empty-body failure mode is exercised.",
                    'Add a test whose fake response body is empty (e.g. b"").',
                )
            )
        return findings  # Return all missing failure-mode findings.

    def _matches_any(self, source: str, needles: tuple[str, ...]) -> bool:
        """Return True if any of `needles` appears as a substring of `source`."""
        # Substring test is sufficient: markers are distinctive exception / literal names.
        return any(needle in source for needle in needles)

    def _finding(
        self,
        posix: str,  # POSIX file path.
        rule_id: str,  # Sub-rule id (missing_fm_*).
        explanation: str,  # Human-facing message.
        remediation: str,  # Suggested fix.
    ) -> Finding:
        """Construct a MEDIUM-severity Finding with common metadata."""
        return Finding(
            category=Category.MISSING_FAILURE_MODE,
            rule_id=rule_id,
            severity=Severity.MEDIUM,
            file_path=posix,
            line_number=1,  # File-level finding -- point at file header.
            explanation=explanation,
            remediation=remediation,
            heuristic=False,
            related_source=posix,
        )


# Register a default instance on import (T019 registry contract).
DetectorRegistry.append(MissingFailureModeDetector())  # Singleton registration.
