"""AST-based analyzers that detect violations of the project coding guidelines.

Each analyzer exposes a single ``analyze(context)`` method returning a list of
``Violation`` records. The analyzers intentionally stay independent so the engine
can run them in any combination.
"""

from __future__ import annotations  # Enable modern annotation syntax on Python 3.13.

import ast  # The standard library AST powers all structural inspection.

from .models import AnalysisContext, Severity, Violation  # Shared record/enum types.

# Structural thresholds derived from the project "5-Item Rule" guidelines.
MAX_PARAMETERS = 5  # Maximum parameters allowed per function (excluding self/cls).
MAX_FUNCTION_LINES = 25  # Soft maximum physical lines per function.
LONG_FUNCTION_LINES = 60  # Hard maximum beyond which length is a high-severity smell.
MAX_LOGICAL_BLOCKS = 5  # Maximum compound statements (if/for/while/with/try) per function.
MAX_NESTING_DEPTH = 4  # Maximum nesting depth before readability suffers.

# Cyclomatic complexity thresholds tuned around the project "rule of five".
COMPLEXITY_INFO = 5  # Above this complexity is worth noting (low severity).
COMPLEXITY_HIGH = 10  # Above this complexity is a high-severity concern.
COMPLEXITY_CRITICAL = 20  # Above this complexity is a critical refactor target.

# Inline-comment coverage targets from the NON-NEGOTIABLE comment guideline.
COMMENT_TARGET = 0.80  # Files should comment at least 80% of executable lines.
COMMENT_FLOOR = 0.50  # Below 50% coverage is treated as high severity.


