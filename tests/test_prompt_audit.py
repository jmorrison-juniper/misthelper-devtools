"""Tests for prompt call resolution in the static menu audit."""

from pathlib import Path

from misthelper_devtools.prompt_audit import FunctionIndex, PromptWalker


def _build_walker(tmp_path: Path, source: str) -> PromptWalker:
    """Create an isolated source tree for one prompt-walk test."""
    source_root = tmp_path / "src"  # Keep indexed files inside the temporary repository.
    source_root.mkdir()  # Give FunctionIndex one valid source directory.
    (source_root / "operations.py").write_text(source, encoding="utf-8")  # Store the synthetic call graph.
    return PromptWalker(FunctionIndex(tmp_path))  # Use the same index as the production audit.


class TestPromptWalker:
    """Prove prompt resolution for object calls, order, and recursive helpers."""

    def test_constructed_object_method_reaches_prompt(self, tmp_path: Path) -> None:
        """Resolve a method call through a locally constructed object."""
        walker = _build_walker(
            tmp_path,
            """\
class SiteSelector:  # Give the instance method a class owner.
    def select(self):  # Model the prompt-bearing method.
        select_site()  # Record the site prompt.
def handler():  # Model the menu handler.
    selector = SiteSelector()  # Construct the method receiver.
    selector.select()  # Call the method through its local instance.
    SiteSelector().select()  # Call the method on a direct construction.
""",
        )

        prompts, note = walker.prompts_for("handler")  # Walk the constructed-object call.

        assert prompts == ["site", "site"]  # Keep both constructed-object prompts.
        assert note == "ok"  # Confirm the handler resolved.

    def test_self_and_cls_calls_resolve_to_their_class_methods(self, tmp_path: Path) -> None:
        """Resolve instance and class method calls within their owning class."""
        walker = _build_walker(
            tmp_path,
            """\
class PromptSource:  # Keep both receiver forms in one owner.
    def choose_site(self):  # Model an instance method call.
        self.ask_site()  # Resolve self through the owning class.
    def ask_site(self):  # Provide the site prompt method.
        select_site()  # Record the site prompt.
    @classmethod  # Mark the class method receiver.
    def choose_device(cls):  # Model a class method call.
        cls.ask_device()  # Resolve cls through the owning class.
    @classmethod  # Mark the class method receiver.
    def ask_device(cls):  # Provide the device prompt method.
        select_device()  # Record the device prompt.
def handler():  # Model the menu handler.
    PromptSource().choose_site()  # Reach the instance method.
    PromptSource.choose_device()  # Reach the class method.
""",
        )

        prompts, note = walker.prompts_for("handler")  # Walk both class-method forms.

        assert prompts == ["site", "device"]  # Preserve the source prompt order.
        assert note == "ok"  # Confirm the handler resolved.

    def test_repeated_helper_calls_count_each_prompt(self, tmp_path: Path) -> None:
        """Count each completed helper call instead of treating it as recursion."""
        walker = _build_walker(
            tmp_path,
            """\
def prompt_helper():  # Model a reusable prompt helper.
    select_site()  # Record one site prompt per call.
def handler():  # Model the menu handler.
    prompt_helper()  # Make the first helper call.
    prompt_helper()  # Make the repeated helper call.
""",
        )

        prompts, _ = walker.prompts_for("handler")  # Walk both helper calls.

        assert prompts == ["site", "site"]  # Keep repeated prompts in the report.

    def test_prompt_calls_follow_source_order(self, tmp_path: Path) -> None:
        """Report prompts in the order the handler calls its helpers."""
        walker = _build_walker(
            tmp_path,
            """\
def site_helper():  # Model the first prompt helper.
    select_site()  # Record a site prompt.
def device_helper():  # Model the second prompt helper.
    select_device()  # Record a device prompt.
def handler():  # Model the menu handler.
    device_helper()  # Request the device prompt first.
    site_helper()  # Request the site prompt second.
""",
        )

        prompts, _ = walker.prompts_for("handler")  # Walk calls in source order.

        assert prompts == ["device", "site"]  # Match the order of the input stream.

    def test_recursive_helpers_stop_without_losing_prompts(self, tmp_path: Path) -> None:
        """Stop a recursive call cycle and still report its prompt."""
        walker = _build_walker(
            tmp_path,
            """\
def first_helper():  # Start the recursive call cycle.
    second_helper()  # Call the next helper.
def second_helper():  # Continue the recursive call cycle.
    first_helper()  # Return to the active helper.
    select_device()  # Keep the prompt after the recursive call.
def handler():  # Model the menu handler.
    first_helper()  # Start the walk.
""",
        )

        prompts, _ = walker.prompts_for("handler")  # Walk without recursive expansion.

        assert prompts == ["device"]  # Retain the reachable prompt.
