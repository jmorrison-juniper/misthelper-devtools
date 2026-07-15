"""CLI entrypoint for `python -m tools.test_quality_analyzer` (T041).

Implements `TestQualityCLI` per specs/1019-test-quality-analyzer/contracts/cli.md.

This is US1 scope only: full audit run with report + summary output. Gate mode
and baseline logic (--gate, --write-baseline) land in T046+.

Pipeline:

    1. Parse CLI flags via argparse.
    2. Load config via ConfigLoader (ConfigError -> exit 2).
    3. Discover test files via TestFileDiscoverer.
    4. Import all 5 detector modules to trigger DetectorRegistry registration.
    5. For each discovered file:
         - Parse AST (SyntaxError -> ParseError record, non-fatal).
         - Check MistApiExcluder (unless --include-mist-api). Excluded -> SkippedFile.
         - Feed the parsed tree to every non-Untested detector.
    6. Call UntestedDetector.analyze() over all parsed non-excluded test files.
    7. Filter findings by --disable-rule and apply severity overrides.
    8. Build the Report; write JSON + Markdown to the requested output paths.
    9. Print the one-line stdout summary.
   10. Return 0 on success; 2 on any engine error.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import argparse  # Stdlib CLI parser (contract mandates argparse).
import ast  # Parses each discovered test file into an AST.
import logging  # Structured logging per Constitution Principle VII.
import sys  # sys.exit / sys.stderr for the module-run form.
from collections.abc import Sequence  # Structural annotation for input sequences.
from datetime import UTC, datetime  # UTC timestamp for --generated_at fallback.
from pathlib import Path  # Path arithmetic for outputs and discovery.

from tools.test_quality_analyzer import __version__ as _ENGINE_VERSION  # Report envelope.
from tools.test_quality_analyzer.baseline import BaselineDiffer  # US2 baseline load/diff/write.
from tools.test_quality_analyzer.config import ConfigError, ConfigLoader  # Config loader.
from tools.test_quality_analyzer.detection import (
    DetectorRegistry,
    Finding,
    ParseError,
    SkippedFile,
)

# Import every detector module by reference so each one appends itself to DetectorRegistry on import.
# The tuple below both documents the registered detectors AND keeps the imports from being pruned by F401.
from tools.test_quality_analyzer.detection import missing_edge_case as _detector_missing_edge_case
from tools.test_quality_analyzer.detection import missing_failure_mode as _detector_missing_failure_mode
from tools.test_quality_analyzer.detection import tautological as _detector_tautological
from tools.test_quality_analyzer.detection import untested as _detector_untested
from tools.test_quality_analyzer.detection import weak_assertion as _detector_weak_assertion
from tools.test_quality_analyzer.detection.untested import UntestedDetector  # Cross-file detector.

# Reference all detector modules so their DetectorRegistry.append() side effects are guaranteed at import.
_REGISTERED_DETECTOR_MODULES = (
    _detector_missing_edge_case,
    _detector_missing_failure_mode,
    _detector_tautological,
    _detector_untested,
    _detector_weak_assertion,
)

from tools.test_quality_analyzer.discovery import (  # Discovery + Mist-API exclusion.
    MistApiExcluder,
    TestFileDiscoverer,
)
from tools.test_quality_analyzer.reporting import (  # Report + Markdown emitters.
    MarkdownRenderer,
    ReportBuilder,
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger (name matches the CLI).


class TestQualityCLI:
    """Test Quality Analyzer CLI orchestrator (T041 US1 scope)."""

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    def run(self, argv: Sequence[str] | None = None) -> int:
        """Execute a single analyzer run. Return the process exit code."""
        # Parse CLI arguments up-front; argparse handles --help by raising SystemExit.
        args = self._parse_args(argv)  # Namespace of parsed CLI arguments.
        # Configure logging as the very first side effect so subsequent code emits records.
        self._configure_logging(args.log_level)  # Wire stderr handler at the requested level.
        _LOGGER.info("test_quality_analyzer starting; engine_version=%s", _ENGINE_VERSION)
        # Wrap the pipeline so any exception downgrades to exit 2 with a stderr message.
        try:
            return self._run_pipeline(args)  # Full US1 pipeline.
        except ConfigError as exc:
            # ConfigError maps to exit 2 per FR-021 / contracts/cli.md.
            _LOGGER.error("Configuration error: %s", exc)
            sys.stderr.write("test_quality_analyzer: config error: %s\n" % exc)
            return 2  # Engine error exit code.
        except OSError as exc:
            # IO / filesystem errors also map to exit 2 per the CLI contract.
            _LOGGER.error("IO error during analyzer run: %s", exc)
            sys.stderr.write("test_quality_analyzer: IO error: %s\n" % exc)
            return 2  # Engine error exit code.

    # -----------------------------------------------------------------------
    # Argument parsing
    # -----------------------------------------------------------------------

    def _parse_args(self, argv: Sequence[str] | None) -> argparse.Namespace:
        """Build the argparse parser and return the parsed Namespace."""
        # Build the parser with the exact flag surface documented in contracts/cli.md.
        parser = argparse.ArgumentParser(  # Program-scoped parser.
            prog="test_quality_analyzer",  # Matches the module path for --help.
            description="Static-analysis auditor for the MistHelper test suite.",
        )
        # --roots: repeatable path list; default `tests/` per contract.
        parser.add_argument(
            "--roots",
            nargs="+",  # One or more paths accepted.
            default=["tests"],  # Contract default is tests/ (Path.resolve handled later).
            help="Test roots to analyze (default: tests).",
        )
        # --config: path to the TOML config file.
        parser.add_argument(
            "--config",
            default="tools/test_quality_analyzer/config.toml",
            help="Analyzer config TOML (default: tools/test_quality_analyzer/config.toml).",
        )
        # --baseline: path to baseline JSON, or empty string to disable (US2 wires this).
        parser.add_argument(
            "--baseline",
            default="tools/test_quality_analyzer/baseline.json",
            help='Baseline JSON path; "" disables baseline comparison (default enabled path).',
        )
        # --report: path to write the JSON report to.
        parser.add_argument(
            "--report",
            default="tools/test_quality_analyzer/output/report.json",
            help="Where to write the JSON report (default: output/report.json).",
        )
        # --summary: path to write the Markdown summary to.
        parser.add_argument(
            "--summary",
            default="tools/test_quality_analyzer/output/summary.md",
            help="Where to write the Markdown summary (default: output/summary.md).",
        )
        # --gate: US2 -- exit 1 on new findings vs baseline. Mutually exclusive with --write-baseline.
        parser.add_argument(
            "--gate",
            action="store_true",  # Enables gate exit-code semantics per contracts/cli.md.
            help="Gate mode -- exit 1 if any new finding vs baseline, 2 on parse errors.",
        )
        # --write-baseline: US2 -- overwrite baseline with current findings and exit 0.
        parser.add_argument(
            "--write-baseline",
            action="store_true",  # Overwrite baseline path per contracts/cli.md.
            help="Overwrite baseline with current findings and exit 0.",
        )
        # --disable-rule: repeatable rule id filter.
        parser.add_argument(
            "--disable-rule",
            action="append",  # Repeatable flag accumulates into a list.
            default=[],  # Empty default -- no rules disabled.
            metavar="RULE_ID",
            help="Runtime rule disable (may be repeated).",
        )
        # --include-mist-api: override the Mist-API exclusion for this run only.
        parser.add_argument(
            "--include-mist-api",
            action="store_true",  # Absent by default: predicate is active.
            help="Bypass Mist-API exclusion predicate for this run only.",
        )
        # --fixed-timestamp: freezes the report envelope timestamp for determinism tests.
        parser.add_argument(
            "--fixed-timestamp",
            default=None,  # Unset -> compute now(UTC) at report-build time.
            metavar="ISO8601",
            help="Fixed ISO-8601 UTC timestamp; used only by determinism tests.",
        )
        # --log-level: standard logging level name; forwarded to logging.getLevelName.
        parser.add_argument(
            "--log-level",
            default="INFO",  # Contract default.
            choices=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"),
            help="Logging level (default: INFO).",
        )
        # Return the parsed namespace; argparse handles --help internally.
        return parser.parse_args(argv)  # SystemExit for --help / parse errors.

    # -----------------------------------------------------------------------
    # Logging plumbing
    # -----------------------------------------------------------------------

    def _configure_logging(self, level_name: str) -> None:
        """Wire a stderr handler at the requested level; idempotent across runs."""
        # Resolve the level name defensively; argparse choices already validated the input.
        level = logging.getLevelName(level_name)  # int or -1 sentinel.
        # Root logger is what pytest capture and console output both attach to.
        root = logging.getLogger()  # Root logger governs both our namespace and dependencies.
        root.setLevel(level if isinstance(level, int) else logging.INFO)
        # Skip re-adding a handler on repeat invocations (unit-test invocations reuse process).
        if root.handlers:
            return  # Idempotent -- previous run wired handlers already.
        # Attach a plain stderr handler; stdout is reserved for the one-line summary.
        handler = logging.StreamHandler(stream=sys.stderr)  # Contract: logs go to stderr.
        handler.setFormatter(  # ASCII-only single-line log format.
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"),
        )
        root.addHandler(handler)  # Register once per process.

    # -----------------------------------------------------------------------
    # Pipeline
    # -----------------------------------------------------------------------

    def _run_pipeline(self, args: argparse.Namespace) -> int:
        """Full US1+US2 pipeline; returns exit code (0/1/2 per contracts/cli.md)."""
        # Validate mutually exclusive flag pair up-front; both-set is invalid CLI usage.
        if args.gate and args.write_baseline:
            sys.stderr.write(
                "test_quality_analyzer: --gate and --write-baseline are mutually exclusive\n",
            )
            return 2  # Invalid CLI usage per contract exit code 2.
        # 1. Load the config; ConfigError bubbles up to run() and maps to exit 2.
        _LOGGER.info("Loading config from %s", args.config)
        config_snapshot = ConfigLoader().load(Path(args.config))  # Immutable snapshot.
        # 2. Discover test files under the requested roots.
        roots = [Path(r) for r in args.roots]  # Roots as Path objects for discovery.
        _LOGGER.info("Discovering test files under %s", [str(r) for r in roots])
        test_files = TestFileDiscoverer().discover(roots)  # POSIX-normalized paths list.
        # 3. Parse every test file into an AST; segregate skipped vs analyzable.
        parsed_files, skipped, parse_errors = self._parse_and_partition(
            test_files=test_files,
            include_mist_api=args.include_mist_api,
            config_snapshot=config_snapshot,
        )
        _LOGGER.info(
            "Parsed %s file(s); %s skipped; %s parse error(s)",
            len(parsed_files),
            len(skipped),
            len(parse_errors),
        )
        # 4. Run per-file detectors (everything except UntestedDetector).
        findings = self._run_per_file_detectors(parsed_files)
        # 5. Run the cross-file UntestedDetector once against the parsed corpus and roots.
        findings.extend(self._run_untested_detector(parsed_files, roots))
        # 6. Apply --disable-rule filtering and config severity overrides.
        findings = self._apply_config_filters(
            findings,
            disabled_rules=list(args.disable_rule),
            config_snapshot=config_snapshot,
        )
        _LOGGER.info("Total findings after config filters: %s", len(findings))
        # 7. Resolve baseline path (empty string disables baseline logic entirely).
        baseline_arg = str(args.baseline) if args.baseline is not None else ""
        baseline_enabled = baseline_arg != ""  # Empty string disables per contract.
        baseline_path = Path(baseline_arg) if baseline_enabled else None
        # 8. --write-baseline short-circuit: write findings, emit summary, exit 0.
        if args.write_baseline:
            if not baseline_enabled:
                sys.stderr.write(
                    "test_quality_analyzer: --write-baseline requires --baseline path\n",
                )
                return 2  # Invalid usage.
            BaselineDiffer().write(baseline_path, findings)  # Overwrite baseline in place.
            _LOGGER.info("Wrote baseline (%s findings) to %s", len(findings), baseline_path)
            # Still emit the standard report/summary/stdout artifacts for auditability.
            report = self._build_report(
                findings=findings,
                skipped=skipped,
                parse_errors=parse_errors,
                stale_baseline_entries=(),
                config_snapshot=config_snapshot,
                args=args,
            )
            self._write_outputs(report=report, report_path=Path(args.report), summary_path=Path(args.summary))
            self._emit_stdout_summary(findings=findings, skipped=skipped, parse_errors=parse_errors)
            return 0  # --write-baseline always exits 0 per contract.
        # 9. Load baseline + compute diff/stale entries when enabled.
        diff = None  # BaselineDiff | None -- populated only when baseline is enabled.
        stale_entries: tuple[str, ...] = ()  # Absent-file advisory entries.
        if baseline_enabled:
            baseline = BaselineDiffer().load(baseline_path)  # Empty Baseline if file missing.
            diff = BaselineDiffer().diff(findings, baseline)  # Set-difference on canonical key.
            # Stale advisory cross-references baseline paths against scanned test paths.
            scanned_posix = {p.as_posix() for p, _t, _s in parsed_files}
            # Include skipped files too -- they were scanned even though excluded from detection.
            scanned_posix.update(s.file_path for s in skipped)
            stale_entries = BaselineDiffer().stale_entries(baseline, scanned_posix)
        # 10. Build the Report envelope with stale-baseline entries populated.
        report = self._build_report(
            findings=findings,
            skipped=skipped,
            parse_errors=parse_errors,
            stale_baseline_entries=stale_entries,
            config_snapshot=config_snapshot,
            args=args,
        )
        # 11. Serialize + write both artifacts.
        self._write_outputs(report=report, report_path=Path(args.report), summary_path=Path(args.summary))
        # 12. Print the one-line stdout summary per contracts/cli.md.
        self._emit_stdout_summary(findings=findings, skipped=skipped, parse_errors=parse_errors)
        # 13. Gate-mode exit logic (FR-018 + contracts/cli.md).
        if args.gate:
            # Parse errors are fatal in gate mode per FR-018.
            if parse_errors:
                sys.stderr.write(
                    "test_quality_analyzer: %d parse error(s) in gate mode\n" % len(parse_errors),
                )
                return 2  # Engine error exit code.
            # diff is only None when baseline was disabled; without a baseline, gate has no comparator.
            if diff is None:
                sys.stderr.write(
                    'test_quality_analyzer: --gate requires --baseline (got "")\n',
                )
                return 2  # Invalid usage in gate mode without a baseline.
            new_count = len(diff.new_findings)  # Number of unseen findings this run.
            sys.stdout.write("gate: %d new findings vs baseline\n" % new_count)
            _LOGGER.debug(
                "Gate result: new=%s removed=%s unchanged=%s",
                new_count,
                len(diff.removed_findings),
                diff.unchanged_count,
            )
            return 1 if new_count > 0 else 0  # Contract exit codes.
        # 14. Non-gate success exit.
        _LOGGER.debug("Analyzer run completed successfully")
        return 0  # Non-gate runs always exit 0 on a clean pipeline.

    def _build_report(
        self,
        findings,  # Sequence[Finding] filtered + severity-overridden.
        skipped,  # Sequence[SkippedFile] Mist-API exclusions.
        parse_errors,  # Sequence[ParseError] non-fatal parse failures.
        stale_baseline_entries: tuple[str, ...],  # Absent-file paths from baseline.
        config_snapshot,  # ConfigSnapshot for envelope.
        args: argparse.Namespace,  # For timestamp + scanned roots.
    ):
        """Assemble the Report envelope; factored out for gate + write-baseline paths."""
        return ReportBuilder().build(
            findings=findings,
            skipped=skipped,
            parse_errors=parse_errors,
            stale_baseline_entries=stale_baseline_entries,
            config_snapshot=config_snapshot,
            engine_version=_ENGINE_VERSION,
            generated_at=self._resolve_timestamp(args.fixed_timestamp),
            scanned_roots=[Path(r).as_posix() for r in args.roots],
        )

    # -----------------------------------------------------------------------
    # Parse + partition step
    # -----------------------------------------------------------------------

    def _parse_and_partition(
        self,
        test_files: Sequence[Path],  # Paths returned by TestFileDiscoverer.
        include_mist_api: bool,  # When True, disable the Mist-API exclusion predicate.
        config_snapshot,  # ConfigSnapshot -- forward-referenced to avoid import cycles.
    ) -> tuple[list[tuple[Path, ast.Module, str]], list[SkippedFile], list[ParseError]]:
        """Parse each file; return (analyzable triples, skipped records, parse errors)."""
        # Analyzable triples fed to detectors: (path, tree, source).
        parsed_files: list[tuple[Path, ast.Module, str]] = []  # Detector input list.
        # Skipped-file records included in the Report envelope.
        skipped: list[SkippedFile] = []  # Mist-API exclusions (unless overridden).
        # Non-fatal parse errors -- surfaced in the Report but do NOT halt the run.
        parse_errors: list[ParseError] = []  # SyntaxError records.
        # Build a MistApiExcluder once so we reuse a single instance per run.
        excluder = MistApiExcluder()  # Stateless; reusable across all files.
        # Iterate deterministically -- discover() already returned sorted results.
        for path in test_files:
            # Read the source text; OSError propagates to run() -> exit 2.
            source = path.read_text(encoding="utf-8")  # Full source (also used by detectors).
            # Parse to AST; capture SyntaxError as a non-fatal ParseError record.
            try:
                tree = ast.parse(source, filename=str(path))  # AST for detector consumption.
            except SyntaxError as exc:
                # ASCII-normalize the message text to keep JSON output ASCII-only.
                message = str(exc.msg) if exc.msg is not None else "syntax error"
                parse_errors.append(
                    ParseError(
                        file_path=path.as_posix(),  # POSIX-normalized path.
                        line_number=exc.lineno,  # May be None; ParseError tolerates it.
                        message=message,
                    ),
                )
                _LOGGER.warning("Skipping unparseable file %s: %s", path, exc)
                continue  # Move on -- the file cannot be analyzed further.
            # Mist-API exclusion check (unless the caller opted in via --include-mist-api).
            if not include_mist_api:
                skip = excluder.classify(
                    test_path=path,
                    tree=tree,
                    predicate=config_snapshot.mist_api_predicate,
                )
                if skip is not None:
                    skipped.append(skip)  # File is deliberately not analyzed.
                    continue  # Do not queue for per-file detectors.
            # File is analyzable: queue it for the detector loop.
            parsed_files.append((path, tree, source))
        return parsed_files, skipped, parse_errors

    # -----------------------------------------------------------------------
    # Detector orchestration
    # -----------------------------------------------------------------------

    def _run_per_file_detectors(
        self,
        parsed_files: Sequence[tuple[Path, ast.Module, str]],
    ) -> list[Finding]:
        """Run every registered detector EXCEPT UntestedDetector against each parsed file."""
        # Per-file findings accumulator returned to the pipeline.
        findings: list[Finding] = []  # Grows one detector-file pair at a time.
        # Build the reduced registry: skip UntestedDetector -- it runs cross-file below.
        per_file_detectors = [d for d in DetectorRegistry if not isinstance(d, UntestedDetector)]
        _LOGGER.info(
            "Running %s per-file detector(s) across %s file(s)",
            len(per_file_detectors),
            len(parsed_files),
        )
        # Iterate: for each parsed file, call detect() on every non-Untested detector.
        for path, tree, source in parsed_files:
            for detector in per_file_detectors:
                # Detectors may raise; surface as engine error rather than silent skip.
                findings.extend(detector.detect(path, tree, source))
        _LOGGER.debug("Per-file detector finding count: %s", len(findings))
        return findings

    def _run_untested_detector(
        self,
        parsed_files: Sequence[tuple[Path, ast.Module, str]],
        roots: Sequence[Path],
    ) -> list[Finding]:
        """Run UntestedDetector once against the full corpus (cross-file)."""
        # Construct a fresh detector with the CLI's --roots as source scan paths.
        # The registry's default instance has an empty source_paths and thus emits nothing.
        detector = UntestedDetector(source_paths=list(roots))  # Late-binding of source roots.
        _LOGGER.info(
            "Running UntestedDetector across %s source root(s) against %s test file(s)",
            len(roots),
            len(parsed_files),
        )
        # analyze() records refs from every parsed test then emits deferred findings.
        findings = detector.analyze(test_files=list(parsed_files))  # Cross-file diff.
        _LOGGER.debug("Untested finding count: %s", len(findings))
        return findings

    # -----------------------------------------------------------------------
    # Post-detection filters
    # -----------------------------------------------------------------------

    def _apply_config_filters(
        self,
        findings: Sequence[Finding],
        disabled_rules: Sequence[str],  # From --disable-rule (repeatable).
        config_snapshot,  # Forward-referenced ConfigSnapshot for severity overrides.
    ) -> list[Finding]:
        """Drop --disable-rule findings and apply severity overrides from the config."""
        # Freeze disabled rule ids into a set for O(1) membership tests.
        disabled_set = frozenset(disabled_rules)  # No-op when empty.
        # Load the severity override mapping from the immutable snapshot.
        severity_overrides = config_snapshot.severity_overrides  # rule_id -> Severity.
        # Rebuild the findings list applying both filters at once.
        filtered: list[Finding] = []  # Post-filter accumulator.
        for finding in findings:
            # Runtime disable check -- drop the whole record if the rule is disabled.
            if finding.rule_id in disabled_set:
                continue  # Filtered out per --disable-rule.
            # Apply severity override if present; otherwise keep the detector-supplied severity.
            new_severity = severity_overrides.get(finding.rule_id, finding.severity)
            if new_severity != finding.severity:
                # Rebuild the frozen dataclass with the overridden severity.
                finding = Finding(
                    category=finding.category,  # Unchanged.
                    rule_id=finding.rule_id,  # Unchanged.
                    severity=new_severity,  # Overridden per config.
                    file_path=finding.file_path,  # Unchanged.
                    line_number=finding.line_number,  # Unchanged.
                    explanation=finding.explanation,  # Unchanged.
                    remediation=finding.remediation,  # Unchanged.
                    heuristic=finding.heuristic,  # Unchanged.
                    related_source=finding.related_source,  # Unchanged.
                )
            filtered.append(finding)  # Retain the (possibly-overridden) finding.
        return filtered

    # -----------------------------------------------------------------------
    # Timestamp helper
    # -----------------------------------------------------------------------

    def _resolve_timestamp(self, fixed_timestamp: str | None) -> str:
        """Return the ISO-8601 UTC timestamp used in the report envelope."""
        # --fixed-timestamp wins outright when supplied (determinism test hook).
        if fixed_timestamp:
            return fixed_timestamp  # Trust the caller's ISO-8601 string.
        # Otherwise emit `now(UTC)` truncated to seconds precision, ISO-8601 with 'Z'.
        now = datetime.now(tz=UTC).replace(microsecond=0)  # Seconds precision.
        return now.isoformat().replace("+00:00", "Z")  # Canonical Z suffix per plan.md.

    # -----------------------------------------------------------------------
    # Output writers
    # -----------------------------------------------------------------------

    def _write_outputs(self, report, report_path: Path, summary_path: Path) -> None:
        """Serialize Report to JSON + Markdown and write both artifacts to disk."""
        # Ensure the parent directories exist so first-run writes succeed.
        report_path.parent.mkdir(parents=True, exist_ok=True)  # Idempotent mkdir.
        summary_path.parent.mkdir(parents=True, exist_ok=True)  # Idempotent mkdir.
        _LOGGER.info("Writing JSON report to %s", report_path)
        # Canonical JSON text (trailing newline) from ReportBuilder.to_json().
        json_text = ReportBuilder().to_json(report)  # Deterministic serialization.
        report_path.write_text(json_text, encoding="utf-8")  # Overwrite in place.
        _LOGGER.info("Writing Markdown summary to %s", summary_path)
        # ASCII-only Markdown rendered by MarkdownRenderer.render().
        md_text = MarkdownRenderer().render(report)  # Human-readable summary.
        summary_path.write_text(md_text, encoding="utf-8")  # Overwrite in place.
        _LOGGER.debug("Output artifacts written: %s + %s", report_path, summary_path)

    # -----------------------------------------------------------------------
    # stdout summary
    # -----------------------------------------------------------------------

    def _emit_stdout_summary(
        self,
        findings: Sequence[Finding],
        skipped: Sequence[SkippedFile],
        parse_errors: Sequence[ParseError],
    ) -> None:
        """Write the one-line stdout summary mandated by contracts/cli.md."""
        # Bucket findings by severity for the (c/h/m/l) breakdown.
        buckets = {"critical": 0, "high": 0, "medium": 0, "low": 0}  # Zero-initialized counts.
        for finding in findings:
            buckets[finding.severity.value] += 1  # Increment the relevant bucket.
        # Compose the summary line per the exact format in the CLI contract.
        line = "test_quality_analyzer: %d findings (%d/%d/%d/%d), %d skipped, %d parse errors" % (
            len(findings),  # Total findings count.
            buckets["critical"],  # C bucket.
            buckets["high"],  # H bucket.
            buckets["medium"],  # M bucket.
            buckets["low"],  # L bucket.
            len(skipped),  # Skipped-file count.
            len(parse_errors),  # Parse-error count.
        )
        sys.stdout.write(line + "\n")  # Trailing newline for POSIX cleanliness.


def main(argv: Sequence[str] | None = None) -> int:
    """Module-level entry point delegating to TestQualityCLI.run()."""
    # Single TestQualityCLI instance per invocation -- no shared state to worry about.
    return TestQualityCLI().run(argv)  # Delegate all logic to the class.


# Module-run form: `python -m tools.test_quality_analyzer`.
if __name__ == "__main__":  # Only executes when the module is run directly.
    sys.exit(main())  # Bubble the return code up as the process exit code.
