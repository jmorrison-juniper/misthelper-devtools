"""Render scored file reports into an AI-agent-ready Markdown document."""

from __future__ import annotations  # Enable modern annotation syntax.

import json  # Emit a machine-readable summary block for downstream agents.
from datetime import UTC, datetime  # Timestamp the report in UTC.

from .models import FileReport, Severity, Violation  # Report record/enum types.
from .scoring import ComplianceScorer  # Reused to grade the overall score.


class MarkdownReportGenerator:  # Renders scored file reports as Markdown.
    """Build a Markdown compliance report including a SpecKit remediation plan."""

    # Severity processing order from most to least serious.
    _SEVERITY_ORDER = (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW)  # Worst-first order.

    # Cap remediation tasks so very large scans produce a usable document.
    _MAX_TASKS = 300  # Hard cap on tasks rendered in the plan.

    def __init__(self, scorer: ComplianceScorer | None = None) -> None:  # Optional custom scorer injection.
        """Create the generator with an optional custom scorer."""
        self._scorer = scorer or ComplianceScorer()  # Used for the overall grade.

    def generate(self, reports: list[FileReport]) -> str:  # Entry point that assembles the whole report.
        """Render the full Markdown report for a list of file reports."""
        lines: list[str] = []  # Accumulate output lines.
        lines.extend(self._header(reports))  # Title and metadata block.
        lines.extend(self._summary(reports))  # Overall score and per-file table.
        lines.extend(self._machine_summary(reports))  # JSON summary for agents.
        for report in reports:  # Render a section per analyzed file.
            lines.extend(self._file_section(report))  # File metrics and violations.
        lines.extend(self._speckit_plan(reports))  # Agent-ready remediation plan.
        return "\n".join(lines) + "\n"  # Join with newlines and a trailing newline.

    def _header(self, reports: list[FileReport]) -> list[str]:  # Title/metadata lines.
        """Return the report title and metadata lines."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")  # Current UTC timestamp.
        return [
            "# Coding Guideline Compliance Report",  # Document title.
            "",  # Spacer.
            f"- **Generated**: {timestamp}",  # When the report was produced.
            "- **Tool**: compliance-analyzer (tools/compliance_analyzer)",  # Producing tool.
            f"- **Files analyzed**: {len(reports)}",  # Number of files scanned.
            "",  # Spacer.
            "Files are graded against the project guidelines: the 5-Item Rule, no",  # Scope line 1.
            "wrappers/delegators/aliases/shims, complexity limits, inline comments,",  # Scope line 2.
            "safe input handling, and portable file paths. Use the SpecKit Remediation",  # Scope line 3.
            "Plan at the end to drive fixes.",  # Scope line 4.
            "",  # Spacer.
        ]

    def _summary(self, reports: list[FileReport]) -> list[str]:  # Aggregate score + per-file table.
        """Return the overall summary and the per-file results table."""
        overall = self.overall_score(reports)  # Mean score across files.
        grade = self._scorer.grade(overall)  # Overall letter grade.
        lines = [
            "## Summary",  # Section heading.
            "",  # Spacer.
            f"- **Overall score**: {overall:.1f} / 100",  # Aggregate score.
            f"- **Overall grade**: {grade}",  # Aggregate grade.
            "",  # Spacer.
            "| File | Score | Grade | Critical | High | Medium | Low | Total |",  # Table header.
            "| - | - | - | - | - | - | - | - |",  # Table divider.
        ]
        for report in reports:  # Add one row per analyzed file.
            lines.append(self._summary_row(report))  # Render the file's summary row.
        lines.append("")  # Trailing spacer.
        return lines  # Return the summary block.

    def _summary_row(self, report: FileReport) -> str:  # One row in the per-file summary table.
        """Return a single Markdown summary-table row for one file."""
        counts = self._counts(report.violations)  # Severity counts for this file.
        return (
            f"| {self._md(report.path)} | {report.score:.1f} | {report.grade} | "  # Path, score, grade.
            f"{counts[Severity.CRITICAL]} | {counts[Severity.HIGH]} | "  # Critical and high counts.
            f"{counts[Severity.MEDIUM]} | {counts[Severity.LOW]} | {len(report.violations)} |"  # Rest.
        )

    def _machine_summary(self, reports: list[FileReport]) -> list[str]:  # JSON block for downstream tooling.
        """Return a fenced JSON block summarizing results for tooling/agents."""
        payload = {
            "overall_score": round(self.overall_score(reports), 1),  # Aggregate score.
            "overall_grade": self._scorer.grade(self.overall_score(reports)),  # Aggregate grade.
            "severity_totals": self._severity_totals(reports),  # Totals per severity.
            "rule_totals": self._rule_totals(reports),  # Totals per rule id.
            "files": self._file_payloads(reports),  # Compact per-file records.
        }
        return ["## Machine-Readable Summary", "", "```json", json.dumps(payload, indent=2), "```", ""]  # Fenced.

    def _file_payloads(self, reports: list[FileReport]) -> list[dict[str, object]]:  # JSON per-file records.
        """Return compact per-file dictionaries for the JSON summary."""
        return [
            {
                "path": report.path,  # File path.
                "score": round(report.score, 1),  # Numeric score.
                "grade": report.grade,  # Letter grade.
                "violations": len(report.violations),  # Total violation count.
            }
            for report in reports  # One entry per analyzed file.
        ]

    def _file_section(self, report: FileReport) -> list[str]:  # Full section for one analyzed file.
        """Return the full Markdown section for one analyzed file."""
        lines = [
            f"## File: {self._md(report.path)}",  # File heading.
            "",  # Spacer.
            f"- **Score**: {report.score:.1f} / 100",  # File score.
            f"- **Grade**: {report.grade}",  # File grade.
            "",  # Spacer.
        ]
        lines.extend(self._metrics_table(report.metrics))  # Metrics sub-table.
        lines.extend(self._hotspots_table(report.hotspots))  # Complexity hotspots sub-table.
        if not report.violations:  # Clean files get a short confirmation.
            lines.extend(["No violations found. This file complies with the guidelines.", ""])  # Note.
            return lines  # Nothing further to render.
        lines.extend(self._violation_tables(report))  # Detailed violation tables.
        return lines  # Return the assembled section.

    def _metrics_table(self, metrics: dict[str, float]) -> list[str]:
        """Return a Markdown table of the file's numeric metrics."""
        rows = [
            ("Lines of code", str(int(metrics.get("lines_of_code", 0)))),  # Physical line count.
            ("Executable code lines", str(int(metrics.get("code_lines", 0)))),  # Commentable lines.
            ("Functions", str(int(metrics.get("function_count", 0)))),  # Function count.
            ("Classes", str(int(metrics.get("class_count", 0)))),  # Class count.
            ("Average complexity", f"{metrics.get('avg_complexity', 0):.1f}"),  # Mean complexity.
            ("Max complexity", str(int(metrics.get("max_complexity", 0)))),  # Peak complexity.
            ("Inline comment coverage", f"{metrics.get('inline_comment_coverage', 0)}%"),  # Comment coverage.
        ]
        lines = ["### Metrics", "", "| Metric | Value |", "| - | - |"]  # Table scaffold.
        lines.extend(f"| {label} | {value} |" for label, value in rows)  # One row per metric.
        lines.append("")  # Trailing spacer.
        return lines  # Return the metrics table.

    def _hotspots_table(self, hotspots: list[tuple[str, int]]) -> list[str]:
        """Return a Markdown table of the most complex functions, if any."""
        if not hotspots:  # Skip the table when there are no notable hotspots.
            return []  # Nothing to render.
        lines = ["### Complexity Hotspots", "", "| Function | Cyclomatic Complexity |", "| - | - |"]  # Scaffold.
        lines.extend(f"| {self._md(name)} | {complexity} |" for name, complexity in hotspots)  # Rows.
        lines.append("")  # Trailing spacer.
        return lines  # Return the hotspots table.

    def _violation_tables(self, report: FileReport) -> list[str]:
        """Return per-category Markdown tables of the file's violations."""
        lines = ["### Violations", ""]  # Section heading.
        grouped: dict[str, list[Violation]] = {}  # Group violations by category.
        for violation in report.violations:  # Bucket every violation.
            grouped.setdefault(violation.category, []).append(violation)  # Append to its category.
        for category in sorted(grouped):  # Render categories in stable order.
            lines.append(f"#### {category}")  # Category heading.
            lines.append("")  # Spacer.
            lines.append("| Line | Severity | Rule | Symbol | Issue | Remediation |")  # Table header.
            lines.append("| - | - | - | - | - | - |")  # Table divider.
            lines.extend(self._violation_row(violation) for violation in self._sorted(grouped[category]))
            lines.append("")  # Trailing spacer per category.
        return lines  # Return all violation tables.

    def _violation_row(self, violation: Violation) -> str:
        """Return a single Markdown row describing one violation."""
        return (
            f"| {violation.line} | {violation.severity.value} | {violation.rule_id} | "  # Line/severity/rule.
            f"{self._md(violation.symbol)} | {self._md(violation.message)} | "  # Symbol and message.
            f"{self._md(violation.remediation)} |"  # Remediation guidance.
        )

    def _speckit_plan(self, reports: list[FileReport]) -> list[str]:
        """Return the agent-ready, phased SpecKit remediation plan."""
        lines = [
            "## SpecKit Remediation Plan",  # Section heading.
            "",  # Spacer.
            "> AI agent: convert each phase below into a SpecKit workflow. For a phase,",  # Guidance 1.
            "> run `speckit.specify` with the phase goal, then `speckit.plan`,",  # Guidance 2.
            "> `speckit.tasks`, and `speckit.implement`. Re-run this analyzer to verify",  # Guidance 3.
            "> every task is resolved before closing the phase.",  # Guidance 4.
            "",  # Spacer.
        ]
        all_tasks = self._collect_tasks(reports)  # Flatten all violations into tasks.
        if not all_tasks:  # Nothing to fix means a fully compliant scan.
            lines.append("No remediation tasks: every analyzed file complies with the guidelines.")  # Note.
            lines.append("")  # Trailing spacer.
            return lines  # Return the short plan.
        lines.extend(self._plan_body(all_tasks))  # Render the phased task list.
        return lines  # Return the full plan.

    def _plan_body(self, all_tasks: list[tuple[str, Violation]]) -> list[str]:
        """Render the truncation note and severity-prioritized task blocks."""
        lines: list[str] = []  # Accumulate plan body lines.
        tasks = self._prioritize_tasks(all_tasks)  # Sorted + capped worst-first.
        lines.extend(self._truncation_note(all_tasks))  # Note when tasks were truncated.
        counter = 1  # Sequential task numbering across phases.
        for severity in self._SEVERITY_ORDER:  # Emit phases from critical to low.
            group = [task for task in tasks if task[1].severity == severity]  # Tasks for this severity.
            if not group:  # Skip empty phases.
                continue  # Move to the next severity.
            block, counter = self._phase_lines(severity, group, counter)  # Render the phase block.
            lines.extend(block)  # Append the phase block.
        return lines  # Return the assembled plan body.

    def _prioritize_tasks(self, all_tasks: list[tuple[str, Violation]]) -> list[tuple[str, Violation]]:
        """Return tasks sorted worst-severity-first and capped at ``_MAX_TASKS``."""
        # WHY: extracted so _plan_body drops from CC 7 to <=5.
        order = {severity: index for index, severity in enumerate(self._SEVERITY_ORDER)}  # Severity ranking.
        prioritized = sorted(all_tasks, key=lambda task: order[task[1].severity])  # Worst severity first.
        return prioritized[: self._MAX_TASKS]  # Cap so oversized scans stay usable.

    def _truncation_note(self, all_tasks: list[tuple[str, Violation]]) -> list[str]:
        """Return the truncation-note lines when tasks exceed ``_MAX_TASKS``, else empty."""
        # WHY: extracted so _plan_body drops from CC 7 to <=5.
        if len(all_tasks) <= self._MAX_TASKS:  # Guard clause: no truncation needed.
            return []  # Nothing to note.
        return [
            f"> Note: showing the {self._MAX_TASKS} highest-severity of {len(all_tasks)} tasks.",  # Note line.
            "",  # Trailing spacer.
        ]

    def _phase_lines(
        self,
        severity: Severity,
        group: list[tuple[str, Violation]],
        start_counter: int,
    ) -> tuple[list[str], int]:
        """Render one severity phase and return its lines and the next counter."""
        lines = [f"### Phase: {severity.value.title()} ({len(group)} task(s))", ""]  # Phase heading.
        counter = start_counter  # Resume numbering from the caller.
        for path, violation in sorted(group, key=lambda item: (item[0], item[1].line)):  # Stable order.
            lines.extend(self._task_lines(counter, path, violation))  # Render this task.
            counter += 1  # Advance the task counter.
        lines.append("")  # Trailing spacer.
        return lines, counter  # Return the block and the updated counter.

    def _task_lines(self, counter: int, path: str, violation: Violation) -> list[str]:
        """Return the Markdown checklist lines for a single remediation task."""
        return [
            f"- [ ] **CMP-{counter:03d}** `{path}:{violation.line}` - {violation.rule_id} ({violation.category})",
            f"  - Symbol: `{violation.symbol}`",  # Affected symbol.
            f"  - Problem: {violation.message}",  # What is wrong.
            f"  - Fix: {violation.remediation}",  # How to fix it.
            f"  - Done when: analyzer reports no {violation.rule_id} for `{violation.symbol}` in `{path}`.",
        ]

    def _collect_tasks(self, reports: list[FileReport]) -> list[tuple[str, Violation]]:
        """Flatten every violation into a (path, violation) task tuple."""
        tasks: list[tuple[str, Violation]] = []  # Accumulate tasks across files.
        for report in reports:  # Walk every file report.
            for violation in report.violations:  # Walk every violation in the file.
                tasks.append((report.path, violation))  # Record the task tuple.
        return tasks  # Return all tasks.

    def overall_score(self, reports: list[FileReport]) -> float:
        """Return the mean score across reports, or 100 when empty."""
        if not reports:  # No files means nothing to penalize.
            return 100.0  # Treat an empty scan as fully compliant.
        return sum(report.score for report in reports) / len(reports)  # Mean of file scores.

    def _severity_totals(self, reports: list[FileReport]) -> dict[str, int]:
        """Return total violation counts per severity across all files."""
        totals = {severity.value: 0 for severity in self._SEVERITY_ORDER}  # Seed every severity at zero.
        for report in reports:  # Aggregate across all files.
            for violation in report.violations:  # Count each violation once.
                totals[violation.severity.value] += 1  # Increment its severity bucket.
        return totals  # Return the severity totals.

    @staticmethod
    def _rule_totals(reports: list[FileReport]) -> dict[str, int]:
        """Return total violation counts per rule id across all files."""
        totals: dict[str, int] = {}  # Accumulate counts keyed by rule id.
        for report in reports:  # Aggregate across all files.
            for violation in report.violations:  # Count each violation once.
                totals[violation.rule_id] = totals.get(violation.rule_id, 0) + 1  # Increment the rule bucket.
        return dict(sorted(totals.items()))  # Return rule totals in stable key order.

    @staticmethod
    def _counts(violations: list[Violation]) -> dict[Severity, int]:
        """Return a severity-to-count mapping for a list of violations."""
        counts = {severity: 0 for severity in Severity}  # Seed every severity at zero.
        for violation in violations:  # Tally each violation.
            counts[violation.severity] += 1  # Increment its severity count.
        return counts  # Return the severity counts.

    def _sorted(self, violations: list[Violation]) -> list[Violation]:
        """Return violations sorted by severity (worst first) then line number."""
        order = {severity: index for index, severity in enumerate(self._SEVERITY_ORDER)}  # Severity ranking.
        return sorted(violations, key=lambda violation: (order[violation.severity], violation.line))  # Sort.

    @staticmethod
    def _md(text: str) -> str:
        """Escape Markdown table-breaking characters in free text."""
        return text.replace("|", "\\|").replace("\n", " ")  # Neutralize pipes and newlines.
