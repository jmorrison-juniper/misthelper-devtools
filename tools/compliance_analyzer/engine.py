"""Orchestration engine that analyzes files and produces scored reports."""

from __future__ import annotations  # Enable modern annotation syntax.

import ast  # Parsing source into an AST for the analyzers.
import io  # Wrap source text in a stream for the tokenizer.
import logging  # Structured action logging before and after each step.
import shutil  # PATH lookup that turns the partial name "git" into an absolute path.
import subprocess  # nosec B404 - The module queries git, and the call below uses shell=False.
import tokenize  # Token stream powers inline-comment coverage measurement.
from collections.abc import Iterable  # Type hint for the target collection.
from pathlib import Path  # Portable filesystem path handling.

from .analyzers import ArchitecturalAnalyzer, ConventionAnalyzer, StructuralComplexityAnalyzer
from .models import AnalysisContext, FileReport, Severity, Violation
from .scoring import ComplianceScorer

logger = logging.getLogger(__name__)  # Module-scoped logger for action logging.

# A held git index lock or a credential prompt blocks a git call with no bound.
# This cap turns that stall into a clear message instead of a six-hour CI job.
_GIT_TIMEOUT_SECONDS = 30


class _LogicalLineTracker:
    """Map a Python token stream to per-logical-line inline-comment coverage.

    A logical line (one statement, even when wrapped across several physical
    lines) counts as covered when any of its physical lines carries a comment.
    This avoids penalizing multi-line calls or signatures whose continuation
    lines cannot each hold a meaningful comment.
    """

    def __init__(self) -> None:
        """Initialize the result sets and the per-line working state."""
        self.code_lines: set[int] = set()  # Start rows of executable logical lines.
        self.inline_comment_lines: set[int] = set()  # Covered logical-line start rows.
        self._reset()  # Initialize the mutable per-line fields.

    def _reset(self) -> None:
        """Reset the working state between logical lines."""
        self._start: int | None = None  # First physical row of the current logical line.
        self._has_code = False  # Whether a non-string code token was seen.
        self._has_comment = False  # Whether a comment token was seen.
        self._exempt = True  # Whether the line is exempt from the comment rule.

    def feed(self, token: tokenize.TokenInfo) -> None:
        """Update the tracker with a single token."""
        token_type = token.type  # Cache the token type for branching.
        if token_type in (tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING):  # Layout-only tokens.
            return  # These tokens carry no coverage meaning.
        if token_type == tokenize.NEWLINE:  # End of a logical line with content.
            self._finish()  # Record the completed logical line.
            return  # Wait for the next logical line.
        if token_type == tokenize.NL:  # Blank line or continuation newline.
            if self._start is None:  # A standalone comment/blank line, not inside a statement.
                self._has_comment = False  # Do not leak its comment to the next line.
            return  # Continue accumulating the current logical line.
        if token_type == tokenize.COMMENT:  # A comment somewhere on the logical line.
            self._has_comment = True  # Mark the logical line as commented.
            return  # Keep accumulating.
        self._consume_code(token)  # Any other token is code content.

    def _consume_code(self, token: tokenize.TokenInfo) -> None:
        """Record a code-bearing token for the current logical line."""
        if self._start is None:  # This is the first token of a new logical line.
            self._start = token.start[0]  # Remember its starting physical row.
            self._exempt = self._is_exempt_line(token.line)  # Decide exemption from the first line.
        if token.type != tokenize.STRING:  # Strings (docstrings) are not executable code.
            self._has_code = True  # Mark that real code is present.

    def _finish(self) -> None:
        """Commit the current logical line to the result sets, then reset."""
        if self._start is not None and self._has_code and not self._exempt:  # Countable code line.
            self.code_lines.add(self._start)  # Record the logical-line start row.
            if self._has_comment:  # The logical line carried a comment somewhere.
                self.inline_comment_lines.add(self._start)  # Record it as covered.
        self._reset()  # Prepare for the next logical line.

    @staticmethod
    def _is_exempt_line(line_text: str) -> bool:
        """Return True for lines exempt from the inline-comment rule."""
        stripped = line_text.strip()  # Remove surrounding whitespace.
        if not stripped or stripped.startswith("#") or stripped.startswith("@"):  # Blank/comment/decorator.
            return True  # These lines never require an inline comment.
        if stripped in ("else:", "try:", "finally:"):  # Bare control keywords have nothing to comment.
            return True  # Treat structural keywords as exempt.
        return all(character in "()[]{}:,. " for character in stripped)  # Bracket-only lines are exempt.


