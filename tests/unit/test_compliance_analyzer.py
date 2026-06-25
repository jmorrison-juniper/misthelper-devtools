"""Unit tests for the compliance analyzer tool."""

from __future__ import annotations  # Enable modern annotation syntax.

import subprocess  # Initialize a throwaway git repo to exercise the ignore filter.
import sys  # Adjust the import path so the tools package is importable.
from pathlib import Path  # Build throwaway sample files and resolve the repo root.

import pytest  # MonkeyPatch fixture for changing the working directory in a test.

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # Add repo root to sys.path.

from tools.compliance_analyzer.engine import ComplianceAnalyzer  # System under test: engine.
from tools.compliance_analyzer.reporting import MarkdownReportGenerator  # Report renderer under test.
from tools.compliance_analyzer.scoring import ComplianceScorer  # Scorer under test.

# A deliberately non-compliant sample: a pass-through wrapper plus an alias.
WRAPPER_SOURCE = """
def real_add(first, second):
    return first + second


def add_wrapper(first, second):
    return real_add(first, second)


legacy_add = add_wrapper
"""

# A compliant sample: class-based, fully inline-commented, no indirection layers.
CLEAN_SOURCE = '''\
"""Sample compliant module used to validate the analyzer."""

from __future__ import annotations  # Enable modern annotations.


class Greeter:  # Encapsulate greeting behavior in a class.
    """Produce greetings for callers."""

    def greet(self, target_name: str) -> str:  # Build a greeting for the given name.
        greeting = "Hello, " + target_name  # Compose the greeting text.
        return greeting  # Return the finished greeting.
'''

# Python dunder forwarders: __call__/__getattr__ are the language's delegation protocol, not wrappers.
DUNDER_FORWARDER_SOURCE = """
class Handler:
    def __init__(self, impl):
        self._impl = impl

    def __call__(self, payload):
        return self._impl.handle(payload)

    def __getattr__(self, name):
        return getattr(self._impl, name)
"""

# A nested closure that forwards an argument to outer-scope state; closures are not architectural wrappers.
NESTED_CLOSURE_SOURCE = """
def build_runner(client):
    def run(request):
        return client.send(request)

    return run
"""

# A genuine class-level pass-through that MUST stay flagged so the exemptions do not over-broaden.
CLASS_DELEGATOR_SOURCE = """
class Service:
    def __init__(self, impl):
        self._impl = impl

    def fetch(self, site_id):
        return self._impl.fetch(site_id)
"""


def test_detects_wrapper_and_alias(tmp_path: Path) -> None:
    """The analyzer flags pass-through wrappers and module-level aliases."""
    target = tmp_path / "sample.py"  # Path for the throwaway bad sample.
    target.write_text(WRAPPER_SOURCE, encoding="utf-8")  # Write the non-compliant sample.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze the sample file.
    rule_ids = {violation.rule_id for violation in report.violations}  # Collect reported rule ids.
    assert "ARCH-DELEGATE" in rule_ids  # The pass-through wrapper must be flagged.
    assert "ARCH-ALIAS" in rule_ids  # The module-level alias must be flagged.
    assert report.score < 100.0  # Violations must lower the score.


def test_dunder_forwarders_not_flagged_as_delegation(tmp_path: Path) -> None:
    """Python dunder forwarders (__call__, __getattr__) must not be flagged as ARCH-DELEGATE."""
    target = tmp_path / "dunder.py"  # Path for the throwaway dunder sample.
    target.write_text(DUNDER_FORWARDER_SOURCE, encoding="utf-8")  # Write the dunder-forwarder sample.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze the sample file.
    delegations = [v for v in report.violations if v.rule_id == "ARCH-DELEGATE"]  # Collect delegation findings.
    assert delegations == []  # Dunders are the language's delegation protocol, never architectural wrappers.


def test_nested_closures_not_flagged_as_delegation(tmp_path: Path) -> None:
    """Nested closures forward outer-scope state by design and must not be flagged as ARCH-DELEGATE."""
    target = tmp_path / "closure.py"  # Path for the throwaway closure sample.
    target.write_text(NESTED_CLOSURE_SOURCE, encoding="utf-8")  # Write the nested-closure sample.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze the sample file.
    delegations = [v for v in report.violations if v.rule_id == "ARCH-DELEGATE"]  # Collect delegation findings.
    assert delegations == []  # Closures capture outer state; they are not standalone pass-through wrappers.


def test_class_level_delegator_still_flagged(tmp_path: Path) -> None:
    """A genuine class-level pass-through must STILL be flagged so the exemptions do not over-broaden."""
    target = tmp_path / "service.py"  # Path for the throwaway class-delegator sample.
    target.write_text(CLASS_DELEGATOR_SOURCE, encoding="utf-8")  # Write the class-level delegator sample.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze the sample file.
    delegations = [v for v in report.violations if v.rule_id == "ARCH-DELEGATE"]  # Collect delegation findings.
    assert len(delegations) == 1  # The real class-level delegator must remain flagged.
    assert delegations[0].symbol == "fetch"  # The forwarding method is the one reported.