class AstHelpers:
    """Reusable AST traversal helpers shared by every analyzer."""

    # Node types that open a new lexical scope and must not be descended into.
    _SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    @staticmethod
    def is_docstring(statement: ast.stmt) -> bool:
        """Return True when a statement is a bare string-literal docstring."""
        if not isinstance(statement, ast.Expr):  # Docstrings are expression statements.
            return False  # Anything else cannot be a docstring.
        value = statement.value  # Inspect the wrapped expression value.
        return isinstance(value, ast.Constant) and isinstance(value.value, str)  # String constant only.

    @classmethod
    def body_without_docstring(cls, function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
        """Return a function body with any leading docstring removed."""
        body = list(function.body)  # Copy so the original AST is never mutated.
        if body and cls.is_docstring(body[0]):  # Detect a leading docstring statement.
            return body[1:]  # Drop the docstring and return the remainder.
        return body  # No docstring; return the body unchanged.

    @staticmethod
    def parameter_count(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        """Return the parameter count, excluding a leading self/cls receiver."""
        arguments = function.args  # Access the arguments node once.
        positional = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]  # All named params.
        names = [argument.arg for argument in positional]  # Extract the identifier strings.
        count = len(names)  # Start from the named-parameter count.
        count += 1 if arguments.vararg else 0  # Count *args as a single parameter.
        count += 1 if arguments.kwarg else 0  # Count **kwargs as a single parameter.
        if names and names[0] in ("self", "cls"):  # Detect an instance/class receiver.
            count -= 1  # Receivers do not count toward the parameter budget.
        return count  # Return the adjusted parameter count.

    @staticmethod
    def parameter_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        """Return the set of parameter identifiers for a function."""
        arguments = function.args  # Access the arguments node once.
        names = {argument.arg for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)}
        if arguments.vararg:  # Include the *args name when present.
            names.add(arguments.vararg.arg)  # Add the variadic positional name.
        if arguments.kwarg:  # Include the **kwargs name when present.
            names.add(arguments.kwarg.arg)  # Add the variadic keyword name.
        return names  # Return all parameter identifiers.

    @staticmethod
    def has_decorator(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
        """Return True when the function carries a decorator with the given name."""
        for decorator in function.decorator_list:  # Inspect each decorator expression.
            if isinstance(decorator, ast.Name) and decorator.id == name:  # Bare-name decorator.
                return True  # Matched a simple decorator name.
            if isinstance(decorator, ast.Attribute) and decorator.attr == name:  # Dotted decorator.
                return True  # Matched an attribute-style decorator name.
        return False  # No matching decorator found.

    @classmethod
    def walk_body(cls, function: ast.FunctionDef | ast.AsyncFunctionDef):
        """Yield every node inside a function body without entering nested scopes."""
        stack = list(function.body)  # Seed the traversal with the top-level body.
        while stack:  # Continue until every reachable node is visited.
            node = stack.pop()  # Take the next node to inspect.
            yield node  # Surface the node to the caller.
            for child in ast.iter_child_nodes(node):  # Examine each direct child.
                if isinstance(child, cls._SCOPE_NODES):  # Nested functions/classes are separate scopes.
                    continue  # Skip nested scopes so their complexity is counted on their own.
                stack.append(child)  # Queue the child for traversal.


class StructuralComplexityAnalyzer:
    """Detect 5-Item-Rule and cyclomatic-complexity violations per function."""

    # Compound statements counted as logical blocks for the 5-block rule.
    _BLOCK_NODES = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        ast.Match,
    )

    # Compound statements that increase nesting depth.
    _NEST_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)

    def analyze(self, context: AnalysisContext) -> list[Violation]:
        """Return all structural/complexity violations found in the file."""
        violations: list[Violation] = []  # Collect findings across every function.
        for node in ast.walk(context.tree):  # Visit every node in the module.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):  # Only functions are checked.
                violations.extend(self._check_function(node, context))  # Append this function's findings.
        return violations  # Return the combined list.

    def _check_function(  # Signature gained `context` so noqa suppressions can be honored per-line.
        self, function: ast.FunctionDef | ast.AsyncFunctionDef, context: AnalysisContext
    ) -> list[Violation]:
        """Run every structural check against a single function."""
        found: list[Violation] = []  # Collect violations for this function.
        noqa = context.noqa_rules(function.lineno)  # Look up any noqa suppressions on the def line.
        if "STRUCT-PARAMS" not in noqa:  # Skip param-count check when explicitly suppressed.
            self._maybe(found, self._check_parameters(function))  # Parameter-count rule.
        if "STRUCT-LENGTH" not in noqa:  # Skip length check when explicitly suppressed.
            self._maybe(found, self._check_length(function))  # Function-length rule.
        if "STRUCT-COMPLEXITY" not in noqa:  # Skip complexity check when explicitly suppressed.
            self._maybe(found, self._check_complexity(function))  # Cyclomatic-complexity rule.
        if "STRUCT-BLOCKS" not in noqa:  # Skip block-count check when explicitly suppressed.
            self._maybe(found, self._check_blocks(function))  # Logical-block-count rule.
        if "STRUCT-NESTING" not in noqa:  # Skip nesting check when explicitly suppressed.
            self._maybe(found, self._check_nesting(function))  # Nesting-depth rule.
        return found  # Return all findings for this function.

    @staticmethod
    def _maybe(target: list[Violation], violation: Violation | None) -> None:
        """Append a violation to the list only when one was produced."""
        if violation is not None:  # Skip checks that returned no finding.
            target.append(violation)  # Record the produced violation.

    def _check_parameters(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> Violation | None:
        """Flag functions that exceed the maximum parameter budget."""
        parameter_count = AstHelpers.parameter_count(function)  # Count non-receiver parameters.
        if parameter_count <= MAX_PARAMETERS:  # Within budget is compliant.
            return None  # No violation to report.
        return Violation(
            rule_id="STRUCT-PARAMS",  # Stable rule identifier.
            category="Structure",  # Report grouping for structural rules.
            severity=Severity.HIGH,  # Excess parameters are a strong design smell.
            line=function.lineno,  # Location of the function definition.
            symbol=function.name,  # Name of the offending function.
            message=f"Function takes {parameter_count} parameters (limit {MAX_PARAMETERS}).",  # Issue text.
            remediation="Group related parameters into a dataclass/config object or split the function.",
        )

    def _check_length(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> Violation | None:
        """Flag functions longer than the allowed physical line budget."""
        end_line = function.end_lineno or function.lineno  # Fall back when end is missing.
        length = end_line - function.lineno + 1  # Inclusive physical line span.
        if length <= MAX_FUNCTION_LINES:  # Short functions are compliant.
            return None  # No violation to report.
        severity = Severity.HIGH if length > LONG_FUNCTION_LINES else Severity.MEDIUM  # Escalate very long ones.
        return Violation(
            rule_id="STRUCT-LENGTH",  # Stable rule identifier.
            category="Structure",  # Report grouping.
            severity=severity,  # Severity scales with length.
            line=function.lineno,  # Location of the function definition.
            symbol=function.name,  # Name of the offending function.
            message=f"Function spans {length} lines (limit {MAX_FUNCTION_LINES}).",  # Issue text.
            remediation="Extract logical sections into well-named helper methods to shrink the function.",
        )

    def _check_complexity(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> Violation | None:
        """Flag functions whose cyclomatic complexity is too high."""
        complexity = self.cyclomatic_complexity(function)  # Compute the McCabe complexity.
        severity = self._complexity_severity(complexity)  # Map the value to a severity.
        if severity is None:  # Low complexity needs no report.
            return None  # No violation to report.
        return Violation(
            rule_id="STRUCT-COMPLEXITY",  # Stable rule identifier.
            category="Complexity",  # Dedicated grouping for complexity findings.
            severity=severity,  # Severity scales with complexity.
            line=function.lineno,  # Location of the function definition.
            symbol=function.name,  # Name of the offending function.
            message=f"Cyclomatic complexity is {complexity} (target <= {COMPLEXITY_INFO}).",  # Issue text.
            remediation="Reduce branching by extracting helpers, using guard clauses, or simplifying logic.",
        )

    @staticmethod
    def _complexity_severity(complexity: int) -> Severity | None:
        """Map a complexity number to a severity, or None when acceptable."""
        if complexity > COMPLEXITY_CRITICAL:  # Extremely complex functions.
            return Severity.CRITICAL  # Treat as critical refactor target.
        if complexity > COMPLEXITY_HIGH:  # Highly complex functions.
            return Severity.HIGH  # Treat as a high-severity concern.
        if complexity > COMPLEXITY_INFO:  # Moderately complex functions.
            return Severity.LOW  # Surface as an informational signal.
        return None  # Simple functions are compliant.

    def _check_blocks(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> Violation | None:
        """Flag functions that contain too many compound logical blocks."""
        block_count = sum(  # Count compound statements within the function body.
            1 for node in AstHelpers.walk_body(function) if isinstance(node, self._BLOCK_NODES)
        )
        if block_count <= MAX_LOGICAL_BLOCKS:  # Within the block budget is compliant.
            return None  # No violation to report.
        return Violation(
            rule_id="STRUCT-BLOCKS",  # Stable rule identifier.
            category="Structure",  # Report grouping.
            severity=Severity.LOW,  # Block count overlaps complexity, so weigh it lightly.
            line=function.lineno,  # Location of the function definition.
            symbol=function.name,  # Name of the offending function.
            message=f"Function has {block_count} logical blocks (limit {MAX_LOGICAL_BLOCKS}).",  # Issue text.
            remediation="Split the function so each helper owns a single cohesive block of logic.",
        )

    def _check_nesting(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> Violation | None:
        """Flag functions whose statements nest more deeply than allowed."""
        depth = self._nesting_depth(function.body, 0)  # Compute the deepest nesting level.
        if depth <= MAX_NESTING_DEPTH:  # Shallow nesting is compliant.
            return None  # No violation to report.
        return Violation(
            rule_id="STRUCT-NESTING",  # Stable rule identifier.
            category="Structure",  # Report grouping.
            severity=Severity.MEDIUM,  # Deep nesting hurts readability.
            line=function.lineno,  # Location of the function definition.
            symbol=function.name,  # Name of the offending function.
            message=f"Maximum nesting depth is {depth} (limit {MAX_NESTING_DEPTH}).",  # Issue text.
            remediation="Flatten nesting with early returns, guard clauses, or extracted helper methods.",
        )

    @classmethod
    def cyclomatic_complexity(cls, function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        """Return the McCabe cyclomatic complexity of a function."""
        complexity = 1  # Every function has a baseline complexity of one.
        for node in AstHelpers.walk_body(function):  # Inspect each node in the body.
            complexity += cls._decision_points(node)  # Add the node's decision contribution.
        return complexity  # Return the accumulated complexity.

    @staticmethod
    def _decision_points(node: ast.AST) -> int:
        """Return how many decision points a single node contributes."""
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)):
            return 1  # Each branch-like construct adds one path.
        if isinstance(node, ast.BoolOp):  # Boolean operators chain conditions.
            return len(node.values) - 1  # Each extra operand adds a path.
        if isinstance(node, ast.comprehension):  # Comprehension generators branch.
            return 1 + len(node.ifs)  # The generator plus each filter add paths.
        if isinstance(node, (ast.Assert, ast.match_case)):  # Asserts and match cases branch.
            return 1  # Each adds a single path.
        return 0  # All other nodes add no complexity.

    @classmethod
    def _nesting_depth(cls, body: list[ast.stmt], base: int) -> int:
        """Return the deepest nesting level reached within a statement body."""
        deepest = base  # Track the deepest level seen.
        for statement in body:  # Examine each statement in this body.
            deepest = max(deepest, cls._statement_depth(statement, base))  # Recurse and keep the max.
        return deepest  # Return the deepest observed level.

    @classmethod
    def _statement_depth(cls, statement: ast.stmt, base: int) -> int:
        """Return the deepest nesting level contributed by one statement."""
        if isinstance(statement, AstHelpers._SCOPE_NODES):  # Nested scopes reset the depth count.
            return base  # Do not count nested function/class bodies.
        level = base + 1 if isinstance(statement, cls._NEST_NODES) else base  # Compound statements deepen.
        deepest = level  # Start from this statement's level.
        for child in cls._child_statements(statement):  # Recurse into nested statements.
            deepest = max(deepest, cls._statement_depth(child, level))  # Keep the deepest child level.
        return deepest  # Return the deepest level for this statement.

    @staticmethod
    def _child_statements(statement: ast.stmt) -> list[ast.stmt]:
        """Return the nested statements that live directly inside a statement."""
        nested: list[ast.stmt] = []  # Collect child statements to recurse into.
        for child in ast.iter_child_nodes(statement):  # Inspect each direct child node.
            if isinstance(child, ast.stmt):  # Plain nested statements (if/for bodies, else, etc.).
                nested.append(child)  # Queue the nested statement.
            elif isinstance(child, ast.ExceptHandler):  # Except handlers wrap their own bodies.
                nested.extend(child.body)  # Queue every statement inside the handler.
        return nested  # Return the collected nested statements.

    def function_complexities(self, tree: ast.Module) -> list[tuple[str, int, int]]:
        """Return (name, complexity, line) for every function in the module."""
        results: list[tuple[str, int, int]] = []  # Collect one entry per function.
        for node in ast.walk(tree):  # Visit every node in the module.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):  # Only functions qualify.
                results.append((node.name, self.cyclomatic_complexity(node), node.lineno))  # Record it.
        return results  # Return the per-function complexity data.

    def complexity_metrics(self, tree: ast.Module) -> dict[str, float]:
        """Return aggregate complexity metrics for the module."""
        complexities = self.function_complexities(tree)  # Gather per-function complexity data.
        if not complexities:  # Modules without functions have no complexity.
            return {"function_count": 0.0, "avg_complexity": 0.0, "max_complexity": 0.0}  # Zeroed metrics.
        values = [complexity for _, complexity, _ in complexities]  # Extract the complexity numbers.
        return {
            "function_count": float(len(values)),  # Total number of functions analyzed.
            "avg_complexity": round(sum(values) / len(values), 1),  # Mean complexity across functions.
            "max_complexity": float(max(values)),  # Highest single-function complexity.
        }


class ArchitecturalAnalyzer:
    """Detect wrappers, delegators, pointers/aliases, shims, and stubs."""

    # Right-hand-side node types that indicate a pass-through alias assignment.
    _ALIAS_RHS = (ast.Name, ast.Attribute)

    # Naming tokens that signal indirection layers the guidelines prohibit.
    _HIGH_TOKENS = (
        "wrapper",
        "shim",
        "proxy",
        "facade",
        "delegate",
        "passthrough",
        "pass_through",
        "forwarder",
        "adapter",
        "compat",
        "legacy",
        "alias",
    )

    # Naming tokens that are softer signals (helpers may sometimes be valid).
    _LOW_TOKENS = ("helper",)

    def analyze(self, context: AnalysisContext) -> list[Violation]:
        """Return all architectural violations found in the file."""
        violations: list[Violation] = []  # Collect findings across the module.
        violations.extend(self._scope_aliases(context.tree.body, "module"))  # Module-level aliases.
        nested_ids = self._collect_nested_function_ids(context.tree)  # Closures to skip in the delegation rule.
        for node in ast.walk(context.tree):  # Visit every node in the module.
            if isinstance(node, ast.ClassDef):  # Class bodies can also hold aliases.
                violations.extend(self._scope_aliases(node.body, node.name))  # Class-level aliases.
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):  # Functions get smell checks.
                nested = id(node) in nested_ids  # Closures forward outer-scope state; skip delegation.
                violations.extend(self._check_function(node, is_nested=nested))  # Wrapper/stub/naming checks.
        return violations  # Return the combined findings.

    @staticmethod
    def _collect_nested_function_ids(tree: ast.AST) -> set[int]:
        """Return the id() of every function nested inside another function.

        ``analyze`` walks the module with ``ast.walk`` which flattens the tree
        and loses parent context, so a closure looks identical to a top-level
        delegator. This pre-pass records the identity of every function found
        in another function's subtree, letting the delegation rule skip
        closures (which forward outer-scope state by design). ``id()`` is
        stable within a single ``analyze`` call because the AST nodes persist.
        """
        nested: set[int] = set()  # Collect identities of inner (closure) functions.
        for node in ast.walk(tree):  # Inspect every node looking for function parents.
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):  # Only functions can host closures.
                continue  # Skip non-function nodes.
            for descendant in ast.walk(node):  # Walk this function's own subtree.
                if descendant is node:  # The parent itself is not nested within itself.
                    continue  # Skip the parent node.
                if isinstance(descendant, (ast.FunctionDef, ast.AsyncFunctionDef)):  # Function inside a function.
                    nested.add(id(descendant))  # Record its identity as nested.
        return nested  # Return the set of closure identities.

    def _scope_aliases(self, body: list[ast.stmt], scope: str) -> list[Violation]:
        """Flag pass-through alias assignments at a module or class scope."""
        found: list[Violation] = []  # Collect alias violations in this scope.
        for statement in body:  # Inspect each top-level statement in the scope.
            alias = self._alias_assignment(statement)  # Detect an alias assignment.
            if alias is None:  # Skip non-alias statements.
                continue  # Move on to the next statement.
            name, is_pure_name = alias  # Unpack the alias target name and kind.
            found.append(self._alias_violation(name, is_pure_name, statement.lineno, scope))  # Record it.
        return found  # Return all alias violations for the scope.

    def _alias_assignment(self, statement: ast.stmt) -> tuple[str, bool] | None:
        """Return (name, is_pure_name) for an alias assignment, else None."""
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:  # Single-target only.
            return None  # Not an alias assignment.
        target = statement.targets[0]  # Inspect the assignment target.
        if not isinstance(target, ast.Name) or not isinstance(statement.value, self._ALIAS_RHS):  # Name = Name/Attr.
            return None  # Not an alias assignment.
        if target.id.isupper():  # ALL_CAPS targets are intentional constants.
            return None  # Constants are allowed, not flagged.
        if self._is_type_alias_placeholder(target.id, statement.value):  # Type aliases such as `MyFn = Any`.
            return None  # Type-only aliases are documentation, not architectural indirection.
        return target.id, isinstance(statement.value, ast.Name)  # Pure-name RHS is the classic alias.

    @staticmethod
    def _is_type_alias_placeholder(target_name: str, value: ast.expr) -> bool:
        """Return True when the assignment is a PEP 613-style placeholder type alias.

        Recognised forms (all common in this project before PEP 695 adoption):
            MyFn = Any                 # narrowest signal; bare `Any` is type-only
            MyFn = TypeAlias           # explicit typing.TypeAlias marker
            MyFn = typing.Any          # qualified `typing.Any`
        The target name must be PascalCase (not snake_case and not ALL_CAPS)
        to qualify -- runtime identifiers normally follow snake_case so this
        avoids accidentally exempting genuine pass-through aliases.
        """
        if not target_name or not target_name[0].isupper():  # PascalCase is the convention for type aliases.
            return False  # snake_case or _private targets are runtime identifiers, not type aliases.
        if "_" in target_name and target_name.lower() == target_name:  # snake_case fallback (paranoid).
            return False  # Belt-and-suspenders: never exempt snake_case names.
        if isinstance(value, ast.Name) and value.id in {"Any", "TypeAlias", "object"}:  # Bare type-alias markers.
            return True  # Treat as type alias and skip.
        if isinstance(value, ast.Attribute):  # Handle `typing.Any` / `typing.TypeAlias` qualified forms.
            return value.attr in {"Any", "TypeAlias"}  # Same marker set, on a qualified RHS.
        return False  # Anything else (e.g. `MyFn = SomeRealClass`) stays flagged.

    def _alias_violation(self, name: str, is_pure_name: bool, line: int, scope: str) -> Violation:
        """Build the violation for a detected alias/pointer assignment."""
        severity = Severity.HIGH if is_pure_name else Severity.MEDIUM  # Pure-name aliases are worse.
        return Violation(
            rule_id="ARCH-ALIAS",  # Stable rule identifier.
            category="Architecture",  # Report grouping for architecture rules.
            severity=severity,  # Severity depends on the alias kind.
            line=line,  # Location of the alias assignment.
            symbol=name,  # The alias name itself.
            message=f"'{name}' is a pass-through alias/pointer in {scope} scope.",  # Issue text.
            remediation="Remove the alias and update call sites to use the canonical symbol directly.",
        )

    def _check_function(
        self, function: ast.FunctionDef | ast.AsyncFunctionDef, is_nested: bool = False
    ) -> list[Violation]:
        """Run wrapper, stub, and naming checks against one function."""
        found: list[Violation] = []  # Collect findings for this function.
        naming = self._naming_violation(function)  # Naming-token smell check (applies to nested functions too).
        if naming is not None:  # Only append when a token matched.
            found.append(naming)  # Record the naming violation.
        if not is_nested and self._is_delegation(function):  # Delegation rule targets class/module-level funcs only.
            found.append(self._delegation_violation(function))  # Record the delegation violation.
        elif self._is_stub(function):  # Facade/stub check (mutually exclusive with delegation).
            found.append(self._stub_violation(function))  # Record the stub violation.
        return found  # Return all findings for this function.

    def _naming_violation(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> Violation | None:
        """Flag functions whose name signals a prohibited indirection layer."""
        lowered = function.name.lower()  # Normalize the name for token matching.
        for token in self._HIGH_TOKENS:  # Check the strong indirection tokens first.
            if token in lowered:  # The name embeds a prohibited token.
                return self._make_naming(function, token, Severity.MEDIUM)  # Medium-severity smell.
        for token in self._LOW_TOKENS:  # Check the softer helper tokens next.
            if token in lowered:  # The name embeds a helper token.
                return self._make_naming(function, token, Severity.LOW)  # Low-severity smell.
        return None  # No suspicious naming token found.

    @staticmethod
    def _make_naming(function: ast.FunctionDef | ast.AsyncFunctionDef, token: str, severity: Severity) -> Violation:
        """Build a naming-smell violation for a matched token."""
        return Violation(
            rule_id="ARCH-NAMING",  # Stable rule identifier.
            category="Architecture",  # Report grouping.
            severity=severity,  # Severity supplied by the caller.
            line=function.lineno,  # Location of the function definition.
            symbol=function.name,  # The offending function name.
            message=f"Name contains '{token}', signalling a wrapper/indirection layer.",  # Issue text.
            remediation="Fold the behavior into the owning class/method instead of a named indirection layer.",
        )

    def _is_delegation(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """Return True when a function only forwards to another call."""
        if function.name.startswith("__") and function.name.endswith("__"):  # Dunder forwarders are not wrappers.
            return False  # __call__/__getattr__ are Python's delegation protocol, never architectural shims.
        body = AstHelpers.body_without_docstring(function)  # Ignore any leading docstring.
        if len(body) != 1:  # Real logic has more than a single statement.
            return False  # Not a pure delegation.
        call = self._single_call(body[0])  # Extract a lone call expression if present.
        if call is None or not isinstance(call.func, (ast.Name, ast.Attribute)):  # Must call a named target.
            return False  # Not a delegation to a named callable.
        if self._called_name(call)[:1].isupper():  # CapWords target is a constructor/factory, not a wrapper.
            return False  # Building and returning an object is not pass-through delegation.
        return self._forwards_parameters(function, call)  # Confirm it forwards its own parameters.

    @staticmethod
    def _called_name(call: ast.Call) -> str:
        """Return the simple name of a call's target, or an empty string."""
        if isinstance(call.func, ast.Name):  # Direct function call such as real_add(...).
            return call.func.id  # The function identifier.
        if isinstance(call.func, ast.Attribute):  # Method call such as obj.method(...).
            return call.func.attr  # The method attribute name.
        return ""  # Unknown or complex call target.

    @staticmethod
    def _single_call(statement: ast.stmt) -> ast.Call | None:
        """Return the call expression if a statement is a lone call/return-call."""
        if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Call):  # return foo(...).
            return statement.value  # Surface the returned call.
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):  # bare foo(...).
            return statement.value  # Surface the fire-and-forget call.
        return None  # Statement is not a lone call.

    @classmethod
    def _forwards_parameters(cls, function: ast.FunctionDef | ast.AsyncFunctionDef, call: ast.Call) -> bool:
        """Return True when a call forwards the function's own parameters."""
        parameters = AstHelpers.parameter_names(function)  # Gather the function's parameter names.
        parameters.discard("self")  # Receivers are not meaningful forwarding evidence.
        parameters.discard("cls")  # Class receivers likewise.
        if not parameters:  # Zero-parameter functions are not treated as wrappers here.
            return False  # Avoid false positives on property-like one-liners.
        if any(cls._positional_forwards(argument, parameters) for argument in call.args):  # Positional/*args.
            return True  # A positional argument forwards a parameter.
        return any(cls._keyword_forwards(keyword, parameters) for keyword in call.keywords)  # Keyword/**kwargs.

    @staticmethod
    def _positional_forwards(argument: ast.expr, parameters: set[str]) -> bool:
        """Return True when a positional or starred argument forwards a parameter."""
        if isinstance(argument, ast.Starred) and isinstance(argument.value, ast.Name):  # *args forwarding.
            return argument.value.id in parameters  # The starred name is a parameter.
        return isinstance(argument, ast.Name) and argument.id in parameters  # Bare positional forwarding.

    @staticmethod
    def _keyword_forwards(keyword: ast.keyword, parameters: set[str]) -> bool:
        """Return True when a keyword or **kwargs argument forwards a parameter."""
        return isinstance(keyword.value, ast.Name) and keyword.value.id in parameters  # Keyword forwarding.

    def _delegation_violation(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> Violation:
        """Build the violation for a pass-through delegator function."""
        return Violation(
            rule_id="ARCH-DELEGATE",  # Stable rule identifier.
            category="Architecture",  # Report grouping.
            severity=Severity.HIGH,  # Delegators add indirection the guidelines forbid.
            line=function.lineno,  # Location of the function definition.
            symbol=function.name,  # The offending function name.
            message="Function only forwards its arguments to another call (pass-through wrapper).",  # Issue.
            remediation="Inline the call at its call sites or move the logic into the owning class method.",
        )

    def _is_stub(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """Return True when a function is an empty facade/stub."""
        if AstHelpers.has_decorator(function, "abstractmethod"):  # Abstract methods are legitimate stubs.
            return False  # Do not flag declared abstract methods.
        if function.name.startswith("__") and function.name.endswith("__"):  # Dunder stubs are often required.
            return False  # Avoid flagging empty dunder methods.
        body = AstHelpers.body_without_docstring(function)  # Ignore any leading docstring.
        if not body:  # A docstring-only body is an empty stub.
            return True  # Treat as a stub.
        if len(body) != 1:  # Real logic has more than one statement.
            return False  # Not a stub.
        return self._is_stub_statement(body[0])  # Check the single remaining statement.

    @classmethod
    def _is_stub_statement(cls, statement: ast.stmt) -> bool:
        """Return True when a lone statement is pass/.../raise NotImplementedError."""
        if isinstance(statement, ast.Pass):  # A bare pass body is a stub.
            return True  # Treat as a stub.
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):  # Ellipsis body.
            return statement.value.value is Ellipsis  # ... literal indicates a stub.
        if isinstance(statement, ast.Raise):  # A lone raise may be a not-implemented stub.
            return cls._raises_not_implemented(statement)  # Check the raised exception.
        return False  # Any other statement means it is not a stub.

    @staticmethod
    def _raises_not_implemented(statement: ast.Raise) -> bool:
        """Return True when a raise statement raises NotImplementedError."""
        exception = statement.exc  # Inspect the raised expression.
        if isinstance(exception, ast.Name):  # raise NotImplementedError.
            return exception.id == "NotImplementedError"  # Match the bare name.
        if isinstance(exception, ast.Call) and isinstance(exception.func, ast.Name):  # raise NotImplementedError().
            return exception.func.id == "NotImplementedError"  # Match the called name.
        return False  # Any other raise is not a stub marker.

    def _stub_violation(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> Violation:
        """Build the violation for an empty facade/stub function."""
        return Violation(
            rule_id="ARCH-STUB",  # Stable rule identifier.
            category="Architecture",  # Report grouping.
            severity=Severity.MEDIUM,  # Stubs are placeholder facades to resolve.
            line=function.lineno,  # Location of the function definition.
            symbol=function.name,  # The offending function name.
            message="Function body is an empty stub (pass/.../NotImplementedError).",  # Issue text.
            remediation="Implement the behavior or remove the stub if it is an unused compatibility facade.",
        )


class ConventionAnalyzer:
    """Detect convention violations: comments, logging, input safety, naming, paths."""

    # Logging method names that should use lazy %s formatting.
    _LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception", "log"}

    # Owner identifiers that indicate a logging call (logging.info, logger.debug...).
    _LOG_OWNERS = {"logging", "log", "logger", "LOG", "LOGGER"}

    def analyze(self, context: AnalysisContext) -> list[Violation]:
        """Return all convention violations found in the file."""
        violations: list[Violation] = []  # Collect findings across the file.
        coverage = self._check_comment_coverage(context)  # File-level inline-comment coverage.
        if coverage is not None:  # Only append when coverage is below target.
            violations.append(coverage)  # Record the coverage violation.
        violations.extend(self._check_tree(context.tree))  # Per-node convention checks.
        violations.extend(self._check_markers(context))  # AI placeholder-marker checks.
        return violations  # Return the combined findings.

    def _check_tree(self, tree: ast.Module) -> list[Violation]:
        """Run per-node convention checks across the whole module."""
        found: list[Violation] = []  # Collect per-node findings.
        enclosing_by_node = self._build_enclosing_function_map(tree)  # Map node id -> nearest enclosing function name.
        for node in ast.walk(tree):  # Visit every node in the module.
            enclosing = enclosing_by_node.get(id(node))  # Resolve enclosing function name for context.
            self._append(  # Raw input() usage with safe_input self-reference exemption.
                found, self._check_input(node, enclosing_function=enclosing)
            )
            self._append(found, self._check_logging(node))  # Logging f-string usage.
            self._append(found, self._check_path(node))  # Hardcoded path separators.
            self._append(found, self._check_loop_name(node))  # Single-letter loop variables.
        return found  # Return all per-node findings.

    @staticmethod
    def _build_enclosing_function_map(tree: ast.Module) -> dict[int, str]:
        """Map every AST node id() -> name of the nearest enclosing function (or '')."""
        mapping: dict[int, str] = {}  # Result map; missing entries imply module scope.
        stack: list[tuple[ast.AST, str]] = [(tree, "")]  # DFS stack with current function name context.
        while stack:  # Walk the tree iteratively to avoid Python recursion-depth limits.
            current, enclosing = stack.pop()  # Take the next node + its inherited enclosing function name.
            for child in ast.iter_child_nodes(current):  # Visit each direct child of the current node.
                child_enclosing = enclosing  # Inherit the parent's enclosing function name by default.
                if isinstance(  # Function definitions start a new scope -- record their name.
                    child, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    child_enclosing = child.name  # Update context so descendants see this function name.
                mapping[id(child)] = child_enclosing  # Record the enclosing-function name for this child.
                stack.append((child, child_enclosing))  # Recurse with the (possibly updated) context.
        return mapping  # Return the fully populated map.

    @staticmethod
    def _append(target: list[Violation], violation: Violation | None) -> None:
        """Append a violation only when a check produced one."""
        if violation is not None:  # Skip checks that returned nothing.
            target.append(violation)  # Record the produced violation.

    def _check_comment_coverage(self, context: AnalysisContext) -> Violation | None:
        """Flag files whose inline-comment coverage is below target."""
        code_lines = context.code_lines  # Executable lines that should carry comments.
        if not code_lines:  # Files with no executable code are trivially compliant.
            return None  # No violation to report.
        commented = context.inline_comment_lines & code_lines  # Lines that carry an inline comment.
        coverage = len(commented) / len(code_lines)  # Fraction of commented executable lines.
        if coverage >= COMMENT_TARGET:  # Meeting the target is compliant.
            return None  # No violation to report.
        severity = Severity.HIGH if coverage < COMMENT_FLOOR else Severity.MEDIUM  # Escalate very low coverage.
        missing = sorted(code_lines - context.inline_comment_lines)[:12]  # Sample of uncommented lines.
        sample = ", ".join(str(line) for line in missing)  # Render the sample as text.
        return Violation(
            rule_id="CONV-COMMENTS",  # Stable rule identifier.
            category="Conventions",  # Report grouping.
            severity=severity,  # Severity scales with how low coverage is.
            line=missing[0] if missing else 1,  # Point at the first uncommented line.
            symbol="<file>",  # Coverage is a file-level metric.
            message=f"Inline-comment coverage is {round(coverage * 100, 1)}%; uncommented lines: {sample}.",
            remediation="Add a same-line comment explaining intent on each executable line of changed code.",
        )

    @staticmethod
    def _check_input(node: ast.AST, *, enclosing_function: str | None = None) -> Violation | None:
        """Flag direct input() calls that are not EOF-safe.

        Exempts the canonical `safe_input` implementation itself -- the rule's
        whole purpose is to drive callers TO `safe_input`, so flagging the
        implementation would be self-referential.
        """
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):  # Only simple calls.
            return None  # Not an input() call.
        if node.func.id != "input":  # Only the builtin input function matters.
            return None  # Not an input() call.
        if enclosing_function == "safe_input":  # The canonical safe_input wrapper is allowed to call input().
            return None  # Self-reference exemption.
        return Violation(
            rule_id="CONV-INPUT",  # Stable rule identifier.
            category="Conventions",  # Report grouping.
            severity=Severity.MEDIUM,  # Unsafe input handling can crash on EOF.
            line=node.lineno,  # Location of the input() call.
            symbol="input",  # The offending builtin call.
            message="Direct input() call is not EOF-safe in SSH/container sessions.",  # Issue text.
            remediation="Wrap the prompt in safe_input(prompt, context=...) to handle EOF gracefully.",
        )

    @classmethod
    def _check_logging(cls, node: ast.AST) -> Violation | None:
        """Flag logging calls that use an f-string instead of lazy %s args."""
        if not isinstance(node, ast.Call) or not cls._is_log_call(node):  # Only logging calls qualify.
            return None  # Not a logging call.
        if not node.args or not isinstance(node.args[0], ast.JoinedStr):  # First arg must be an f-string.
            return None  # Lazy formatting already used.
        return Violation(
            rule_id="CONV-LOG-FSTRING",  # Stable rule identifier.
            category="Conventions",  # Report grouping.
            severity=Severity.LOW,  # Soft preference; the project tolerates some f-strings.
            line=node.lineno,  # Location of the logging call.
            symbol="<logging>",  # The offending logging call.
            message="Logging call uses an f-string instead of lazy %s arguments.",  # Issue text.
            remediation="Use logging.info('msg %s', value) so formatting is deferred until needed.",
        )

    @classmethod
    def _is_log_call(cls, node: ast.Call) -> bool:
        """Return True when a call looks like a logging method invocation."""
        function = node.func  # Inspect the called expression.
        if not isinstance(function, ast.Attribute) or function.attr not in cls._LOG_METHODS:  # method name.
            return False  # Not a recognized logging method.
        owner = function.value  # Inspect the object the method is called on.
        if isinstance(owner, ast.Name) and owner.id in cls._LOG_OWNERS:  # logging.info / logger.debug.
            return True  # Recognized logging owner.
        return isinstance(owner, ast.Attribute) and owner.attr in cls._LOG_OWNERS  # self.logger.info etc.

    @staticmethod
    def _check_path(node: ast.AST) -> Violation | None:
        """Flag string literals that hardcode Windows drive path separators.

        Heuristic: requires the literal to LOOK like a real drive path -- a
        letter immediately before the colon, followed by colon + backslash.
        This avoids flagging regex character classes (e.g. `r"[:\\-.]"`) that
        happen to contain `:\\` for unrelated reasons.
        """
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):  # Only string literals.
            return None  # Not a string literal.
        text = node.value  # Pull out the literal value once.
        drive_marker = ":" + chr(92)  # Colon + backslash, built from parts to avoid self-flagging.
        if drive_marker not in text:  # Cheap reject -- no drive-style fragment at all.
            return None  # No hardcoded drive path present.
        index = text.index(drive_marker)  # Locate the marker for context inspection.
        if index == 0:  # No preceding character to anchor against -- ambiguous, skip.
            return None  # Likely not a drive path (drives start with a letter).
        preceding = text[index - 1]  # Look at the char immediately before the colon.
        if not preceding.isalpha():  # Real drive paths look like `C:\` (letter, colon, backslash).
            return None  # Not a drive-letter pattern -- skip regex char classes etc.
        return Violation(
            rule_id="CONV-PATH",  # Stable rule identifier.
            category="Conventions",  # Report grouping.
            severity=Severity.LOW,  # Portability concern rather than a crash.
            line=node.lineno,  # Location of the string literal.
            symbol="<literal>",  # The offending literal.
            message="String literal hardcodes a Windows drive path separator.",  # Issue text.
            remediation="Build paths with os.path.join() or pathlib.Path for cross-platform support.",
        )

    @staticmethod
    def _check_loop_name(node: ast.AST) -> Violation | None:
        """Flag single-letter for-loop variables that hurt readability."""
        if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):  # Simple loop targets only.
            return None  # Not a single-name loop target.
        if len(node.target.id) != 1 or node.target.id == "_":  # Allow the throwaway underscore.
            return None  # Multi-character names are fine.
        return Violation(
            rule_id="CONV-NAME",  # Stable rule identifier.
            category="Conventions",  # Report grouping.
            severity=Severity.LOW,  # Naming is a style concern.
            line=node.lineno,  # Location of the loop statement.
            symbol=node.target.id,  # The single-letter variable name.
            message=f"Loop variable '{node.target.id}' is a single letter.",  # Issue text.
            remediation="Use a full descriptive name, e.g. 'for device in devices' not 'for d in devices'.",
        )

    @staticmethod
    def _check_markers(context: AnalysisContext) -> list[Violation]:
        """Flag AI editor placeholder/ellipsis marker text left in the source."""
        found: list[Violation] = []  # Collect marker findings.
        marker_phrase = "existing code"  # Phrase portion of the editor placeholder marker.
        ellipsis_token = chr(46) * 3  # Ellipsis portion, built from parts to avoid self-flagging.
        for index, text in enumerate(context.lines, start=1):  # Scan every physical line.
            if marker_phrase not in text.lower() or ellipsis_token not in text:  # Need both parts present.
                continue  # Not an editor placeholder marker line.
            found.append(
                Violation(
                    rule_id="CONV-MARKER",  # Stable rule identifier.
                    category="Conventions",  # Report grouping.
                    severity=Severity.MEDIUM,  # Leftover markers mean incomplete code.
                    line=index,  # Location of the marker line.
                    symbol="<file>",  # Markers are file-level artifacts.
                    message="AI editor placeholder/ellipsis marker text detected.",  # Issue text.
                    remediation="Remove the placeholder marker and replace it with the real code.",
                )
            )
        return found  # Return all marker findings.
