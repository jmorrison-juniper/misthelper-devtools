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

    .venv\\Scripts\\python.exe tools/prompt_audit.py
    .venv\\Scripts\\python.exe tools/prompt_audit.py --menus 215,92,63
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
MENU_FILE = REPO / "MistHelper.py"
SOURCE_ROOTS = (REPO / "src",)

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


class FunctionIndex:
    """Find a function definition by its qualified or plain name."""

    def __init__(self) -> None:
        """Build the index across every source root."""
        self.by_qualname: dict[str, ast.FunctionDef] = {}  # "Class.method" to its node.
        self.by_name: dict[str, list[ast.FunctionDef]] = {}  # Plain name to every match.
        self._build()

    def _build(self) -> None:
        """Parse every source file once and record each function it defines."""
        for root in SOURCE_ROOTS:
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
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.by_qualname[f"{node.name}.{child.name}"] = child
                    self.by_name.setdefault(child.name, []).append(child)
                    methods.add(id(child))  # Remember it, so the next pass skips it.
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(node) not in methods:
                self.by_name.setdefault(node.name, []).append(node)

    def lookup(self, dotted: str) -> ast.FunctionDef | None:
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
        self._walk(node, found, depth=0, seen=set())
        return found, "ok"

    def _walk(self, node: ast.AST, found: list[str], depth: int, seen: set[str]) -> None:
        """Follow calls from one function and record each prompt it reaches."""
        if depth > MAX_DEPTH:
            return  # A deeper walk reaches unrelated code and reports noise.
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = self._call_name(child)
            if name is None:
                continue
            plain = name.rsplit(".", 1)[-1]
            if plain in PROMPT_HELPERS:
                found.append(PROMPT_HELPERS[plain])  # A known helper names its own kind.
                continue
            if plain in RAW_INPUT_NAMES:
                found.append("raw")  # A bare input call needs an answer of some kind.
                continue
            if plain in seen:
                continue  # A repeated call adds nothing and can loop forever.
            target = self.index.lookup(name)
            if target is not None:
                seen.add(plain)  # Record the visit before the descent, so recursion ends.
                self._walk(target, found, depth + 1, seen)

    @staticmethod
    def _call_name(call: ast.Call) -> str | None:
        """Return the dotted name of a call, or None when it has no name."""
        func = call.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            parts = [func.attr]  # Build the dotted name from the tail backward.
            node = func.value
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            return ".".join(reversed(parts))
        return None


def read_menu_handlers() -> dict[str, str]:
    """Return the handler name of every menu row in MistHelper.py."""
    text = MENU_FILE.read_text(encoding="utf-8", errors="replace")
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
    report = {}
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
