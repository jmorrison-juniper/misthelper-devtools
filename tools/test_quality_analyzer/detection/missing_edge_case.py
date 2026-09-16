r"""MissingEdgeCaseDetector (T040): flag uncovered input edge cases.

The detector inspects a test module when it can infer at least one applicable
source-under-test parameter. It uses the source signature annotation, default
value, or parameter name. It avoids assertion helpers, mock helpers, factories,
and HTTP response builders, because their inputs do not describe the source
under test. It treats `status` and `status_code` as categorical codes.

Sub-rules:

- missing_ec_empty_input: any empty container literal `[]`, `{}`, `""`, `b""`.
- missing_ec_zero_value: the integer literal `0` used as a call argument.
- missing_ec_negative_value: any negative int literal `-\d+` used as a call argument.
- missing_ec_none_input: the literal `None` used as a call argument.
"""

from __future__ import annotations  # Postponed annotations keep runtime imports small.

import ast  # Parse and inspect test modules without executing them.
import logging  # Record detector actions for local and CI diagnosis.
from dataclasses import dataclass  # Store inferred domains and coverage as clear records.
from pathlib import Path  # Resolve repository-relative source paths safely.

from tools.test_quality_analyzer.detection import (  # Registry and shared finding types.
    Category,
    DetectorRegistry,
    Finding,
    Severity,
)

_LOGGER = logging.getLogger(__name__)  # Module logger lets the CLI control formatting.

_SUPPORT_CALL_NAMES = frozenset(  # Names that belong to pytest, mocks, or Python helpers.
    {
        "ANY",  # Mock sentinel arguments are not source-under-test inputs.
        "MagicMock",  # Mock construction arguments are not source-under-test inputs.
        "Mock",  # Mock construction arguments are not source-under-test inputs.
        "PropertyMock",  # Mock construction arguments are not source-under-test inputs.
        "SimpleNamespace",  # Namespace fixtures are not production behavior.
        "append",  # List construction in tests is fixture setup.
        "call",  # Mock call objects describe expectations.
        "dict",  # Container constructors do not identify the source under test.
        "enumerate",  # Loop helper input is not a source-under-test call.
        "float",  # Type conversion input is not a source-under-test call.
        "int",  # Type conversion input is not a source-under-test call.
        "len",  # Length checks are assertions.
        "list",  # Container constructors do not identify the source under test.
        "patch",  # Mock patch targets are not source-under-test calls.
        "parametrize",  # Pytest parameter lists build cases.
        "print",  # Debug output is not a source-under-test call.
        "range",  # Loop bounds are not source-under-test inputs.
        "set",  # Container constructors do not identify the source under test.
        "sorted",  # Sorting helper input is not a source-under-test call.
        "str",  # Type conversion input is not a source-under-test call.
        "tuple",  # Container constructors do not identify the source under test.
        "zip",  # Loop helper input is not a source-under-test call.
    }
)

_SUPPORT_CALL_PREFIXES = (  # Helper prefixes usually build fixtures instead of exercising behavior.
    "_",  # Private calls in tests are usually helper seams or fixture builders.
    "_Fake",  # Fake classes create test doubles.
    "_make_",  # Private factory helpers commonly create fake responses.
    "assert",  # Assertion helpers check expectations.
    "build_",  # Builder helpers create fixtures.
    "create_",  # Creation helpers commonly create test data.
    "fake_",  # Fake helpers create doubles.
    "fixture_",  # Fixture helpers are test support by naming convention.
    "make_",  # Factory helpers create fixture data.
    "sample_",  # Sample helpers create fixture data.
)