class ComplianceAnalyzer:
    """Read Python files, run the analyzers, and score each file."""

    # Path fragments that are always skipped during directory scans.
    # Matching is performed against the POSIX form of each path (see _is_excluded), so the
    # tokens below use forward slashes. The Windows "tests\\fixtures" form is kept only for
    # backward compatibility with callers that pass pre-normalized Windows fragments.
    _DEFAULT_EXCLUDES = (
        ".venv",
        "site-packages",
        "__pycache__",
        ".git",
        "node_modules",
        "tests/fixtures",
        "tests\\fixtures",
        "starlink-api-reference",  # Third-party reference + generated gRPC protobuf (device_pb2*.py).
        ".agents/skills",  # Vendored Copilot skill scripts -- not project source.
        "data/skills",  # Mirror of the vendored skill scripts under the runtime data dir.
    )

    def __init__(
        self,
        analyzers: list[object] | None = None,
        scorer: ComplianceScorer | None = None,
    ) -> None:
        """Build the engine with default analyzers and scorer when none are given."""
        self._structural = StructuralComplexityAnalyzer()  # Reused for metrics and hotspots.
        default = [self._structural, ArchitecturalAnalyzer(), ConventionAnalyzer()]  # Standard analyzer set.
        self._analyzers = list(analyzers) if analyzers else default  # Allow custom analyzer injection.
        self._scorer = scorer or ComplianceScorer()  # Allow custom scorer injection.

    def analyze_targets(
        self,
        targets: Iterable[str],
        recursive: bool = False,
        excludes: list[str] | None = None,
    ) -> list[FileReport]:
        """Analyze every Python file under the given file/directory targets."""
        target_list = list(targets)  # Materialize the targets for logging and reuse.
        logger.info("Collecting Python files from %d target(s)", len(target_list))  # Log before collection.
        files = self._collect_files(target_list, recursive, excludes or [])  # Resolve target paths to files.
        logger.debug("Collected %d Python file(s) for analysis", len(files))  # Log the collection result.
        reports = [self.analyze_file(path) for path in files]  # Analyze each file in turn.
        logger.debug("Generated %d file report(s)", len(reports))  # Log the number of reports produced.
        return reports  # Return all per-file reports.

    def analyze_file(self, path: str | Path) -> FileReport:
        """Analyze a single Python file and return its scored report."""
        file_path = Path(path)  # Normalize the incoming path argument.
        logger.info("Analyzing file %s", file_path)  # Log before reading the file.
        source = file_path.read_text(encoding="utf-8", errors="replace")  # Read source, tolerating bad bytes.
        try:
            tree = ast.parse(source, filename=str(file_path))  # Parse the source into an AST.
        except SyntaxError as error:  # Unparseable files cannot be analyzed further.
            logger.error("Failed to parse %s: %s", file_path, error.msg)  # Log the parse failure.
            return self._parse_error_report(file_path, error)  # Return a failing report.
        context = self._build_context(file_path, source, tree)  # Assemble the shared analysis context.
        violations = self._run_analyzers(context)  # Gather violations from every analyzer.
        metrics = self._collect_metrics(context, tree)  # Compute aggregate file metrics.
        hotspots = self._collect_hotspots(tree)  # Identify the most complex functions.
        score = self._scorer.score(violations)  # Convert violations into a numeric score.
        grade = self._scorer.grade(score)  # Convert the score into a letter grade.
        logger.debug("File %s scored %.1f (%s) with %d issue(s)", file_path, score, grade, len(violations))
        return FileReport(str(file_path), violations, metrics, hotspots, score, grade)  # Assemble the report.

    def _run_analyzers(self, context: AnalysisContext) -> list[Violation]:
        """Run each configured analyzer and merge their violations."""
        violations: list[Violation] = []  # Collect violations from all analyzers.
        for analyzer in self._analyzers:  # Run analyzers in their configured order.
            violations.extend(analyzer.analyze(context))  # Append this analyzer's findings.
        return violations  # Return the merged list.

    def _build_context(self, path: Path, source: str, tree: ast.Module) -> AnalysisContext:
        """Assemble the reusable analysis context for a parsed file."""
        lines = source.splitlines()  # Split the source into physical lines.
        code_lines, inline_comment_lines = self._tokenize(source)  # Derive comment-coverage line sets.
        return AnalysisContext(str(path), source, lines, tree, code_lines, inline_comment_lines)  # Bundle it.

    def _tokenize(self, source: str) -> tuple[set[int], set[int]]:
        """Return (code_lines, inline_comment_lines) using logical-line coverage."""
        tracker = _LogicalLineTracker()  # State machine over the token stream.
        try:
            for token in tokenize.generate_tokens(io.StringIO(source).readline):  # Stream tokens lazily.
                tracker.feed(token)  # Advance the coverage state machine.
        except (tokenize.TokenError, IndentationError):  # Tolerate tokenizer edge cases gracefully.
            logger.debug("Tokenizer stopped early; comment coverage may be approximate")  # Note degradation.
        return tracker.code_lines, tracker.inline_comment_lines  # Return both line sets.

    def _collect_metrics(self, context: AnalysisContext, tree: ast.Module) -> dict[str, float]:
        """Compute aggregate numeric metrics for the analyzed file."""
        metrics = self._structural.complexity_metrics(tree)  # Function count and complexity averages.
        metrics["lines_of_code"] = float(len(context.lines))  # Total physical lines.
        metrics["code_lines"] = float(len(context.code_lines))  # Executable code lines.
        metrics["class_count"] = float(self._count_classes(tree))  # Number of classes in the file.
        metrics["inline_comment_coverage"] = self._coverage_percent(context)  # Inline-comment coverage percent.
        return metrics  # Return the metrics mapping.

    @staticmethod
    def _count_classes(tree: ast.Module) -> int:
        """Return the number of class definitions in the module."""
        return sum(1 for node in ast.walk(tree) if isinstance(node, ast.ClassDef))  # Count ClassDef nodes.

    @staticmethod
    def _coverage_percent(context: AnalysisContext) -> float:
        """Return inline-comment coverage as a 0-100 percentage."""
        if not context.code_lines:  # Avoid division by zero on empty files.
            return 100.0  # No executable code means trivially full coverage.
        commented = len(context.inline_comment_lines & context.code_lines)  # Commented executable lines.
        return round(commented / len(context.code_lines) * 100, 1)  # Percentage rounded to one decimal.

    def _collect_hotspots(self, tree: ast.Module) -> list[tuple[str, int]]:
        """Return the five most complex functions as (name, complexity) pairs."""
        complexities = self._structural.function_complexities(tree)  # Per-function complexity data.
        ranked = sorted(complexities, key=lambda entry: entry[1], reverse=True)  # Worst complexity first.
        return [(name, complexity) for name, complexity, _ in ranked[:5] if complexity >= 2]  # Top notable few.

    def _collect_files(self, targets: list[str], recursive: bool, excludes: list[str]) -> list[Path]:
        """Expand file/directory targets into a sorted list of Python files."""
        exclude_tokens = (*self._DEFAULT_EXCLUDES, *excludes)  # Combine default and user excludes.
        collected: list[Path] = []  # Accumulate matching Python files.
        for target in targets:  # Process each requested target.
            collected.extend(self._expand_target(Path(target), recursive, exclude_tokens))  # Expand it.
        return self._filter_git_ignored(collected)  # Drop git-ignored files so scans match a clean checkout.

    @staticmethod
    def _resolve_git_executable() -> str | None:
        """Return the absolute path of the git executable, or None when PATH holds no match."""
        logger.debug("Resolving the git executable on PATH")  # Log before the PATH lookup runs.
        git_path = shutil.which("git")  # An absolute path stops an earlier PATH entry supplying another program.
        logger.debug("Resolved the git executable to %s", git_path)  # Log the result of the PATH lookup.
        return git_path  # Hand the resolved path, or None, back to the caller.

    @staticmethod
    def _filter_git_ignored(files: list[Path]) -> list[Path]:
        """Drop files that git ignores so scans match a clean checkout / CI.

        Compliance applies to version-controlled source. Untracked, ignored
        trees (data dumps, build output, or packages accidentally shadowed by a
        broad ignore rule) do not exist in a fresh clone, so reporting on them
        yields phantom findings that nobody can fix via the repo. ``git
        check-ignore`` provides exact gitignore semantics and never reports
        tracked files; this degrades to a no-op when git is unavailable or the
        target is not inside a repository (fail open: scan everything).

        NUL-delimited (-z) binary I/O is used so Windows newline translation
        cannot corrupt paths in the stdin pipe and git never quotes output.
        """
        if not files:  # No collected files means nothing to filter.
            return files  # Preserve the identity result for empty inputs.
        git_path = ComplianceAnalyzer._resolve_git_executable()  # Absolute path, or None when git is absent.
        if git_path is None:  # PATH holds no git binary on this host.
            return files  # Fail open: never hide files when git cannot be consulted.
        payload = b"\0".join(path.as_posix().encode("utf-8") for path in files)  # NUL-delimited path list.
        try:
            completed = subprocess.run(  # nosec B603 - shutil.which resolved the path and the rest are literals.
                [git_path, "check-ignore", "-z", "--stdin"],  # -z: NUL-delimited in and out, no quoting.
                input=payload,  # Feed the collected file list as raw bytes.
                capture_output=True,  # Capture the ignored-path bytes from stdout.
                check=False,  # Exit 1 (none ignored) is normal, not an error.
                timeout=_GIT_TIMEOUT_SECONDS,  # A held index lock must not stall the gate forever.
            )
        except subprocess.TimeoutExpired:  # git held the pipe past the bound.
            # Name the bound, so the operator can tell a stall from a crash.
            print(f"compliance_analyzer: git check-ignore passed {_GIT_TIMEOUT_SECONDS}s and was stopped")
            return files  # Fail open: never hide files when git cannot be consulted.
        except (OSError, ValueError):  # git binary missing or stdin write failure.
            return files  # Fail open: never hide files when git cannot be consulted.
        if completed.returncode not in (0, 1):  # 128 => not a git repo; other codes => unknown failure.
            return files  # Fail open on fatal git errors (e.g., scanning outside a repo).
        ignored = {  # Decode each NUL-delimited ignored path back into a comparable string.
            chunk.decode("utf-8") for chunk in completed.stdout.split(b"\0") if chunk
        }
        if not ignored:  # Fast path when git reports nothing ignored.
            return files  # Return the original ordering unchanged.
        return [path for path in files if path.as_posix() not in ignored]  # Keep only non-ignored files.

    def _expand_target(self, target: Path, recursive: bool, exclude_tokens: tuple[str, ...]) -> list[Path]:
        """Expand one target path into the Python files it contributes."""
        if target.is_dir():  # Directories expand to their contained Python files.
            pattern = "**/*.py" if recursive else "*.py"  # Recurse only when requested.
            matches = sorted(target.glob(pattern))  # Deterministically ordered matches.
            return [match for match in matches if not self._is_excluded(match, exclude_tokens)]  # Filtered.
        if target.is_file() and target.suffix == ".py":  # A direct Python file target.
            return [] if self._is_excluded(target, exclude_tokens) else [target]  # Honor excludes.
        logger.warning("Skipping non-Python or missing target: %s", target)  # Note skipped targets.
        return []  # Nothing to contribute from this target.

    @staticmethod
    def _is_excluded(path: Path, exclude_tokens: tuple[str, ...]) -> bool:
        """Return True when a path matches any exclude token."""
        text = path.as_posix()  # Normalize separators for substring matching.
        return any(token in text for token in exclude_tokens)  # Exclude on any token match.

    @staticmethod
    def _parse_error_report(path: Path, error: SyntaxError) -> FileReport:
        """Build a failing report for a file that could not be parsed."""
        violation = Violation(
            rule_id="PARSE-ERROR",  # Stable rule identifier.
            category="Structure",  # Parse failures are structural blockers.
            severity=Severity.CRITICAL,  # An unparseable file is a critical issue.
            line=error.lineno or 1,  # Report the failing line when known.
            symbol="<module>",  # The whole module failed to parse.
            message=f"File could not be parsed: {error.msg}.",  # Surface the parser message.
            remediation="Fix the syntax error so the file can be analyzed and graded.",  # Required fix.
        )
        return FileReport(str(path), [violation], {"lines_of_code": 0.0}, [], 0.0, "F", parse_error=str(error))
