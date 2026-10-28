"""Test prompt-audit call resolution with isolated Python source fixtures."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from misthelper_devtools.prompt_audit import FunctionIndex, PromptWalker

CONSTRUCTED_SOURCE = dedent("""
    class SiteWorker:
        def prompt(self):
            select_site()

        def unique_prompt(self):
            select_device()

    class DeviceWorker:
        def prompt(self):
            select_client()

    def handler():
        worker = SiteWorker()
        worker.prompt()
        alias = worker
        alias.prompt()
        worker.unique_prompt()
        worker = unknown_worker()
        worker.unique_prompt()
        unresolved = unknown_worker()
        unresolved.unique_prompt()
        if enabled:
            branch_worker = SiteWorker()
            branch_worker.prompt()
    """)

CLASS_CONTEXT_SOURCE = dedent("""
    class SiteWorker:
        def prompt(self):
            select_site()

        def via_self(self):
            self.prompt()

        @classmethod
        def via_cls(cls):
            cls.prompt()

    class DeviceWorker:
        def prompt(self):
            select_device()

        def via_self(self):
            self.prompt()

        @classmethod
        def via_cls(cls):
            cls.prompt()

    def unresolved():
        unknown_worker.prompt()
    """)

ORDERED_CALL_SOURCE = dedent("""
    def prompt_helper():
        select_client()

    def sink(*values, **options):
        return None

    def handler():
        sink(option=select_site(), *[select_device()])
        prompt_helper()
        prompt_helper()
        select_client()
    """)

RECURSION_SOURCE = dedent("""
    def direct_handler():
        direct()
        direct()

    def direct():
        select_site()
        direct()

    def mutual_handler():
        first()

    def first():
        select_site()
        second()

    def second():
        select_device()
        third()

    def third():
        select_client()
        fourth()

    def fourth():
        select_site()
        first()
        level_five()

    def depth_handler():
        level_one()

    def level_one():
        level_two()

    def level_two():
        level_three()

    def level_three():
        level_four()

    def level_four():
        select_client()
        level_five()

    def level_five():
        select_device()
    """)

QUALIFIED_CONSTRUCTOR_SOURCE = dedent("""
    class SiteWorker:
        def prompt(self):
            select_site()

    class DeviceWorker:
        def prompt(self):
            select_device()

    class Worker:
        def prompt(self):
            select_device()

    class Class:
        @staticmethod
        def method():
            select_client()

    class Ambiguous:
        def prompt(self):
            select_site()

    class Ambiguous:
        def prompt(self):
            select_device()

    def handler():
        SiteWorker().prompt()
        module.DeviceWorker().prompt()
        Worker().prompt()
        module.Worker().prompt()
        Class.method()
        mh.Class.method()
        resolver.Class.method()
        consume(SiteWorker().prompt())
        Ambiguous().prompt()
        unknown_receiver.prompt()
    """)

COMPOUND_STATEMENT_SOURCE = dedent("""
    class Worker:
        def prompt(self):
            select_site()

    def handler():
        try:
            worker = Worker()
            worker.prompt()
        except RuntimeError:
            worker = Worker()
            worker.prompt()
            def deferred():
                select_client()
        with context_manager():
            worker = Worker()
            worker.prompt()
    """)


def build_walker(source_root: Path, source: str) -> PromptWalker:
    """Build a prompt walker from one temporary source module."""
    source_directory = source_root / "src"
    source_directory.mkdir()
    (source_directory / "fixture.py").write_text(source, encoding="utf-8")
    return PromptWalker(FunctionIndex(source_root))


def test_constructed_receivers_and_simple_aliases_resolve_conservatively(tmp_path: Path) -> None:
    """Resolve constructed and aliased receivers but reject unknown receiver types."""
    walker = build_walker(tmp_path, CONSTRUCTED_SOURCE)
    prompts, note = walker.prompts_for("handler")

    assert prompts == ["site", "site", "device", "site"]
    assert note == "ok"


def test_self_and_cls_calls_use_the_exact_owning_class(tmp_path: Path) -> None:
    """Resolve self and cls calls within their owner despite duplicate method names."""
    walker = build_walker(tmp_path, CLASS_CONTEXT_SOURCE)
    self_prompts, _ = walker.prompts_for("SiteWorker.via_self")
    cls_prompts, _ = walker.prompts_for("DeviceWorker.via_cls")
    unresolved_prompts, _ = walker.prompts_for("unresolved")

    assert self_prompts == ["site"]
    assert cls_prompts == ["device"]
    assert unresolved_prompts == []


def test_call_evaluation_order_and_repeated_helper_occurrences(tmp_path: Path) -> None:
    """Keep Python argument evaluation order and every repeated helper call site."""
    walker = build_walker(tmp_path, ORDERED_CALL_SOURCE)
    prompts, _ = walker.prompts_for("handler")

    assert prompts == ["device", "site", "client", "client", "client"]


def test_recursive_paths_terminate_and_include_the_four_edge_boundary(tmp_path: Path) -> None:
    """Stop direct and mutual cycles while retaining distinct reachable occurrences."""
    walker = build_walker(tmp_path, RECURSION_SOURCE)
    direct_prompts, _ = walker.prompts_for("direct_handler")
    mutual_prompts, _ = walker.prompts_for("mutual_handler")
    boundary_prompts, _ = walker.prompts_for("depth_handler")

    assert direct_prompts == ["site", "site"]
    assert mutual_prompts == ["site", "device", "client", "site"]
    assert boundary_prompts == ["client"]


def test_dotted_and_constructed_receivers_resolve_only_unique_class_targets(tmp_path: Path) -> None:
    """Resolve ordinary dotted and constructed class calls without guessing ambiguous receivers."""
    walker = build_walker(tmp_path, QUALIFIED_CONSTRUCTOR_SOURCE)
    prompts, _ = walker.prompts_for("handler")

    assert prompts == ["site", "device", "device", "device", "client", "client", "client", "site"]


def test_compound_statement_suites_keep_sequential_bindings_and_skip_nested_functions(tmp_path: Path) -> None:
    """Keep local receiver evidence within suites and ignore deferred function bodies."""
    walker = build_walker(tmp_path, COMPOUND_STATEMENT_SOURCE)
    prompts, _ = walker.prompts_for("handler")

    assert prompts == ["site", "site", "site"]
