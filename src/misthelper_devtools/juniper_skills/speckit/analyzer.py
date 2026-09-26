"""Cross-artifact analysis for generated SpecKit skill artifacts."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


@dataclass(frozen=True)
class AnalysisFinding:
    """One consistency finding from a SpecKit artifact check."""

    finding_id: str
    category: str
    severity: str
    location: str
    summary: str
    recommendation: str


class SpecKitAnalyzer:
    """Run a speckit.analyze-equivalent consistency check."""

    REQUIRED_FILES = (
        "spec.md",
        "clarifications.md",
        "plan.md",
        "tasks.md",
        "implementation.md",
        "analysis.md",
        str(Path("checklists") / "requirements.md"),
        ".spec-context.json",
    )
    COMMANDS = (
        "speckit.specify",
        "speckit.clarify",
        "speckit.plan",
        "speckit.tasks",
        "speckit.implement",
        "speckit.analyze",
        "speckit.checklist",
    )

    def analyze(self, feature_dir: Path, require_analysis: bool = True) -> list[AnalysisFinding]:
        """Return cross-artifact consistency findings."""
        logger.info("Analyzing generated SpecKit artifacts")  # Record validation start.
        texts = self._artifact_texts(feature_dir)  # Read artifacts that the analysis needs.
        findings = self._missing_file_findings(feature_dir, require_analysis)  # Find absent command artifacts first.
        findings.extend(self._coverage_findings(texts.get("spec.md", ""), texts.get("tasks.md", "")))
        findings.extend(self._placeholder_findings(*texts.values()))  # Find unresolved placeholders.
        findings.extend(self._command_findings(texts, require_analysis))  # Prove command records exist.
        findings.extend(self._metadata_findings(texts))  # Prove measured document values agree.
        logger.debug("Analysis produced %d findings", len(findings))  # Record the finding count.
        return findings

    def render_report(self, feature_dir: Path) -> str:
        """Return the analysis report Markdown."""
        logger.info("Rendering SpecKit analysis report")  # Record report rendering.
        findings = self.analyze(feature_dir, require_analysis=False)  # Check artifacts before writing this report.
        lines = self._report_header(findings)  # Build the fixed report table head.
        lines.extend(self._finding_lines(findings))  # Add one row per finding.
        lines.extend(self._metrics_lines(feature_dir, findings))  # Add coverage metrics for proof.
        logger.debug("Rendered analysis report with %d findings", len(findings))  # Record report result.
        return "\n".join(lines) + "\n"

    def _artifact_texts(self, feature_dir: Path) -> dict[str, str]:
        """Return readable artifact text keyed by required path name."""
        logger.info("Reading SpecKit artifacts for analysis")  # Record bulk artifact read.
        texts = {name: self._read_optional(feature_dir / name) for name in self.REQUIRED_FILES}
        logger.debug("Read %d SpecKit artifact text entries", len(texts))  # Record artifact count.
        return texts

    def _read_optional(self, path: Path) -> str:
        """Read an artifact when it exists."""
        logger.info("Reading optional SpecKit artifact %s", path)  # Record artifact read.
        text = path.read_text(encoding="utf-8") if path.exists() else ""  # Let missing-file checks report absence.
        logger.debug("Read %d characters from %s", len(text), path.name)  # Record safe file size.
        return text

    def _missing_file_findings(self, feature_dir: Path, require_analysis: bool) -> list[AnalysisFinding]:
        """Return findings for absent command artifacts."""
        logger.info("Checking required SpecKit command artifacts")  # Record required artifact check.
        required = (
            self.REQUIRED_FILES
            if require_analysis
            else tuple(name for name in self.REQUIRED_FILES if name != "analysis.md")
        )  # Permit report generation before analysis exists.
        missing = [name for name in required if not (feature_dir / name).exists()]  # Find missing paths.
        findings = [self._finding(index, "Missing Artifact", "CRITICAL", name) for index, name in enumerate(missing)]
        logger.debug("Found %d missing required artifacts", len(findings))  # Record missing artifact count.
        return findings

    def _coverage_findings(self, spec_text: str, tasks_text: str) -> list[AnalysisFinding]:
        """Return findings for requirements that no task references."""
        logger.info("Checking requirement coverage in tasks")  # Record coverage analysis start.
        requirements = re.findall(r"\*\*(FR-\d{3})\*\*", spec_text)  # Extract requirement identifiers.
        missing = [requirement for requirement in requirements if requirement not in tasks_text]  # Find gaps.
        findings = [self._missing_requirement(index, item) for index, item in enumerate(missing, start=1)]
        logger.debug("Found %d uncovered requirements", len(findings))  # Record coverage gap count.
        return findings

    def _placeholder_findings(self, *texts: str) -> list[AnalysisFinding]:
        """Return findings for unresolved SpecKit placeholders."""
        logger.info("Checking artifacts for unresolved placeholders")  # Record placeholder analysis start.
        joined = "\n".join(texts)  # Combine generated artifacts for a simple placeholder scan.
        patterns = ("[NEEDS CLARIFICATION", "TODO", "TKTK", "???", "[FEATURE]")  # Match placeholders.
        found = [pattern for pattern in patterns if pattern in joined]  # Keep only placeholders that remain.
        findings = [self._placeholder(index, item) for index, item in enumerate(found, start=1)]
        logger.debug("Found %d unresolved placeholder groups", len(findings))  # Record placeholder gap count.
        return findings

    def _command_findings(self, texts: dict[str, str], require_analysis: bool) -> list[AnalysisFinding]:
        """Return findings for missing command records."""
        logger.info("Checking the full SpecKit command sequence")  # Record sequence validation start.
        joined = "\n".join(texts.values())  # Search all generated artifacts for command evidence.
        commands = (
            self.COMMANDS if require_analysis else tuple(item for item in self.COMMANDS if item != "speckit.analyze")
        )  # Require analysis only after analysis.md exists.
        missing = [command for command in commands if command not in joined]  # Find skipped commands.
        findings = [
            self._finding(index, "Skipped Command", "CRITICAL", command) for index, command in enumerate(missing)
        ]
        logger.debug("Found %d missing SpecKit command records", len(findings))  # Record sequence gap count.
        return findings

    def _metadata_findings(self, texts: dict[str, str]) -> list[AnalysisFinding]:
        """Return findings for inconsistent measured values."""
        logger.info("Checking measured metadata across artifacts")  # Record metadata validation start.
        joined = "\n".join(texts.values())  # Combine artifacts so repeated metadata values can be counted.
        labels = ("Document title", "Page count", "Part count", "Topic count", "Guard result")  # Lock fields.
        missing = [label for label in labels if f"{label}:" not in joined]  # Find absent measured fields.
        findings = [self._finding(index, "Metadata Gap", "HIGH", label) for index, label in enumerate(missing)]
        logger.debug("Found %d measured metadata gaps", len(findings))  # Record metadata gap count.
        return findings

    def _missing_requirement(self, index: int, requirement: str) -> AnalysisFinding:
        """Return one missing requirement finding."""
        logger.info("Creating a coverage finding")  # Record finding creation.
        finding = AnalysisFinding(
            f"C{index}",
            "Coverage Gap",
            "CRITICAL",
            "tasks.md",
            f"Requirement {requirement} has no matching task.",
            f"Add a task that references {requirement}.",
        )
        logger.debug("Created coverage finding %s", finding.finding_id)  # Record the finding id.
        return finding

    def _placeholder(self, index: int, marker: str) -> AnalysisFinding:
        """Return one unresolved placeholder finding."""
        logger.info("Creating a placeholder finding")  # Record finding creation.
        finding = AnalysisFinding(
            f"P{index}",
            "Ambiguity",
            "HIGH",
            "generated artifacts",
            f"Placeholder marker {marker} remains in the artifact set.",
            "Replace the placeholder with concrete content.",
        )
        logger.debug("Created placeholder finding %s", finding.finding_id)  # Record the finding id.
        return finding

    def _finding(self, index: int, category: str, severity: str, value: str) -> AnalysisFinding:
        """Return one general analysis finding."""
        logger.info("Creating a general analysis finding")  # Record finding creation.
        summary = f"{value} is missing or inconsistent."  # State the artifact gap without source prose.
        finding = AnalysisFinding(f"G{index + 1}", category, severity, value, summary, "Regenerate the full workflow.")
        logger.debug("Created general finding %s", finding.finding_id)  # Record the finding id.
        return finding

    def _report_header(self, findings: list[AnalysisFinding]) -> list[str]:
        """Return the fixed analysis report header."""
        logger.info("Building the analysis report header")  # Record header rendering.
        status = "PASS" if not findings else "FAIL"  # Summarize whether install can proceed.
        lines = ["# Specification Analysis Report", "", "Command: speckit.analyze", "", f"Status: {status}", ""]
        lines.extend(
            ["| ID | Category | Severity | Location | Summary | Recommendation |", "| - | - | - | - | - | - |"]
        )
        logger.debug("Built analysis report header with status %s", status)  # Record the status.
        return lines

    def _finding_lines(self, findings: list[AnalysisFinding]) -> list[str]:
        """Return Markdown table rows for findings."""
        logger.info("Rendering analysis finding rows")  # Record finding row rendering.
        rows = [self._finding_line(finding) for finding in findings]  # Render all findings consistently.
        logger.debug("Rendered %d analysis finding rows", len(rows))  # Record row count.
        return rows or ["| None | None | None | None | No issue found. | No action required. |"]

    def _finding_line(self, finding: AnalysisFinding) -> str:
        """Return one Markdown finding row."""
        logger.info("Rendering one analysis finding row")  # Record single row rendering.
        row = (
            f"| {finding.finding_id} | {finding.category} | {finding.severity} | {finding.location} | "
            f"{finding.summary} | {finding.recommendation} |"
        )  # Keep the table row readable and deterministic.
        logger.debug("Rendered finding row %s", finding.finding_id)  # Record rendered finding id.
        return row

    def _metrics_lines(self, feature_dir: Path, findings: list[AnalysisFinding]) -> list[str]:
        """Return report metric lines."""
        logger.info("Rendering analysis metrics")  # Record metric rendering.
        spec_text = (feature_dir / "spec.md").read_text(encoding="utf-8") if (feature_dir / "spec.md").exists() else ""
        tasks_text = (
            (feature_dir / "tasks.md").read_text(encoding="utf-8") if (feature_dir / "tasks.md").exists() else ""
        )
        requirement_count = len(re.findall(r"\*\*FR-\d{3}\*\*", spec_text))  # Count explicit requirements.
        task_count = len(re.findall(r"^- \[[ xX]\] T\d{3}", tasks_text, re.MULTILINE))  # Count SpecKit tasks.
        logger.debug("Metrics found %d requirements and %d tasks", requirement_count, task_count)  # Record counts.
        return [
            "",
            "## Metrics",
            "",
            f"- Total Requirements: {requirement_count}",
            f"- Total Tasks: {task_count}",
            f"- Critical Issues Count: {self._critical_count(findings)}",
        ]

    def _critical_count(self, findings: list[AnalysisFinding]) -> int:
        """Return the number of critical findings."""
        logger.info("Counting critical analysis findings")  # Record severity count action.
        count = sum(1 for finding in findings if finding.severity == "CRITICAL")  # Count blocking findings.
        logger.debug("Counted %d critical findings", count)  # Record the critical count.
        return count
