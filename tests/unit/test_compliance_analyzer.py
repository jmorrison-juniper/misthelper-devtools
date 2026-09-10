"""Unit tests for the compliance analyzer tool."""

from __future__ import annotations  # Enable modern annotation syntax.

import subprocess  # Initialize a throwaway git repo to exercise the ignore filter.
import sys  # Adjust the import path so the tools package is importable.
from pathlib import Path  # Build throwaway sample files and resolve the repo root.

import pytest  # MonkeyPatch fixture for changing the working directory in a test.

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # Add repo root to sys.path.

from tools.compliance_analyzer import engine  # Module under test for patched parallel constants.
from tools.compliance_analyzer.__main__ import ComplianceCLI  # CLI under test for argument forwarding.
from tools.compliance_analyzer.engine import ComplianceAnalyzer  # System under test: engine.
from tools.compliance_analyzer.models import FileReport  # Report type for test helper annotations.
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


def _write_parallel_fixture(root: Path) -> list[Path]:
    """Create a deterministic multi-file tree for parallel analyzer tests."""
    sources = {  # Each file gives the analyzer a different but stable report.
        "a_clean.py": CLEAN_SOURCE,  # Clean file proves high-score parity.
        "b_wrapper.py": WRAPPER_SOURCE,  # Non-compliant file proves violation parity.
        "nested/c_dunder.py": DUNDER_FORWARDER_SOURCE,  # Nested path proves ordering parity.
    }
    written: list[Path] = []  # Keep the exact paths for order assertions.
    for relative_path, source in sources.items():  # Materialize each sample file.
        path = root / relative_path  # Resolve the fixture path inside the temp tree.
        path.parent.mkdir(parents=True, exist_ok=True)  # Create nested directories before writing.
        path.write_text(source, encoding="utf-8")  # Write deterministic Python source.
        written.append(path)  # Record the path for expected-order checks.
    return sorted(written)  # Match the analyzer's sorted collection order.


def test_parallel_jobs_match_sequential_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parallel jobs must preserve every report field and the report order."""
    expected_files = _write_parallel_fixture(tmp_path)  # Build the shared fixture tree.
    monkeypatch.setattr(engine, "_PARALLEL_FILE_FLOOR", 1)  # Force the small fixture through the pool.
    sequential = ComplianceAnalyzer().analyze_targets([str(tmp_path)], recursive=True, jobs=1)  # Baseline path.
    parallel = ComplianceAnalyzer().analyze_targets([str(tmp_path)], recursive=True, jobs=2)  # Worker path.
    assert parallel == sequential  # Report values must match exactly.
    assert [Path(report.path) for report in parallel] == expected_files  # Output order must match collection order.


def test_parallel_jobs_keep_parse_error_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parallel jobs must convert syntax errors into the same report as sequential jobs."""
    target = tmp_path / "broken.py"  # Single parse-error fixture path.
    clean = tmp_path / "clean.py"  # Second file keeps the worker count above one.
    target.write_text("def broken(:\n", encoding="utf-8")  # Write invalid Python to exercise parse handling.
    clean.write_text(CLEAN_SOURCE, encoding="utf-8")  # Write valid Python beside the syntax error.
    monkeypatch.setattr(engine, "_PARALLEL_FILE_FLOOR", 1)  # Force the worker path for one file.
    sequential = ComplianceAnalyzer().analyze_targets([str(tmp_path)], recursive=True, jobs=1)  # Baseline reports.
    parallel = ComplianceAnalyzer().analyze_targets([str(tmp_path)], recursive=True, jobs=2)  # Worker reports.
    assert parallel == sequential  # Syntax errors must not change the report contract.
    assert parallel[0].violations[0].rule_id == "PARSE-ERROR"  # The stable parse rule must remain visible.