_HTTP_STATUS_KEYWORDS = frozenset({"status", "status_code"})  # HTTP statuses are categorical codes.
_NUMERIC_TYPES = frozenset({"Decimal", "float", "int"})  # Numeric annotations require numeric edge tests.
_COLLECTION_TYPES = frozenset(  # Collection annotations require empty-input edge tests.
    {"Collection", "Iterable", "Mapping", "MutableMapping", "Sequence", "dict", "frozenset", "list", "set", "tuple"}
)
_NUMERIC_NAME_PARTS = frozenset(  # Parameter names that describe numeric domains.
    {
        "age",
        "attempt",
        "capacity",
        "count",
        "delay",
        "duration",
        "height",
        "index",
        "interval",
        "length",
        "limit",
        "maximum",
        "minimum",
        "offset",
        "percent",
        "port",
        "rate",
        "retry",
        "retries",
        "seconds",
        "size",
        "threshold",
        "timeout",
        "total",
        "value",
        "width",
    }
)
_COLLECTION_NAME_PARTS = frozenset(  # Parameter names that describe collection domains.
    {
        "aps",
        "clients",
        "devices",
        "entries",
        "groups",
        "items",
        "list",
        "mapping",
        "payloads",
        "records",
        "rows",
        "sequence",
        "sites",
        "switches",
        "values",
    }
)


@dataclass
class EdgeCaseDomain:
    """Store edge-case obligations for one source-under-test parameter."""

    numeric: bool = False  # True when zero and negative tests apply.
    collection: bool = False  # True when an empty container test applies.
    optional: bool = False  # True when a None test applies.

    @property
    def applies(self) -> bool:
        """Return True when any edge-case rule applies to the parameter."""
        return self.numeric or self.collection or self.optional  # One domain makes the call in scope.


@dataclass
class SourceSignature:
    """Store inferred domains for one callable source signature."""

    positional: tuple[EdgeCaseDomain, ...]  # Positional domains in source parameter order.
    keywords: dict[str, EdgeCaseDomain]  # Keyword domains by source parameter name.

    def domain_for(self, index: int | None, name: str | None) -> EdgeCaseDomain:
        """Return the inferred domain for one bound argument."""
        if name is not None and name in self.keywords:  # Keyword binding wins when present.
            return self.keywords[name]  # Use the exact source parameter name.
        if index is not None and index < len(self.positional):  # Positional binding uses source order.
            return self.positional[index]  # Return the parameter at the same index.
        return EdgeCaseDomain()  # No inferred source parameter exists.


@dataclass
class EdgeCaseCoverage:
    """Store source-under-test edge coverage that one test file demonstrates."""

    requires_empty_input: bool = False  # True when a collection input exists.
    requires_numeric_edges: bool = False  # True when a numeric input exists.
    requires_none_input: bool = False  # True when an optional input exists.
    has_empty_input: bool = False  # True when an empty collection reaches the source under test.
    has_zero_value: bool = False  # True when zero reaches the source under test.
    has_negative_value: bool = False  # True when a negative integer reaches the source under test.
    has_none_input: bool = False  # True when None reaches the source under test.

    @property
    def applies(self) -> bool:
        """Return True when this file contains an inferred edge-case domain."""
        return self.requires_empty_input or self.requires_numeric_edges or self.requires_none_input  # Scope proof.