def test_conv_path_distinguishes_drive_paths_from_regex(tmp_path: Path) -> None:
    """CONV-PATH flags real drive paths but not regex escapes like `:\\d` (issue #453)."""
    sample = (
        "import re\n"
        'real = "C:\\\\Users\\\\j\\\\data"\n'  # Genuine hardcoded Windows drive path -> must flag.
        'prompt = re.compile(r"{master:\\\\d+}")\n'  # Juniper prompt regex -> must NOT flag.
        'doc = re.compile(r"API doc:\\\\s*(https?://\\\\S+)")\n'  # Doc-link regex -> must NOT flag.
    )
    target = tmp_path / "paths.py"  # Throwaway sample file.
    target.write_text(sample, encoding="utf-8")  # Write the mixed sample.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze it.
    path_hits = [v for v in report.violations if v.rule_id == "CONV-PATH"]  # Collect CONV-PATH findings.
    assert len(path_hits) == 1, [v.line for v in path_hits]  # Only the genuine drive path is flagged.
    assert path_hits[0].line == 2  # Specifically the real "C:\\Users..." literal on line 2.


def test_clean_file_scores_well(tmp_path: Path) -> None:
    """A compliant file earns a high score and grade."""
    target = tmp_path / "clean.py"  # Path for the throwaway clean sample.
    target.write_text(CLEAN_SOURCE, encoding="utf-8")  # Write the compliant sample.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze the clean sample.
    assert report.score >= 90.0  # A compliant file should score highly.
    assert report.grade in {"A+", "A", "A-"}  # And earn a top-tier grade.


def test_report_contains_speckit_plan(tmp_path: Path) -> None:
    """The Markdown report includes an agent-ready SpecKit remediation plan."""
    target = tmp_path / "sample.py"  # Reuse the non-compliant sample.
    target.write_text(WRAPPER_SOURCE, encoding="utf-8")  # Write the sample to disk.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze the sample.
    markdown = MarkdownReportGenerator().generate([report])  # Render the Markdown report.
    assert "SpecKit Remediation Plan" in markdown  # The agent-ready plan must be present.
    assert "CMP-001" in markdown  # At least one numbered remediation task must appear.
    assert "Machine-Readable Summary" in markdown  # The JSON summary block must be present.


def test_scorer_grades_and_minimums() -> None:
    """The scorer maps scores to grades and compares minimum grades."""
    scorer = ComplianceScorer()  # Build a scorer instance.
    assert scorer.grade(95.0) == "A"  # A 95 score is an A grade.
    assert scorer.grade(59.0) == "F"  # A failing score is an F grade.
    assert scorer.meets_minimum("B", "C")  # A B grade satisfies a C minimum.
    assert not scorer.meets_minimum("D", "C")  # A D grade fails a C minimum.


def test_generated_and_vendored_paths_are_excluded(tmp_path: Path) -> None:
    """Generated/vendored trees are skipped by default so they do not inflate counts (issue #451)."""
    sample = "x = 1  # trivial module body.\n"  # Minimal valid Python for each throwaway file.
    # Files that MUST be excluded by default: generated protobuf + vendored skill scripts.
    excluded_rel = [
        "starlink-api-reference/device-api/device_pb2_grpc.py",  # Generated gRPC stub.
        ".agents/skills/caveman/scripts/compress.py",  # Vendored skill script.
        "data/skills/caveman/scripts/validate.py",  # Mirror of the vendored skill script.
    ]
    # A normal source file that MUST still be collected.
    included_rel = "src/example_module.py"  # Ordinary project source under src/.
    for rel in [*excluded_rel, included_rel]:  # Materialize every sample on disk.
        path = tmp_path / rel  # Resolve under the throwaway temp root.
        path.parent.mkdir(parents=True, exist_ok=True)  # Create intermediate dirs.
        path.write_text(sample, encoding="utf-8")  # Write the minimal module.
    reports = ComplianceAnalyzer().analyze_targets([str(tmp_path)], recursive=True)  # Scan the tree.
    collected = {Path(r.path).as_posix() for r in reports}  # Normalize collected paths for matching.
    assert any(included_rel in p for p in collected)  # The ordinary src/ file is analyzed.
    for rel in excluded_rel:  # None of the generated/vendored files may appear.
        assert not any(rel in p for p in collected), f"{rel} should have been excluded"  # Assert exclusion.


def test_git_ignored_files_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Files git ignores are skipped so scans match a clean checkout / CI (issue #454)."""
    sample = "x = 1  # trivial module body.\n"  # Minimal valid Python for each throwaway file.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # Real repo for check-ignore semantics.
    (tmp_path / ".gitignore").write_text("ignored_dir/\n", encoding="utf-8")  # Ignore one directory tree.
    ignored = tmp_path / "ignored_dir" / "dead.py"  # Untracked + ignored file that must be skipped.
    kept = tmp_path / "src" / "live.py"  # Ordinary tracked-eligible source that must be analyzed.
    for path in (ignored, kept):  # Materialize both samples on disk.
        path.parent.mkdir(parents=True, exist_ok=True)  # Create intermediate directories.
        path.write_text(sample, encoding="utf-8")  # Write the minimal module body.
    monkeypatch.chdir(tmp_path)  # Run from inside the repo so check-ignore resolves the local .gitignore.
    reports = ComplianceAnalyzer().analyze_targets(["."], recursive=True)  # Scan the throwaway tree.
    collected = {Path(r.path).as_posix() for r in reports}  # Normalize collected paths for matching.
    assert any("src/live.py" in p for p in collected)  # The non-ignored source file is analyzed.
    assert not any("dead.py" in p for p in collected)  # The git-ignored file is skipped entirely.
