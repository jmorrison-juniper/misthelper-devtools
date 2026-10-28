"""Report the interactive prompts that each portal operation reaches.

Issue #3179 recorded 33 site-scoped operations that run with no site
selector. Issues #3151, #3152, #3155, and #3158 each record part of the
same defect for one category.

The portal answers an operation through ``web_input_context``. It feeds
one recorded answer to each ``input()`` call, in order. An operation
therefore needs one registry parameter for each prompt it reaches, in the
same order.

This tool reads the menu table in ``MistHelper.py``, finds the handler of
each row, and walks the call graph of that handler until it reaches a
known prompt helper. It prints the prompt list for every row.

The result is evidence for a registry entry. It is not a substitute for
running the operation, because a branch can skip a prompt.

Run it from the repository root:

    .venv\\Scripts\\python.exe -m misthelper_devtools.prompt_audit
    .venv\\Scripts\\python.exe -m misthelper_devtools.prompt_audit --menus 215,92,63
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
from typing import TypedDict

from misthelper_devtools.repository_root import resolve_repository_root  # One root answer for every tool here.

# The helpers that stop and ask the operator for one answer. The value names
# the parameter kind the portal must offer so that prompt never reaches a
# closed input stream.
PROMPT_HELPERS = {
    "select_site_id_from_csv": "site",
    "select_site": "site",
    "select_site_with_logging": "site",
    "select_device": "device",
    "select_device_from_site": "device",
    "select_client": "client",
}

# A direct call to one of these reads the operator input stream.
RAW_INPUT_NAMES = {"input", "safe_input", "input_fn"}

# How deep the walk follows a call before it gives up. A prompt sits close to
# its handler in this project, and an unbounded walk reaches the whole tree.
MAX_DEPTH = 4
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef  # Functions and async functions share the fields we inspect.


class PromptReportRow(TypedDict):
    """One JSON-serializable row in the prompt audit report."""

    handler: str | None  # Handler dotted name from the menu table.
    prompts: list[str]  # Prompt kinds reached by the call walk.
    note: str  # Confidence or missing-handler note.


def menu_file_for(repository_root: pathlib.Path | str | None = None) -> pathlib.Path:
    """Return the menu table file inside the repository this tool reads."""
    return resolve_repository_root(repository_root) / "MistHelper.py"  # The menu table lives at the root.


def source_roots_for(repository_root: pathlib.Path | str | None = None) -> tuple[pathlib.Path, ...]:
    """Return every directory the function index parses."""
    return (resolve_repository_root(repository_root) / "src",)  # Every handler lives under src.


class FunctionIndex:
    """Find a function definition by its qualified or plain name."""

    def __init__(self, repository_root: pathlib.Path | str | None = None) -> None:
        """Build the index across every source root.

        Args:
            repository_root: The checkout to parse. Omit it to search upward
                from the working directory for the nearest ``.git`` entry.
        """
        self.source_roots = source_roots_for(repository_root)  # Resolve once, so every read uses one root.
        self.by_qualname: dict[str, FunctionNode] = {}  # "Class.method" to its node.
        self.by_name: dict[str, list[FunctionNode]] = {}  # Plain name to every match.
        self._classes_by_name: dict[str, list[ast.ClassDef]] = {}  # Names map to every class definition.
        self._method_owners: dict[int, ast.ClassDef] = {}  # Function node identity maps to its owning class.
        self._methods_by_owner: dict[tuple[int, str], FunctionNode] = {}  # Exact class identity maps each method.
        self._build()

    def _build(self) -> None:
        """Parse every source file once and record each function it defines."""
        for root in self.source_roots:
            for path in root.rglob("*.py"):
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    continue  # A file the parser rejects cannot hold a handler this tool reads.
                self._index_tree(tree)

    def _index_tree(self, tree: ast.Module) -> None:
        """Record every function in one parsed module."""
        # Track the methods a class owns, so the standalone pass below cannot
        # index the same node twice. ast.walk visits a method as a child of its
        # class and again on its own, and a double count broke the unique-name
        # lookup this tool depends on.
        methods: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            self._classes_by_name.setdefault(node.name, []).append(node)  # Preserve duplicates for safe resolution.
            class_identity = id(node)  # Node identity distinguishes classes with the same spelling.
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.by_qualname[f"{node.name}.{child.name}"] = child
                    self.by_name.setdefault(child.name, []).append(child)
                    self._method_owners[id(child)] = node  # Record exact ownership for self and cls calls.
                    self._methods_by_owner[(class_identity, child.name)] = child
                    methods.add(id(child))  # Remember it, so the next pass skips it.
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(node) not in methods:
                self.by_name.setdefault(node.name, []).append(node)

    def lookup(self, dotted: str) -> FunctionNode | None:
        """Return the function a dotted handler name points at."""
        if dotted in self.by_qualname:
            return self.by_qualname[dotted]
        parts = dotted.split(".")
        if len(parts) >= 2:
            # A call can arrive through a resolver, as in
            # mh.SiteDeviceExporter._resolve_site_for_stats. The last two parts
            # still name the class and the method.
            tail = f"{parts[-2]}.{parts[-1]}"
            if tail in self.by_qualname:
                return self.by_qualname[tail]
        plain = parts[-1]  # Fall back to the bare method name.
        matches = self.by_name.get(plain, [])
        return matches[0] if len(matches) == 1 else None


class PromptWalker:
    """Collect the prompts one handler reaches, in call order."""

    def __init__(self, index: FunctionIndex) -> None:
        """Store the function index this walker reads."""
        self.index = index

    def prompts_for(self, dotted: str) -> tuple[list[str], str]:
        """Return the prompt kinds a handler reaches and a confidence note."""
        node = self.index.lookup(dotted)
        if node is None:
            return [], "handler not found"
        found: list[str] = []  # Prompts in the order the walk meets them.
        _CallTraversal(self.index, found).walk_callable(node, depth=0)
        return found, "ok"


class _CallResolver:
    """Resolve only callable and receiver targets supported by source evidence."""

    def __init__(self, index: FunctionIndex) -> None:
        """Store the source index used to resolve classes and functions."""
        self.index = index

    def target_for(
        self,
        call: ast.Call,
        owner: ast.ClassDef | None,
        receivers: dict[str, ast.ClassDef],
    ) -> FunctionNode | None:
        """Return a proven target for a bare, constructed, or class-bound call."""
        func = call.func
        if isinstance(func, ast.Name):
            if func.id in self.index._classes_by_name:
                return None
            return self.index.lookup(func.id)
        if not isinstance(func, ast.Attribute):
            return None
        if isinstance(func.value, ast.Name) and func.value.id in {"self", "cls"}:
            class_node = owner
        else:
            class_node = self.class_for_value(func.value, receivers)
        if class_node is None:
            return None
        return self.index._methods_by_owner.get((id(class_node), func.attr))

    def class_for_value(self, value: ast.AST, receivers: dict[str, ast.ClassDef]) -> ast.ClassDef | None:
        """Return a unique class for a local receiver or dotted constructor."""
        if isinstance(value, ast.Name):
            return receivers.get(value.id) or self._unique_class(value.id)
        if isinstance(value, ast.Call):
            return self.class_for_constructor(value.func)
        if isinstance(value, ast.Attribute):
            return self._unique_class(value.attr)
        return None

    def class_for_constructor(self, constructor: ast.expr) -> ast.ClassDef | None:
        """Return a unique class named by a direct constructor expression."""
        if isinstance(constructor, ast.Name):
            return self._unique_class(constructor.id)
        if isinstance(constructor, ast.Attribute):
            return self._unique_class(constructor.attr)
        return None

    def _unique_class(self, name: str) -> ast.ClassDef | None:
        """Return a class only when its simple name identifies one definition."""
        matches = self.index._classes_by_name.get(name, [])
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def call_name(call: ast.Call) -> str | None:
        """Return a dotted name for a call expression with a named function."""
        func = call.func
        if isinstance(func, ast.Name):
            return func.id
        if not isinstance(func, ast.Attribute):
            return None
        parts = [func.attr]  # Build the dotted name from the tail backward.
        node = func.value
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts)) if isinstance(node, ast.Name) else None


class _CallTraversal:
    """Visit statements and calls in source order with a path-local recursion guard."""

    def __init__(self, index: FunctionIndex, found: list[str]) -> None:
        """Store shared report state and the resolver for this audit."""
        self.index = index
        self.found = found
        self.resolver = _CallResolver(index)
        self.active: set[int] = set()

    def walk_callable(self, node: FunctionNode, depth: int) -> None:
        """Visit one callable unless its depth or active path blocks descent."""
        identity = id(node)
        if depth > MAX_DEPTH or identity in self.active:
            return
        self.active.add(identity)
        try:
            owner = self.index._method_owners.get(identity)
            self.visit_statements(node.body, depth, owner, {})
        finally:
            self.active.remove(identity)

    def visit_statements(
        self,
        statements: list[ast.stmt],
        depth: int,
        owner: ast.ClassDef | None,
        receivers: dict[str, ast.ClassDef],
    ) -> None:
        """Visit one statement list in its recorded source order."""
        for statement in statements:
            self.visit_statement(statement, depth, owner, receivers)

    def visit_statement(
        self,
        statement: ast.stmt,
        depth: int,
        owner: ast.ClassDef | None,
        receivers: dict[str, ast.ClassDef],
    ) -> None:
        """Visit one statement and update only its supported local bindings."""
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            _visit_assignment(statement, self, depth, owner, receivers)
            return
        if isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While)):
            _visit_control_statement(statement, self, depth, owner, receivers)
            return
        if isinstance(statement, ast.Try):
            _visit_try_statement(statement, self, depth, owner, receivers)
            return
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            _visit_with_statement(statement, self, depth, owner, receivers)
            return
        _visit_generic_statement(statement, self, depth, owner, receivers)

    def visit_expression(
        self,
        node: ast.AST,
        depth: int,
        owner: ast.ClassDef | None,
        receivers: dict[str, ast.ClassDef],
    ) -> None:
        """Visit nested expressions before resolving the enclosing call."""
        if isinstance(node, ast.stmt):
            return
        if isinstance(node, ast.Call):
            _visit_call_expression(node, self, depth, owner, receivers)
            return
        if isinstance(node, ast.NamedExpr):
            _visit_named_expression(node, self, depth, owner, receivers)
            return
        for child in ast.iter_child_nodes(node):
            self.visit_expression(child, depth, owner, receivers)


def _visit_assignment(
    statement: ast.Assign | ast.AnnAssign,
    traversal: _CallTraversal,
    depth: int,
    owner: ast.ClassDef | None,
    receivers: dict[str, ast.ClassDef],
) -> None:
    """Update receiver bindings after visiting an assignment value."""
    value = statement.value
    if value is not None:
        traversal.visit_expression(value, depth, owner, receivers)
    class_node = traversal.resolver.class_for_value(value, receivers) if value else None
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    for target in targets:
        for child in ast.walk(target):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                if class_node is None:
                    receivers.pop(child.id, None)
                else:
                    receivers[child.id] = class_node


def _visit_generic_statement(
    statement: ast.stmt,
    traversal: _CallTraversal,
    depth: int,
    owner: ast.ClassDef | None,
    receivers: dict[str, ast.ClassDef],
) -> None:
    """Visit a statement's children and clear bindings changed by control flow."""
    for child in ast.iter_child_nodes(statement):
        if isinstance(child, ast.stmt):
            traversal.visit_statements([child], depth, owner, receivers.copy())
        else:
            traversal.visit_expression(child, depth, owner, receivers)


