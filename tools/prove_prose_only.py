"""Prove that a documentation sweep changed no executable code.

Why:
    Issue #1796 records a sweep that deleted a live declaration inside a
    515-line comment-only difference. A reviewer read the difference as safe.

    This check compares the syntax tree of each changed file against the tree
    of the base revision. It removes every docstring first, because a
    documentation sweep rewrites a docstring on purpose. A comment never
    reaches the tree at all.

    Any other difference means the sweep touched code, and the sweep must stop.

Usage:
    python tools/prove_prose_only.py <base-revision>
"""

from __future__ import annotations

import ast
import shutil
import subprocess  # nosec B404 - The module queries git, and each call below uses shell=False.
import sys


def strip_docstrings(tree: ast.AST) -> ast.AST:
    """Remove every docstring from one syntax tree.

    Args:
        tree: The parsed module.

    Returns:
        The same tree with each docstring expression removed.
    """
    for node in ast.walk(tree):  # Every node that may carry a docstring.
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue  # No other node holds a docstring.
        body = node.body  # The statement list of this node.
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):  # The first statement is a docstring.
                node.body = body[1:] or [ast.Pass()]  # A body must hold one statement.
    return tree


def code_shape(source: str) -> str:
    """Return the code of one module with every docstring removed.

    Args:
        source: The module text.

    Returns:
        A stable text form of the syntax tree.
    """
    return ast.dump(strip_docstrings(ast.parse(source)))  # The tree holds no comment at all.


def git_path() -> str:
    """Return the absolute path of the git program.

    Why:
        A bare name reads the search path, and an earlier entry could supply
        another program of the same name.

    Returns:
        The absolute path of git.

    Raises:
        RuntimeError: If the search path holds no git program.
    """
    found = shutil.which("git")  # An absolute path stops an earlier entry supplying another program.
    if found is None:  # No git means the check cannot read the base revision.
        raise RuntimeError("This check needs git, and the search path holds none.")
    return found


def base_source(revision: str, path: str) -> str | None:
    """Return the text of one file at one revision.

    Args:
        revision: The git revision to read.
        path: The repository path of the file.

    Returns:
        The file text, or None when the revision holds no such file.
    """
    result = subprocess.run(  # nosec B603 - shutil.which resolved the path and the rest are literals.
        [git_path(), "show", f"{revision}:{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def changed_files(revision: str) -> list[str]:
    """Return every changed Python file against one revision.

    Args:
        revision: The git revision to compare against.

    Returns:
        The repository path of each changed Python file.
    """
    result = subprocess.run(  # nosec B603 - shutil.which resolved the path and the rest are literals.
        [git_path(), "diff", "--name-only", revision],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.endswith(".py")]


def main(argv: list[str]) -> int:
    """Compare every changed module against the base revision.

    Args:
        argv: The command line. The first entry is the base revision.

    Returns:
        Zero when the sweep changed prose alone.
    """
    if not argv:
        print("usage: python tools/prove_prose_only.py <base-revision>")
        return 2
    revision = argv[0]  # The revision that the sweep started from.
    touched: list[str] = []  # Every file whose code changed.
    for path in changed_files(revision):  # One comparison for each changed module.
        before = base_source(revision, path)  # The text before the sweep.
        if before is None:  # A new file has no earlier code to compare.
            print(f"new file: {path}")
            continue
        with open(path, encoding="utf-8") as handle:
            after = handle.read()  # The text after the sweep.
        if code_shape(before) != code_shape(after):  # The tree changed beyond a docstring.
            touched.append(path)
    for path in touched:  # Name each file that the sweep must explain.
        print(f"CODE CHANGED: {path}")
    print(f"checked {len(changed_files(revision))} file(s), {len(touched)} with a code change")
    return 1 if touched else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