def test_small_inputs_stay_sequential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Small scans must avoid worker startup because measurement showed spawn overhead."""
    target = tmp_path / "small.py"  # Single-file scan that stays below the floor.
    target.write_text(CLEAN_SOURCE, encoding="utf-8")  # Write a valid file for the scan.

    def fail_parallel(files: list[Path], worker_count: int) -> list[FileReport]:
        raise AssertionError("parallel path should not run for a small input")  # Guard the fallback decision.

    monkeypatch.setattr(engine, "_PARALLEL_FILE_FLOOR", 200)  # Keep the fixture below the measured floor.
    monkeypatch.setattr(ComplianceAnalyzer, "_analyze_files_parallel", staticmethod(fail_parallel))
    reports = ComplianceAnalyzer().analyze_targets([str(target)], jobs=8)  # Request workers on a small input.
    assert len(reports) == 1  # The sequential fallback still returns the file report.


def test_missing_collected_file_raises_same_error_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing collected file must raise the same error type in both execution modes."""
    missing = tmp_path / "missing.py"  # This path is collected by patch but never created.
    missing_again = tmp_path / "missing_again.py"  # Second missing file forces a two-worker pool.
    monkeypatch.setattr(engine, "_PARALLEL_FILE_FLOOR", 1)  # Force pool use for the parallel run.
    monkeypatch.setattr(
        ComplianceAnalyzer,
        "_collect_files",
        lambda self, targets, recursive, excludes: [missing, missing_again],
    )
    with pytest.raises(FileNotFoundError):  # Sequential path raises from Path.read_text.
        ComplianceAnalyzer().analyze_targets([str(tmp_path)], jobs=1)  # Run the baseline path.
    with pytest.raises(FileNotFoundError):  # Parallel path must preserve the exception type.
        ComplianceAnalyzer().analyze_targets([str(tmp_path)], jobs=2)  # Run the worker path.


def test_negative_jobs_are_rejected(tmp_path: Path) -> None:
    """Negative worker counts must fail before any analysis starts."""
    target = tmp_path / "clean.py"  # Valid file path for the rejected request.
    target.write_text(CLEAN_SOURCE, encoding="utf-8")  # Write valid source so only jobs validation fails.
    with pytest.raises(ValueError, match="jobs must be zero or a positive integer"):  # Assert explicit error.
        ComplianceAnalyzer().analyze_targets([str(target)], jobs=-1)  # Negative jobs are invalid input.


def test_explicit_jobs_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit worker requests must respect the measured cap."""
    monkeypatch.setattr(engine, "_PARALLEL_FILE_FLOOR", 1)  # Put worker resolution on the parallel path.
    monkeypatch.setattr(engine, "_PARALLEL_AUTO_WORKER_LIMIT", 8)  # Use the measured automatic cap.
    monkeypatch.setattr(engine.os, "process_cpu_count", lambda: 32)  # Simulate the measured host capacity.
    assert ComplianceAnalyzer._resolve_worker_count(5000, 1391) == 8  # A large request must not over-spawn.


def test_cli_passes_jobs_to_analyzer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The CLI must forward `--jobs` to the analyzer."""
    output = tmp_path / "report.md"  # Report path keeps the test output isolated.
    captured: dict[str, int] = {}  # Capture the worker count passed by the CLI.

    def fake_analyze(
        self: object,
        targets: object,
        recursive: bool = False,
        excludes: object = None,
        jobs: int = 1,
    ) -> list[FileReport]:
        captured["jobs"] = jobs  # Record the forwarded CLI value.
        return [ComplianceAnalyzer().analyze_file(tmp_path / "clean.py")]  # Return a real report for rendering.

    (tmp_path / "clean.py").write_text(CLEAN_SOURCE, encoding="utf-8")  # Provide the fake analyzer report input.
    monkeypatch.setattr(ComplianceAnalyzer, "analyze_targets", fake_analyze)  # Isolate the CLI argument path.
    exit_code = ComplianceCLI().run([str(tmp_path / "clean.py"), "--jobs", "3", "-o", str(output), "-q"])
    assert exit_code == 0  # The CLI should complete normally with the fake report.
    assert captured["jobs"] == 3  # The parsed worker count must reach the engine.


