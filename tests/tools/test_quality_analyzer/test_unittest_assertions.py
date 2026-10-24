"""Prove TestCase recognition without accepting arbitrary or deferred assertions."""

import ast
import json
from pathlib import Path

import pytest

from misthelper_devtools.test_quality_analyzer.__main__ import main
from misthelper_devtools.test_quality_analyzer.detection.weak_assertion import WeakAssertionDetector


def rules(source: str) -> list[str]:
    """Run the actual detector on a self-contained source example."""
    findings = WeakAssertionDetector().detect(Path("test_example.py"), ast.parse(source), source)
    return sorted(finding.rule_id for finding in findings)


@pytest.mark.parametrize(
    "assertion",
    [
        "self.assertEqual(result, 42)",
        "self.assertTrue(result)",
        "self.assertFalse(result)",
        "self.assertIn(42, result)",
        "self.assertIsInstance(result, dict)",
        "self.assertRaises(ValueError, operation)",
        "self.assertRaisesRegex(ValueError, 'invalid', operation)",
        "with self.assertRaises(ValueError):\n            operation()",
        "with self.assertRaisesRegex(ValueError, 'invalid'):\n            operation()",
    ],
)
def test_unittest_assertions_count(assertion: str) -> None:
    source = f"import unittest\nclass Checks(unittest.TestCase):\n    def test_result(self):\n        {assertion}\n"
    assert rules(source) == []


@pytest.mark.parametrize(
    "declarations, base",
    [
        ("import unittest as ut", "ut.TestCase"),
        ("from unittest import TestCase as Case", "Case"),
        ("from unittest.case import TestCase as Case", "Case"),
        ("import unittest.case as case", "case.TestCase"),
        ("import unittest\nclass Base(unittest.TestCase): pass", "Base"),
        ("import unittest\nclass Base(Middle): pass\nclass Middle(unittest.TestCase): pass", "Base"),
        ("import unittest", "unittest.IsolatedAsyncioTestCase"),
    ],
)
def test_aliases_and_local_inheritance(declarations: str, base: str) -> None:
    source = (
        f"{declarations}\nclass Checks({base}):\n" "    def test_result(this):\n        this.assertEqual(result, 42)\n"
    )
    assert rules(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "def test_result():\n    object.assertEqual(result, 42)",
        "class Checks:\n    def test_result(self):\n        self.assertEqual(result, 42)",
        "import unittest\nclass Checks(unittest.TestCase):\n"
        "    def test_result(self):\n        self.assertSomething(result)",
        "def test_result():\n    def helper():\n        assert result == 42",
        "def test_result():\n    class Helper:\n        def check(self):\n            assert result == 42",
        "def test_result():\n    async def helper():\n        assert result == 42",
        "def test_result():\n    helper = lambda: mock.assert_called_once_with(42)",
    ],
)
def test_unproved_assertions_do_not_hide_empty_test(source: str) -> None:
    assert rules(source) == ["weak_zero_assertions"]


def test_unittest_non_none_assertion_remains_weak() -> None:
    source = (
        "import unittest\nclass Checks(unittest.TestCase):\n"
        "    def test_result(self):\n        self.assertIsNotNone(result)"
    )
    assert rules(source) == ["weak_is_not_none"]


@pytest.mark.parametrize(
    "assertion",
    [
        "self.assertRaises(Exception, operation)",
        "self.assertRaisesRegex(Exception, 'error', operation)",
        "with self.assertRaises(Exception):\n            operation()",
        "with self.assertRaisesRegex(Exception, 'error'):\n            operation()",
        "self.assertRaises(BaseException, operation)",
        "self.assertRaises((ValueError, Exception), operation)",
        "self.assertRaises(builtins.Exception, operation)",
        "with self.assertRaises(expected_exception=Exception):\n            operation()",
    ],
)
def test_unittest_broad_exception_remains_weak(assertion: str) -> None:
    source = f"import unittest\nclass Checks(unittest.TestCase):\n    def test_result(self):\n        {assertion}\n"
    assert rules(source) == ["weak_pytest_raises_exception"]


@pytest.mark.parametrize("method", ["assert_not_called", "assert_not_awaited", "assert_awaited_once_with"])
def test_mock_assertions_count(method: str) -> None:
    assert rules(f"def test_result():\n    mock.{method}()") == []


def test_original_pytest_weak_assertions_still_fail() -> None:
    source = "import pytest\ndef test_result():\n    with pytest.raises(Exception):\n        operation()"
    assert rules(source) == ["weak_pytest_raises_exception"]


def test_unittest_test_without_assertion_still_fails() -> None:
    source = "import unittest\nclass Checks(unittest.TestCase):\n    def test_result(self):\n        operation()"
    assert rules(source) == ["weak_zero_assertions"]


def test_unused_exception_context_does_not_count() -> None:
    source = (
        "import unittest\nclass Checks(unittest.TestCase):\n"
        "    def test_result(self):\n        self.assertRaises(ValueError)"
    )
    assert rules(source) == ["weak_zero_assertions"]


def test_staticmethod_receiver_is_not_a_testcase() -> None:
    source = (
        "import unittest\nclass Checks(unittest.TestCase):\n"
        "    @staticmethod\n    def test_result(other):\n        other.assertEqual(result, 42)"
    )
    assert rules(source) == ["weak_zero_assertions"]


def test_positional_only_receiver_is_supported() -> None:
    source = (
        "import unittest\nclass Checks(unittest.TestCase):\n"
        "    def test_result(self, /):\n        self.assertEqual(result, 42)"
    )
    assert rules(source) == []


@pytest.mark.parametrize(
    "helper",
    [
        "def helper():\n            self.assertEqual(result, 42)",
        "def helper():\n            with self.assertRaises(ValueError):\n                operation()",
        "def helper():\n            mock.assert_not_called()",
        "class Helper:\n            def test_inner(self):\n                assert result == 42",
    ],
)
def test_nested_unittest_assertions_do_not_count(helper: str) -> None:
    """Keep deferred helper assertions separate from the outer test."""
    source = f"import unittest\nclass Checks(unittest.TestCase):\n    def test_result(self):\n        {helper}\n"
    assert rules(source) == ["weak_zero_assertions"]


def test_cli_reports_only_the_assertion_free_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise discovery and serialized findings with mixed unittest methods."""
    monkeypatch.chdir(tmp_path)
    test_path = tmp_path / "test_example.py"
    test_path.write_text(
        "import unittest\n"
        "class Checks(unittest.TestCase):\n"
        "    def test_strong(self):\n"
        "        self.assertEqual(42, 42)\n"
        "    def test_empty(self):\n"
        "        operation()\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    result = main(
        [
            "--roots",
            str(tmp_path),
            "--baseline",
            "",
            "--report",
            str(report_path),
            "--summary",
            str(tmp_path / "summary.md"),
        ]
    )
    assert result == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    findings = [finding for finding in report["findings"] if finding["category"] == "weak_assertion"]
    assert [(finding["rule_id"], finding["line_number"]) for finding in findings] == [("weak_zero_assertions", 5)]
    assert report["analyzed_files"] == ["test_example.py"]


def test_keyword_non_none_assertion_remains_weak() -> None:
    source = (
        "import unittest\nclass Checks(unittest.TestCase):\n"
        "    def test_result(self):\n        self.assertIsNotNone(obj=result)"
    )
    assert rules(source) == ["weak_is_not_none"]