def _visit_try_statement(
    statement: ast.Try,
    traversal: _CallTraversal,
    depth: int,
    owner: ast.ClassDef | None,
    receivers: dict[str, ast.ClassDef],
) -> None:
    """Visit each try suite sequentially with isolated branch evidence."""
    traversal.visit_statements(statement.body, depth, owner, receivers.copy())
    for handler in statement.handlers:
        handler_receivers = receivers.copy()
        if handler.name is not None:
            handler_receivers.pop(handler.name, None)
        traversal.visit_statements(handler.body, depth, owner, handler_receivers)
    traversal.visit_statements(statement.orelse, depth, owner, receivers.copy())
    traversal.visit_statements(statement.finalbody, depth, owner, receivers.copy())
    for name in _stored_names(statement):
        receivers.pop(name, None)


def _visit_with_statement(
    statement: ast.With | ast.AsyncWith,
    traversal: _CallTraversal,
    depth: int,
    owner: ast.ClassDef | None,
    receivers: dict[str, ast.ClassDef],
) -> None:
    """Visit a with suite in order and retain its same-scope assignments."""
    for item in statement.items:
        traversal.visit_expression(item.context_expr, depth, owner, receivers)
        if item.optional_vars is not None:
            for name in _stored_names(item.optional_vars):
                receivers.pop(name, None)
    traversal.visit_statements(statement.body, depth, owner, receivers)