def test_cli_reports_negative_jobs_without_traceback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI must report a negative worker count as a usage error."""
    target = tmp_path / "clean.py"  # Valid file keeps the failure focused on argument validation.
    target.write_text(CLEAN_SOURCE, encoding="utf-8")  # Write valid source for the rejected scan.
    exit_code = ComplianceCLI().run([str(target), "--jobs", "-1", "-q"])  # Run the bad argument path.
    captured = capsys.readouterr()  # Capture console output to verify no traceback appears.
    assert exit_code == 2  # Negative jobs must use the CLI usage-error code.
    assert "Traceback" not in captured.err  # The CLI must not expose a Python traceback.


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


# Names whose indirection token is only a SUBSTRING of a legitimate domain word must not be flagged.
ARCH_NAMING_SOURCE = '''\
"""Sample module exercising ARCH-NAMING whole-word matching."""


class Probe:  # Container for the sampled method names.
    """Methods whose names mix legitimate domain words and real indirection tokens."""

    def verify_ssr_compatibility(self) -> bool:  # 'compat' is only a substring of 'compatibility'.
        return True  # Genuine domain method; must NOT be flagged.

    def handle_existing_non_misthelper(self) -> bool:  # 'helper' is part of the product name.
        return True  # Product-name method; must NOT be flagged.

    def legacy_fallback(self) -> bool:  # 'legacy' is a whole word -> genuine indirection token.
        return True  # Real legacy indirection; must be flagged.
'''


def test_arch_naming_matches_whole_words_not_substrings(tmp_path: Path) -> None:
    """ARCH-NAMING flags whole-word tokens (legacy) but not substrings (compat in compatibility) (issue #455)."""
    target = tmp_path / "probe.py"  # Throwaway sample file.
    target.write_text(ARCH_NAMING_SOURCE, encoding="utf-8")  # Write the mixed-name sample.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze it.
    flagged = {v.symbol for v in report.violations if v.rule_id == "ARCH-NAMING"}  # Collect flagged names.
    assert "verify_ssr_compatibility" not in flagged  # 'compat' substring of 'compatibility' is not a smell.
    assert "handle_existing_non_misthelper" not in flagged  # 'helper' inside 'misthelper' is the product name.
    assert "legacy_fallback" in flagged  # 'legacy' is a whole word -> genuine indirection token.


# A method call on a literal receiver is a computation, not delegation to a collaborator object.
ARCH_DELEGATE_LITERAL_SOURCE = '''\
"""Sample exercising ARCH-DELEGATE literal-receiver exemption."""


class Probe:  # Container for the sampled methods.
    """Methods that look like single forwarding calls."""

    def __init__(self, impl: object) -> None:  # Store a collaborator for the genuine delegate below.
        self._impl = impl  # Collaborator object referenced by the real delegate.

    def icon_for_type(self, item_type: str) -> tuple:  # {literal}.get(x) is a self-contained lookup.
        return {"module": (">", "cyan")}.get(item_type, ("-", "dim"))  # Computation; must NOT be flagged.

    def fetch(self, site_id: str) -> object:  # Forwards to a collaborator -> genuine delegation.
        return self._impl.fetch(site_id)  # Pass-through to a collaborator; must be flagged.
'''


def test_arch_delegate_ignores_literal_receiver_calls(tmp_path: Path) -> None:
    """ARCH-DELEGATE ignores method calls on literals ({...}.get) but flags collaborator delegation (issue #455)."""
    target = tmp_path / "probe.py"  # Throwaway sample file.
    target.write_text(ARCH_DELEGATE_LITERAL_SOURCE, encoding="utf-8")  # Write the mixed sample.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze it.
    flagged = {v.symbol for v in report.violations if v.rule_id == "ARCH-DELEGATE"}  # Collect delegation findings.
    assert "icon_for_type" not in flagged  # Lookup on a dict literal is a computation, not delegation.
    assert "fetch" in flagged  # Forwarding to a collaborator object remains a flagged pass-through.


# Forwarding a parameter into a logging/print sink is an output operation, not collaborator delegation.
ARCH_DELEGATE_OUTPUT_SINK_SOURCE = '''\
"""Sample exercising ARCH-DELEGATE output-sink exemption."""

import logging


class Probe:  # Container for the sampled methods.
    """Methods whose single call writes output or forwards to a collaborator."""

    def __init__(self, impl: object) -> None:  # Store a collaborator for the genuine delegate below.
        self._impl = impl  # Collaborator object referenced by the real delegate.

    def log_loop_stop(self, iteration: int) -> None:  # logging.info(...) is an output operation.
        logging.info("Capture loop stopped after %s iterations", iteration)  # Output; must NOT be flagged.

    def announce(self, message: str) -> None:  # print(...) is an output operation.
        print(message)  # Output; must NOT be flagged.

    def fetch(self, site_id: str) -> object:  # Forwards to a collaborator -> genuine delegation.
        return self._impl.fetch(site_id)  # Pass-through to a collaborator; must be flagged.
'''


def test_arch_delegate_ignores_output_sink_calls(tmp_path: Path) -> None:
    """ARCH-DELEGATE ignores logging/print sinks but still flags collaborator delegation (issue #455)."""
    target = tmp_path / "probe.py"  # Throwaway sample file.
    target.write_text(ARCH_DELEGATE_OUTPUT_SINK_SOURCE, encoding="utf-8")  # Write the mixed sample.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze it.
    flagged = {v.symbol for v in report.violations if v.rule_id == "ARCH-DELEGATE"}  # Collect delegation findings.
    assert "log_loop_stop" not in flagged  # logging.info(...) is output, not delegation.
    assert "announce" not in flagged  # print(...) is output, not delegation.
    assert "fetch" in flagged  # Forwarding to a collaborator object remains a flagged pass-through.
