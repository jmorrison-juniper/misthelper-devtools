"""Detect Junos command text that the converter left outside code fences."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


@dataclass(frozen=True)
class CommandDetectionResult:
    """The command detection output and its measured counts."""

    text: str  # Store the source text with detected command blocks fenced.
    fenced_blocks: int  # Count the new fences that this detector inserted.
    command_lines: int  # Count nonblank command or output lines inside new fences.


class CommandBlockDetector:
    """Find Junos commands, configuration, and output, then fence them verbatim."""

    COMMAND_VERBS = {
        "clear",
        "commit",
        "configure",
        "delete",
        "edit",
        "help",
        "monitor",
        "ping",
        "request",
        "run",
        "set",
        "show",
        "traceroute",
    }  # Keep only Junos CLI verbs.
    HIERARCHY_TOKENS = {
        "chassis",
        "class-of-service",
        "firewall",
        "forwarding-options",
        "interfaces",
        "policy-options",
        "protocols",
        "routing-options",
        "security",
        "system",
    }  # Match Junos hierarchy roots.

    def __init__(self) -> None:
        """Initialize the CommandBlockDetector instance."""
        logger.info("Compiling command detection patterns")  # Log setup before regular expressions are built.
        self.prompt_pattern = re.compile(r"^[\w.-]+@[\w.-]+[>#]\s*.*$")  # Detect operational and config prompts.
        self.interface_pattern = re.compile(
            r"^(?:ae|et|ge|irb|lo0|xe)-?\d*(?:/\d+/\d+)?(?:\.\d+)?\b"
        )  # Detect port names.
        self.table_pattern = re.compile(r"^\S+(?:\s{2,}|\s+\S+\s+\S+)\S*")  # Detect terse table rows.
        logger.debug("Compiled %s command detection patterns", 3)  # Report setup completion.

    def refence_text(self, text: str) -> CommandDetectionResult:
        """Run the refence text operation."""
        logger.info("Detecting unfenced Junos command blocks")  # Log command detection before scanning text.
        lines = text.splitlines()  # Preserve exact line text while scanning logical lines.
        output: list[str] = []  # Accumulate the repaired Markdown in source order.
        index = 0  # Track the current source line position for deterministic scanning.
        blocks = 0  # Count new code fences for the proof report.
        command_lines = 0  # Count nonblank lines moved into code fences.
        while index < len(lines):  # Scan until every source line is classified.
            index, block_count, line_count = self._copy_next(lines, index, output)  # Copy prose or one command block.
            blocks += block_count  # Add one when the copied unit was a command block.
            command_lines += line_count  # Add only nonblank command and output lines.
        result = CommandDetectionResult("\n".join(output), blocks, command_lines)  # Return text and proof metrics.
        logger.debug("Command detection inserted %s fences for %s lines", blocks, command_lines)  # Report results.
        return result

    def _copy_next(self, lines: list[str], index: int, output: list[str]) -> tuple[int, int, int]:
        line = lines[index]  # Read one line without changing its content.
        if line.startswith("```"):  # Respect existing fences so the detector does not nest code blocks.
            return self._copy_existing_fence(lines, index, output)  # Copy the existing block unchanged.
        if self._starts_command_block(line):  # Start a new fence only when the line has a command signal.
            block, next_index = self._collect_block(lines, index)  # Collect the command and immediate output lines.
            output.extend(["```text", *block, "```"])  # Fence the exact source lines as literal text.
            line_count = sum(1 for value in block if value.strip())  # Count command lines for the report.
            return next_index, 1, line_count  # Tell the caller that one new block was emitted.
        output.append(line)  # Keep a prose line outside a code fence.
        return index + 1, 0, 0  # Tell the caller that no command block was emitted.

    def _copy_existing_fence(self, lines: list[str], index: int, output: list[str]) -> tuple[int, int, int]:
        output.append(lines[index])  # Copy the opening fence exactly as the source wrote it.
        index += 1  # Move to the first line inside the existing fence.
        while index < len(lines):  # Copy until the existing closing fence appears.
            output.append(lines[index])  # Preserve existing fenced content verbatim.
            index += 1  # Advance after each copied fenced line.
            if output[-1].startswith("```"):  # Stop after the closing fence line is copied.
                break
        return index, 0, 0  # Existing fences do not count as detector output.

    def _collect_block(self, lines: list[str], index: int) -> tuple[list[str], int]:
        block = [lines[index]]  # Start the block with the detected command line.
        index += 1  # Move to the line that can hold command output.
        while index < len(lines):  # Continue while the next source line belongs to the command block.
            line = lines[index]  # Inspect the next line without changing it.
            if self._stops_block(line):  # Stop before headings, page markers, and fences.
                break
            if line.strip() or self._blank_between_output(lines, index):  # Keep useful output and separator blanks.
                block.append(line)  # Preserve the command or output line exactly.
                index += 1  # Advance after adding the line to the block.
                continue
            break  # Stop at a blank line that is not inside output.
        return block, index  # Return the verbatim block and the first prose line index.

    def _starts_command_block(self, line: str) -> bool:
        stripped = line.strip()  # Normalize only for detection, not for output.
        if not stripped:  # Empty lines cannot start a command block.
            return False
        checks = [
            self._has_prompt(stripped),
            self._has_cli_verb(stripped),
            self._has_hierarchy(stripped),
        ]  # Test signals.
        return any(checks) or self._has_interface_row(stripped)  # Accept the line when any strong signal exists.

    def _continues_command_block(self, line: str) -> bool:
        stripped = line.strip()  # Normalize only for detection, not for output.
        if not stripped:  # A blank can continue only when the caller sees output after it.
            return False
        checks = [
            self._starts_command_block(stripped),
            self._looks_like_output(stripped),
        ]  # Test command output signals.
        return any(checks)  # Continue the block when the next line looks like output.

    def _stops_block(self, line: str) -> bool:
        stripped = line.strip()  # Normalize structural markers for detection.
        markers = [stripped.startswith("``"), stripped.startswith("<!-- page "), stripped.startswith("##")]  # Stops.
        return any(markers)  # Never fence a Markdown control line with command output.

    def _blank_between_output(self, lines: list[str], index: int) -> bool:
        if lines[index].strip():  # This helper only answers for blank lines.
            return False
        next_index = self._next_nonblank(lines, index + 1)  # Find the next real line after the blank.
        return next_index is not None and self._continues_command_block(lines[next_index])  # Keep blank before output.

    def _next_nonblank(self, lines: list[str], index: int) -> int | None:
        while index < len(lines):  # Walk forward until a real line appears.
            if lines[index].strip():  # Return the first line that contains text.
                return index
            index += 1  # Skip a blank separator line.
        return None  # Tell the caller that no more text exists.

    def _has_prompt(self, line: str) -> bool:
        return bool(self.prompt_pattern.match(line))  # Prompts are the strongest command signal.

    def _has_cli_verb(self, line: str) -> bool:
        parts = line.split()  # Read the first two tokens to avoid false prose matches.
        if not parts or parts[0] not in self.COMMAND_VERBS:  # Reject non-command verbs early.
            return False
        if len(parts) == 1:  # Single commands such as commit are valid Junos commands.
            return True
        if parts[1][:1].isupper():  # Reject title rows such as "set Set CLI properties".
            return False
        return parts[1].islower() or any(token in line for token in ["|", "/", "{", "}", "<"])  # Accept CLI syntax.

    def _has_hierarchy(self, line: str) -> bool:
        first = line.split(maxsplit=1)[0] if line.split() else ""  # Read the first token for hierarchy matching.
        rest = line.split(maxsplit=1)[1] if len(line.split()) > 1 else ""  # Read child syntax after the root token.
        checks = ["{" in line, ";" in line, bool(self.interface_pattern.match(rest))]  # Require config syntax.
        return first in self.HIERARCHY_TOKENS and any(checks)  # Avoid prose that starts with a hierarchy word.

    def _has_interface_row(self, line: str) -> bool:
        return (
            bool(self.interface_pattern.match(line)) and len(line.split()) > 1
        )  # Require status or config after port.

    def _looks_like_output(self, line: str) -> bool:
        checks = [self._has_interface_row(line), bool(self.table_pattern.match(line)), line in ["}", "]"]]  # Signals.
        return any(checks) or line.endswith("{") or line.endswith(";")  # Include Junos brace configuration output.