def _visit_call_expression(
    call: ast.Call,
    traversal: _CallTraversal,
    depth: int,
    owner: ast.ClassDef | None,
    receivers: dict[str, ast.ClassDef],
) -> None:
    """Visit call inputs in Python order, then record or follow the call."""
    traversal.visit_expression(call.func, depth, owner, receivers)
    arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
    for argument in arguments:
        traversal.visit_expression(argument, depth, owner, receivers)
    name = traversal.resolver.call_name(call)
    plain = name.rsplit(".", 1)[-1] if name is not None else None
    if plain is None and isinstance(call.func, ast.Attribute):
        plain = call.func.attr
    if plain is None:
        return
    if plain in PROMPT_HELPERS:
        traversal.found.append(PROMPT_HELPERS[plain])
    elif plain in RAW_INPUT_NAMES:
        traversal.found.append("raw")
    else:
        target = traversal.resolver.target_for(call, owner, receivers)
        if target is not None:
            traversal.walk_callable(target, depth + 1)


def _visit_named_expression(
    expression: ast.NamedExpr,
    traversal: _CallTraversal,
    depth: int,
    owner: ast.ClassDef | None,
    receivers: dict[str, ast.ClassDef],
) -> None:
    """Update a receiver binding after evaluating a named expression."""
    traversal.visit_expression(expression.value, depth, owner, receivers)
    class_node = traversal.resolver.class_for_value(expression.value, receivers)
    if isinstance(expression.target, ast.Name):
        if class_node is None:
            receivers.pop(expression.target.id, None)
        else:
            receivers[expression.target.id] = class_node