class EdgeCaseApplicabilityInferer:
    """Infer edge-case domains from source-under-test signatures."""

    def __init__(self, test_path: Path, tree: ast.Module) -> None:
        """Build reusable lookup tables for one test module."""
        self._test_path = test_path  # Keep the test path for repository-root resolution.
        self._tree = tree  # Keep the parsed tree so lookup methods share one AST.
        self._root = self._repository_root(test_path)  # Resolve source imports from the repository root.
        self._source_cache: dict[Path, ast.Module | None] = {}  # Avoid reparsing source modules.
        self._local_signatures = self._local_signature_map(tree)  # Support local fixture source functions.
        self._imported_signatures = self._imported_signature_map(tree)  # Support direct source imports.
        self._module_aliases = self._module_alias_map(tree)  # Support module.func call shapes.

    def domains_for_call(self, call: ast.Call) -> list[EdgeCaseDomain]:
        """Return inferred domains for each argument sent to a source-under-test call."""
        signature = self._signature_for_call(call)  # Resolve the target signature if possible.
        if signature is None:  # The detector must infer from the source signature, not from a call guess.
            return []  # Unknown targets stay outside the rule scope.
        domains = self._domains_from_args(call, signature)  # Bind actual arguments to inferred domains.
        return [domain for domain in domains if domain.applies]  # Drop arguments outside the rule scope.

    @staticmethod
    def domain_from_name(name: str) -> EdgeCaseDomain:
        """Infer a domain from a parameter name."""
        lowered = name.lower()  # Normalize once so matching stays stable.
        parts = set(lowered.replace("-", "_").split("_"))  # Tokenize snake-style names.
        if lowered in _HTTP_STATUS_KEYWORDS or "status" in parts:  # Status values are categorical.
            return EdgeCaseDomain()  # Do not ask for zero or negative status tests.
        numeric = bool(parts & _NUMERIC_NAME_PARTS)  # Numeric names imply numeric edge applicability.
        collection = bool(parts & _COLLECTION_NAME_PARTS)  # Collection names imply empty-input applicability.
        optional = lowered.startswith("optional_") or lowered.endswith("_or_none")  # Name can signal None support.
        return EdgeCaseDomain(numeric=numeric, collection=collection, optional=optional)  # Return inferred domains.

    def _domains_from_args(self, call: ast.Call, signature: SourceSignature) -> list[EdgeCaseDomain]:
        domains: list[EdgeCaseDomain] = []  # Accumulate domains in call-argument order.
        for index, _arg in enumerate(call.args):  # Bind positional arguments by index.
            domains.append(signature.domain_for(index, None))  # Positional calls need a resolved signature.
        for keyword in call.keywords:  # Bind keyword arguments by source parameter name.
            domains.append(signature.domain_for(None, keyword.arg))  # Keyword calls use resolved parameter names.
        return domains  # The caller filters arguments without applicability.

    def _signature_for_call(self, call: ast.Call) -> SourceSignature | None:
        if isinstance(call.func, ast.Name):  # Plain calls map to local or direct imported symbols.
            return self._local_signatures.get(call.func.id) or self._imported_signatures.get(call.func.id)  # Lookup.
        if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):  # module.func calls.
            module_path = self._module_aliases.get(call.func.value.id)  # Resolve the module alias.
            return self._signature_from_module(module_path, call.func.attr) if module_path else None  # Lookup attr.
        return None  # Dynamic call targets stay outside inferred applicability.

    def _local_signature_map(self, tree: ast.Module) -> dict[str, SourceSignature]:
        signatures: dict[str, SourceSignature] = {}  # Build lookup by function name.
        if not self._allows_local_signatures():  # Real test modules define helpers, not production source.
            return signatures  # Preserve local signatures only for analyzer fixtures and synthetic tests.
        for node in tree.body:  # Read only top-level definitions in the test module.
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("test_"):  # Ignore pytest tests.
                signatures[node.name] = self._signature_from_function(node)  # Local source helper can be SUT.
            if isinstance(node, ast.ClassDef):  # Local class constructors can be the source under test.
                signature = self._signature_from_class(node)  # Read __init__ or empty signature.
                signatures[node.name] = signature  # Store constructor applicability by class name.
        return signatures  # Return local signatures for plain calls.

    def _allows_local_signatures(self) -> bool:
        """Return True when local definitions can represent fixture source under test."""
        resolved = (self._root / self._test_path).resolve() if not self._test_path.is_absolute() else self._test_path
        try:
            resolved.relative_to((self._root / "tests").resolve())  # Real tests use local functions as helpers.
            return False  # Do not infer source-under-test domains from real test helpers.
        except ValueError:
            return True  # Analyzer fixtures and synthetic tests can define their source under test locally.

    def _imported_signature_map(self, tree: ast.Module) -> dict[str, SourceSignature]:
        signatures: dict[str, SourceSignature] = {}  # Build lookup by imported symbol alias.
        for node in tree.body:  # Imports at module scope define the test module contract.
            if isinstance(node, ast.ImportFrom) and node.module:  # Direct imports can name source symbols.
                module_path = self._module_path(node.module)  # Resolve dotted module names to files.
                for alias in node.names:  # Each alias can expose one callable to the test.
                    name = alias.asname or alias.name  # Tests call the alias when one exists.
                    signature = self._signature_from_module(module_path, alias.name)  # Read source signature.
                    if signature is not None:  # Only callable symbols get a signature.
                        signatures[name] = signature  # Store the alias used by the test.
        return signatures  # Return direct import lookup.

    def _module_alias_map(self, tree: ast.Module) -> dict[str, Path]:
        aliases: dict[str, Path] = {}  # Build lookup for module aliases used in attribute calls.
        for node in tree.body:  # Module imports live at top level in normal tests.
            if isinstance(node, ast.Import):  # `import src.foo as foo` exposes a module object.
                for alias in node.names:  # One import statement can contain multiple modules.
                    module_path = self._module_path(alias.name)  # Resolve the imported module.
                    if module_path is not None:  # Only repository modules are useful here.
                        aliases[alias.asname or alias.name.split(".")[-1]] = module_path  # Store call prefix.
        return aliases  # Return module alias lookup.

    def _signature_from_module(self, module_path: Path | None, symbol: str) -> SourceSignature | None:
        tree = self._source_tree(module_path) if module_path is not None else None  # Parse source module lazily.
        if tree is None:  # Missing or unparseable modules stay outside inferred scope.
            return None  # The detector must not guess from unreadable source.
        for node in tree.body:  # Search only top-level public objects.
            if isinstance(node, ast.FunctionDef) and node.name == symbol:  # Direct function import.
                return self._signature_from_function(node)  # Use the function signature.
            if isinstance(node, ast.ClassDef) and node.name == symbol:  # Class import.
                return self._signature_from_class(node)  # Use the constructor signature.
        return None  # Imported symbol is not a supported callable.

    def _signature_from_class(self, node: ast.ClassDef) -> SourceSignature:
        init = next((item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == "__init__"), None)
        return self._signature_from_function(init, skip_self=True) if init else SourceSignature((), {})  # Constructor.

    def _signature_from_function(self, node: ast.FunctionDef | None, skip_self: bool = False) -> SourceSignature:
        if node is None:  # Some classes have no explicit constructor.
            return SourceSignature((), {})  # No inferred domain exists.
        parameters = list(node.args.posonlyargs) + list(node.args.args)  # Preserve callable parameter order.
        parameters = parameters[1:] if skip_self and parameters else parameters  # Drop `self` for constructors.
        defaults = [None] * (len(parameters) - len(node.args.defaults)) + list(node.args.defaults)  # Align defaults.
        domains = [  # Infer each parameter domain from the aligned parameter and default.
            self._domain_from_parameter(param, default) for param, default in zip(parameters, defaults, strict=False)
        ]
        keyword_domains = {  # Build a keyword map for calls that name arguments.
            param.arg: domain for param, domain in zip(parameters, domains, strict=False)
        }
        return SourceSignature(tuple(domains), keyword_domains)  # Return both positional and keyword views.

    def _domain_from_parameter(self, parameter: ast.arg, default: ast.expr | None) -> EdgeCaseDomain:
        if self._is_status_parameter(parameter.arg):  # Status parameters are categorical, even with int annotation.
            return EdgeCaseDomain()  # Preserve the pull request #2734 status-code exclusion.
        from_name = self.domain_from_name(parameter.arg)  # Parameter names are the weakest inference source.
        from_annotation = self._domain_from_annotation(parameter.annotation)  # Type annotations are stronger.
        from_default = self._domain_from_default(default)  # Defaults prove accepted shapes.
        return EdgeCaseDomain(
            numeric=from_name.numeric or from_annotation.numeric or from_default.numeric,  # Merge numeric proof.
            collection=from_name.collection
            or from_annotation.collection
            or from_default.collection,  # Merge collection proof.
            optional=from_name.optional or from_annotation.optional or from_default.optional,  # Merge None proof.
        )

    def _is_status_parameter(self, name: str) -> bool:
        lowered = name.lower()  # Normalize once for categorical parameter checks.
        parts = set(lowered.replace("-", "_").split("_"))  # Tokenize snake-style names.
        return lowered in _HTTP_STATUS_KEYWORDS or "status" in parts  # Any status token suppresses numeric scope.

    def _domain_from_annotation(self, annotation: ast.expr | None) -> EdgeCaseDomain:
        text = ast.unparse(annotation) if annotation is not None else ""  # Convert annotation to comparable text.
        optional = "None" in text or "Optional" in text  # Optional syntax requires a None edge case.
        numeric = any(token in text for token in _NUMERIC_TYPES)  # Numeric type names require numeric edge cases.
        collection = any(token in text for token in _COLLECTION_TYPES)  # Container type names require empty input.
        return EdgeCaseDomain(numeric=numeric, collection=collection, optional=optional)  # Return annotation domains.

    def _domain_from_default(self, default: ast.expr | None) -> EdgeCaseDomain:
        if default is None:  # No default gives no domain proof.
            return EdgeCaseDomain()  # Keep the signature outside scope unless another signal exists.
        numeric = isinstance(default, ast.Constant) and isinstance(default.value, int | float)  # Numeric default.
        numeric = numeric and not isinstance(default.value, bool)  # Boolean defaults are categorical switches.
        collection = isinstance(default, ast.List | ast.Dict | ast.Tuple | ast.Set)  # Container default.
        optional = isinstance(default, ast.Constant) and default.value is None  # None default marks optional input.
        return EdgeCaseDomain(numeric=numeric, collection=collection, optional=optional)  # Return default domains.

    def _module_path(self, module_name: str) -> Path | None:
        candidate = self._root / Path(*module_name.split(".")).with_suffix(".py")  # Build platform-safe module path.
        return candidate if candidate.exists() else None  # Return only existing source files.

    def _source_tree(self, module_path: Path | None) -> ast.Module | None:
        if module_path is None:  # Unknown modules cannot produce signatures.
            return None  # Avoid guessing from third-party sources.
        if module_path not in self._source_cache:  # Parse each source module at most once.
            self._source_cache[module_path] = self._parse_source(module_path)  # Cache success and failure.
        return self._source_cache[module_path]  # Return cached parse result.

    def _parse_source(self, module_path: Path) -> ast.Module | None:
        try:
            source = module_path.read_text(encoding="utf-8")  # Read source text for AST parsing.
            return ast.parse(source, filename=str(module_path))  # Parse safely without import side effects.
        except (OSError, SyntaxError) as exc:
            _LOGGER.debug("Cannot inspect source signature %s: %s", module_path, exc)  # Explain skipped source.
            return None  # Unreadable source cannot establish applicability.

    def _repository_root(self, test_path: Path) -> Path:
        resolved = (Path.cwd() / test_path).resolve() if not test_path.is_absolute() else test_path.resolve()
        for parent in (resolved.parent, *resolved.parents):  # Walk upward from the test module path.
            if (parent / "pyproject.toml").exists() and (parent / "src").exists():  # Identify repository root.
                return parent  # Use the first matching repository root.
        return Path.cwd()  # Fall back to the current checkout used by the CLI.


