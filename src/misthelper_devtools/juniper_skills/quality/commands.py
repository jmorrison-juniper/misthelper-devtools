"""Command fence cleanup for generated Juniper topics."""

from __future__ import annotations  # Keep annotations cheap during quality imports.

import logging  # Record cleanup counts for validation reports.
import re  # Detect command syntax and prose punctuation.
from pathlib import Path  # Use platform-safe paths for built-store scans.

from .models import CommandFenceCleanReport  # Return cleaned text and proof counts.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class CommandFenceCleaner:
    """Remove prose lines that leaked into CLI code fences."""

    COMMAND_WORDS = {"set", "show", "delete", "edit", "commit", "request", "clear", "run", "monitor"}  # Junos verbs.
    SENTENCE_MARKS = {"!", "?"}  # Sentence punctuation is not valid in a command sample.

    def clean_text(self, text: str) -> CommandFenceCleanReport:
        """Run the clean text operation."""
        logger.info("Cleaning command fences in one Markdown text")  # Log before parsing Markdown fences.
        lines = text.splitlines()  # Preserve line order for deterministic output.
        output: list[str] = []  # Build cleaned Markdown lines.
        removed: list[str] = []  # Store bounded examples for a report.
        in_fence = False  # Track whether the current line is inside a fenced block.
        for line in lines:  # Inspect every Markdown line once.
            in_fence = self._copy_line(line, output, removed, in_fence)  # Copy or reject the line by state.
        report = CommandFenceCleanReport("\n".join(output), 1, len(removed), tuple(removed[:10]))  # Build evidence.
        logger.debug("Removed %s prose lines from command fences", report.lines_removed)  # Report cleanup count.
        return report  # Return cleaned text and examples.

    def clean_paths(self, paths: tuple[Path, ...], write: bool = False) -> CommandFenceCleanReport:
        """Run the clean paths operation."""
        logger.info("Cleaning command fences in %s Markdown files", len(paths))  # Log before store scan.
        total_removed = 0  # Count all removed lines across the store.
        examples: list[str] = []  # Keep a bounded example list.
        for path in paths:  # Process each file independently so one bad file is visible.
            report = self.clean_text(path.read_text(encoding="utf-8"))  # Clean one file and collect metrics.
            total_removed += report.lines_removed  # Add the file count to the store count.
            examples.extend(report.removed_examples)  # Preserve early examples for the report.
            self._write_clean(path, report, write)  # Persist only when the caller requests a mutation.
        logger.debug("Removed %s prose lines from %s files", total_removed, len(paths))  # Report store totals.
        return CommandFenceCleanReport("", len(paths), total_removed, tuple(examples[:10]))  # Return aggregate proof.

    def _copy_line(self, line: str, output: list[str], removed: list[str], in_fence: bool) -> bool:
        if line.strip().startswith("```"):  # Fence boundaries always remain in the file.
            output.append(line)  # Preserve the Markdown fence marker.
            return not in_fence  # Toggle fence state after copying the marker.
        if in_fence and self._is_prose_leak(line):  # Reject prose only inside code fences.
            removed.append(line.strip())  # Store the removed text for evidence.
            return in_fence  # Stay inside the current fence.
        output.append(line)  # Keep valid command, output, and prose outside fences.
        return in_fence  # Preserve the current fence state.

    def _is_prose_leak(self, line: str) -> bool:
        stripped = line.strip()  # Normalize edge whitespace for classification only.
        if not stripped:  # Empty lines are valid separators inside command output.
            return False  # Keep blank lines in command output.
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", stripped)  # Count prose-like words for sentence signals.
        first = words[0].lower() if words else ""  # Read the first word as a possible Junos command.
        checks = [
            self._has_sentence_punctuation(stripped),
            self._has_help_title(first, words),
            self._long_without_command(first, words),
        ]
        return any(checks)  # Reject the line when any prose signal is strong.

    def _has_sentence_punctuation(self, line: str) -> bool:
        if any(mark in line for mark in self.SENTENCE_MARKS):  # Commands do not contain sentence punctuation.
            return True  # Reject question and exclamation sentences.
        if line.endswith("."):  # A final period is sentence punctuation, not Junos command syntax.
            return True  # Reject command-looking lines that received prose punctuation.
        return bool(re.search(r"[A-Za-z][.]($|\s)", line))  # Reject sentence periods after word tokens.

    def _has_help_title(self, first: str, words: list[str]) -> bool:
        second_is_title = len(words) > 1 and words[1][:1].isupper()  # CLI help descriptions use a title word.
        return first in self.COMMAND_WORDS and second_is_title  # Reject help output such as request Make.

    def _long_without_command(self, first: str, words: list[str]) -> bool:
        return len(words) > 8 and first not in self.COMMAND_WORDS  # Long non-command lines are explanatory prose.

    def _write_clean(self, path: Path, report: CommandFenceCleanReport, write: bool) -> None:
        if write and report.lines_removed:  # Avoid touching files that did not change.
            path.write_text(report.text + "\n", encoding="utf-8")  # Persist cleaned Markdown with final newline.