def _visit_control_statement(
    statement: ast.If | ast.For | ast.AsyncFor | ast.While,
    traversal: _CallTraversal,
    depth: int,
    owner: ast.ClassDef | None,
    receivers: dict[str, ast.ClassDef],
) -> None:
    """Visit each control-flow branch with an isolated receiver map."""
    if isinstance(statement, ast.If):
        traversal.visit_expression(statement.test, depth, owner, receivers)
        branch_receivers = receivers.copy()
        traversal.visit_statements(statement.body, depth, owner, branch_receivers)
        traversal.visit_statements(statement.orelse, depth, owner, receivers.copy())
    elif isinstance(statement, (ast.For, ast.AsyncFor)):
        traversal.visit_expression(statement.iter, depth, owner, receivers)
        branch_receivers = receivers.copy()
        for name in _stored_names(statement.target):
            branch_receivers.pop(name, None)
        traversal.visit_statements(statement.body, depth, owner, branch_receivers)
        traversal.visit_statements(statement.orelse, depth, owner, receivers.copy())
    else:
        traversal.visit_expression(statement.test, depth, owner, receivers)
        branch_receivers = receivers.copy()
        traversal.visit_statements(statement.body, depth, owner, branch_receivers)
        traversal.visit_statements(statement.orelse, depth, owner, receivers.copy())
    for name in _stored_names(statement):
        receivers.pop(name, None)


def _stored_names(node: ast.AST) -> set[str]:
    """Return simple names that an assignment or control-flow node writes."""
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)}


def read_menu_handlers(repository_root: pathlib.Path | str | None = None) -> dict[str, str]:
    """Return the handler name of every menu row in MistHelper.py.

    Args:
        repository_root: The checkout to read. Omit it to search upward from
            the working directory for the nearest ``.git`` entry.
    """
    text = menu_file_for(repository_root).read_text(encoding="utf-8", errors="replace")
    # Each row opens with its key and carries a handler on a later line.
    pattern = re.compile(r'"(?P<key>[\w.]+)":\s*GlobalImportManager\.MenuEntry\((?P<body>.*?)\n    \),', re.S)
    handlers: dict[str, str] = {}
    for match in pattern.finditer(text):
        body = match.group("body")
        found = re.search(r"handler=([\w.]+)", body)
        if found:
            handlers[match.group("key")] = found.group(1)
    return handlers


def main() -> int:
    """Print the prompt audit for the requested menu rows."""
    parser = argparse.ArgumentParser(description="Report the prompts each operation reaches.")
    parser.add_argument("--menus", default="", help="Comma separated menu numbers. Omit for every row.")
    parser.add_argument("--json", action="store_true", help="Print a machine readable report.")
    parser.add_argument("--only-prompting", action="store_true", help="Print only rows that reach a prompt.")
    arguments = parser.parse_args()

    handlers = read_menu_handlers()
    wanted = [m.strip() for m in arguments.menus.split(",") if m.strip()] or sorted(
        handlers, key=lambda k: float(k.replace("a", ".1"))
    )

    index = FunctionIndex()
    walker = PromptWalker(index)
    report: dict[str, PromptReportRow] = {}
    for menu in wanted:
        dotted = handlers.get(menu)
        if dotted is None:
            report[menu] = {"handler": None, "prompts": [], "note": "no menu row"}
            continue
        prompts, note = walker.prompts_for(dotted)
        report[menu] = {"handler": dotted, "prompts": prompts, "note": note}

    if arguments.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"indexed {len(index.by_qualname)} methods, {len(handlers)} menu rows\n")
    print(f'{"menu":>6}  {"prompts":<22} {"handler":<46} note')
    shown = 0
    for menu, row in report.items():
        if arguments.only_prompting and not row["prompts"]:
            continue
        shown += 1
        kinds = ",".join(row["prompts"]) or "-"
        print(f'{menu:>6}  {kinds:<22} {str(row["handler"])[:44]:<46} {row["note"]}')
    print(f"\nrows printed: {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