class MissingEdgeCaseDetector:
    """Detect source-under-test input domains that omit edge-case coverage."""

    def __init__(self) -> None:
        """Initialize per-run inspection state."""
        self._inspected_paths: set[str] = set()  # Track files where inferred applicability exists.

    def reset_inspection(self) -> None:
        """Clear per-run inspection state before a CLI scan."""
        _LOGGER.info("Resetting missing-edge-case inspection state")  # Log before resetting shared detector state.
        self._inspected_paths.clear()  # Registry instances persist across in-process test invocations.
        _LOGGER.debug("Missing-edge-case inspection state reset")  # Confirm the detector has no stale files.

    def inspected_module_count(self) -> int:
        """Return the number of test modules with inferred edge-case applicability."""
        return len(self._inspected_paths)  # Count unique modules, not calls.

    def detect(
        self,
        test_path: Path,  # File under analysis.
        tree: ast.Module,  # Parsed AST.
        source: str,  # Raw source text kept for the detector protocol.
    ) -> list[Finding]:
        """Return one heuristic Finding per uncovered edge case in a scoped test file."""
        del source  # The detector infers scope from AST signatures instead of source markers.
        _LOGGER.info("Scanning %s for missing edge cases", test_path)  # Log before per-file inference.
        posix = test_path.as_posix()  # Store stable paths in findings and metrics.
        coverage = self._build_coverage_profile(test_path, tree)  # Infer applicability and observed coverage.
        _LOGGER.debug("Edge-case coverage profile for %s: %s", test_path, coverage)  # Log the measured result.
        if not coverage.applies:  # Files with no inferred source-under-test domains are outside this rule.
            return []  # Do not emit noise for helpers, mocks, factories, or unsupported signatures.
        self._inspected_paths.add(posix)  # Count this module as measured by the detector.
        findings = self._findings_for_coverage(posix, coverage)  # Convert uncovered domains to findings.
        _LOGGER.debug("Missing-edge-case finding count for %s: %s", test_path, len(findings))  # Log result count.
        return findings  # Return all file-level findings.

    def _build_coverage_profile(self, test_path: Path, tree: ast.Module) -> EdgeCaseCoverage:
        coverage = EdgeCaseCoverage()  # Accumulate coverage across all tests in the file.
        inferer = EdgeCaseApplicabilityInferer(test_path, tree)  # Resolve source signatures once per file.
        for call in self._collect_source_under_test_calls(tree):  # Inspect only non-support calls in test functions.
            self._record_call_coverage(call, inferer.domains_for_call(call), coverage)  # Merge call evidence.
        return coverage  # The caller decides whether missing coverage is a finding.

    def _collect_source_under_test_calls(self, tree: ast.Module) -> list[ast.Call]:
        calls: list[ast.Call] = []  # Accumulate non-support calls made by test functions.
        for node in ast.walk(tree):  # Search all test functions, including methods.
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):  # Match pytest tests only.
                calls.extend(
                    sub for sub in ast.walk(node) if isinstance(sub, ast.Call) and self._is_non_support_call(sub)
                )
        return calls  # Return candidate calls for signature inference.

    def _is_non_support_call(self, call: ast.Call) -> bool:
        call_name = self._call_leaf_name(call.func)  # Read the stable leaf name for filtering.
        if call_name is None:  # Dynamic targets are too uncertain for this heuristic.
            return False  # Keep dynamic calls outside the rule scope.
        call_path = self._call_path(call.func)  # Read dotted names such as `patch.object`.
        if call_path.startswith("patch."):  # Mock patch helpers configure tests.
            return False  # They are not source-under-test calls.
        if self._is_support_call_name(call_name):  # Known helper calls create false findings.
            return False  # Preserve the pull request #2734 support-call exclusion.
        return True  # The inference class decides whether this call has applicable domains.

    def _call_leaf_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):  # Plain function call.
            return node.id  # Return the name as written.
        if isinstance(node, ast.Attribute):  # Method or module function call.
            return node.attr  # Return the rightmost name.
        return None  # Other targets are dynamic.

    def _call_path(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):  # A bare function call has one path part.
            return node.id  # Return the one-part path.
        if isinstance(node, ast.Attribute):  # Attribute calls need dotted path reconstruction.
            parent = self._call_path(node.value)  # Resolve the left side first.
            return f"{parent}.{node.attr}" if parent else node.attr  # Join only when a parent exists.
        return ""  # Dynamic calls have no safe dotted path.

    def _is_support_call_name(self, call_name: str) -> bool:
        if call_name in _SUPPORT_CALL_NAMES:  # Exact helper names are not source-under-test calls.
            return True  # Preserve the known-noise exclusion.
        if call_name[:1].isupper():  # Class constructors in tests usually build fixtures or services.
            return True  # Preserve constructor setup outside edge-case scope.
        return call_name.startswith(_SUPPORT_CALL_PREFIXES)  # Naming conventions catch fixture helpers.

    def _record_call_coverage(
        self,
        call: ast.Call,  # Candidate source-under-test call.
        domains: list[EdgeCaseDomain],  # Domains inferred for each call argument.
        coverage: EdgeCaseCoverage,  # File-level coverage accumulator.
    ) -> None:
        if not domains:  # A call without inferred domains is outside the rule scope.
            return  # Do not let literal values alone create applicability.
        coverage.requires_numeric_edges |= any(domain.numeric for domain in domains)  # Numeric domain obligation.
        coverage.requires_empty_input |= any(domain.collection for domain in domains)  # Collection domain obligation.
        coverage.requires_none_input |= any(domain.optional for domain in domains)  # Optional domain obligation.
        coverage.has_empty_input |= self._has_empty_container_arg(call)  # Record empty-input evidence.
        coverage.has_zero_value |= self._has_zero_arg(call)  # Record zero-value evidence.
        coverage.has_negative_value |= self._has_negative_int_arg(call)  # Record negative-value evidence.
        coverage.has_none_input |= self._has_none_arg(call)  # Record None-input evidence.

    def _call_args(self, call: ast.Call) -> list[ast.expr]:
        args = list(call.args)  # Copy positional arguments before keyword values are appended.
        args.extend(keyword.value for keyword in call.keywords)  # Include keyword values in coverage.
        return args  # Combined argument list for literal checks.

    def _has_empty_container_arg(self, call: ast.Call) -> bool:
        for node in self._call_args(call):  # Check every argument literal.
            if isinstance(node, ast.List | ast.Tuple | ast.Set) and not node.elts:  # Sequence literals use elts.
                return True  # Empty list, tuple, or set satisfies the collection edge.
            if isinstance(node, ast.Dict) and not node.keys:  # Dict keys carry emptiness instead of elts.
                return True  # Empty dict satisfies the collection edge.
            if isinstance(node, ast.Constant) and node.value in ("", b""):  # Empty strings are collection-like.
                return True  # Empty string or bytes satisfies the collection edge.
        return False  # No empty-input literal reached the source under test.

    def _has_zero_arg(self, call: ast.Call) -> bool:
        for node in self._call_args(call):  # Check every argument literal.
            if isinstance(node, ast.Constant) and isinstance(node.value, int):  # Integers include bool in Python.
                if not isinstance(node.value, bool) and node.value == 0:  # Boolean false is categorical.
                    return True  # Zero satisfies the numeric edge.
        return False  # No zero literal reached the source under test.

    def _has_negative_int_arg(self, call: ast.Call) -> bool:
        for node in self._call_args(call):  # Check every argument literal.
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):  # Negative literals use unary minus.
                operand = node.operand  # Read the literal behind the minus sign.
                if isinstance(operand, ast.Constant) and isinstance(operand.value, int):  # Confirm integer literal.
                    if not isinstance(operand.value, bool) and operand.value >= 1:  # Exclude boolean values.
                        return True  # Negative integer satisfies the numeric edge.
        return False  # No negative integer reached the source under test.

    def _has_none_arg(self, call: ast.Call) -> bool:
        for node in self._call_args(call):  # Check every argument literal.
            if isinstance(node, ast.Constant) and node.value is None:  # Match explicit None only.
                return True  # None satisfies the optional edge.
        return False  # No None literal reached the source under test.

    def _findings_for_coverage(self, posix: str, coverage: EdgeCaseCoverage) -> list[Finding]:
        findings: list[Finding] = []  # Accumulate uncovered edge cases in stable order.
        if coverage.requires_empty_input and not coverage.has_empty_input:  # Collection domain lacks empty test.
            findings.append(self._finding(posix, "missing_ec_empty_input", "No empty-input edge case is exercised."))
        if coverage.requires_numeric_edges and not coverage.has_zero_value:  # Numeric domain lacks zero test.
            findings.append(self._finding(posix, "missing_ec_zero_value", "No zero-value edge case is exercised."))
        if coverage.requires_numeric_edges and not coverage.has_negative_value:  # Numeric domain lacks negative test.
            findings.append(
                self._finding(posix, "missing_ec_negative_value", "No negative-value edge case is exercised.")
            )
        if coverage.requires_none_input and not coverage.has_none_input:  # Optional domain lacks None test.
            findings.append(self._finding(posix, "missing_ec_none_input", "No None-input edge case is exercised."))
        return findings  # Return all missing edge cases for this file.

    def _finding(self, posix: str, rule_id: str, explanation: str) -> Finding:
        return Finding(
            category=Category.MISSING_EDGE_CASE,  # Group all sub-rules under the edge-case category.
            rule_id=rule_id,  # Stable sub-rule identifier used by reports and baselines.
            severity=Severity.MEDIUM,  # Keep the existing detector severity.
            file_path=posix,  # File-level finding path.
            line_number=1,  # File-level finding points at the header.
            explanation=explanation,  # Human-facing reason.
            remediation="Add a test that passes the missing edge input to the source under test.",  # Shared fix.
            heuristic=True,  # Edge-case detection remains heuristic.
            related_source=posix,  # The test file owns the missing coverage action.
        )


DetectorRegistry.append(MissingEdgeCaseDetector())  # Register the detector for CLI discovery.
