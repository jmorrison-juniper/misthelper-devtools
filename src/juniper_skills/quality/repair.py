"""Measured word repair for stripped Juniper source text."""

from __future__ import annotations  # Keep annotations cheap during quality imports.

import logging  # Record dictionary, repair, and accuracy actions.
import math  # Convert word frequencies into dynamic-programming costs.
import re  # Tokenize source text and detect command lines.
from collections import Counter  # Build a real corpus dictionary from healthy documents.
from pathlib import Path  # Use platform-safe paths for source scans.

from .models import RepairAccuracyReport  # Return measured repair accuracy and decision data.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class WordFrequencyDictionary:
    """Build a Juniper word-frequency dictionary from healthy documents."""

    DOMAIN_WORDS = (
        "allow-commands",
        "authentication",
        "chassis",
        "class",
        "commit",
        "delete",
        "edit",
        "encrypted-password",
        "interfaces",
        "permissions",
        "policy-options",
        "protocols",
        "request",
        "routing-options",
        "security",
        "set",
        "show",
        "system",
        "username",
    )  # Seed critical Junos command words before corpus learning.

    def __init__(self, counts: Counter[str] | None = None) -> None:
        """Initialize the WordFrequencyDictionary instance."""
        self.counts = counts or Counter()  # Store token frequencies for repair scoring.
        self.total = sum(self.counts.values()) or 1  # Store a nonzero denominator for word cost.
        self.words = set(self.counts) | set(self.DOMAIN_WORDS)  # Keep all known tokens in one lookup set.

    @classmethod
    def from_paths(cls, paths: tuple[Path, ...]) -> WordFrequencyDictionary:
        """Run the from paths operation."""
        logger.info("Building word-frequency dictionary from %s healthy documents", len(paths))  # Log corpus input.
        counts: Counter[str] = Counter()  # Collect word frequencies across healthy source files.
        for path in paths:  # Read each healthy document exactly once.
            counts.update(cls._tokens(path.read_text(encoding="utf-8", errors="ignore")))  # Learn source terms.
        counts.update({word: 1000 for word in cls.DOMAIN_WORDS})  # Ensure required Junos terms segment correctly.
        logger.debug("Built word-frequency dictionary with %s tokens", len(counts))  # Report dictionary size.
        return cls(counts)  # Return a dictionary ready for dynamic programming.

    @staticmethod
    def _tokens(text: str) -> list[str]:
        tokens = re.findall(r"[a-z0-9][a-z0-9_./-]*", text.lower())  # Keep Junos command forms intact.
        return [token.strip(".") for token in tokens if len(token.strip(".")) > 1]  # Drop noise and blank tokens.

    def cost(self, word: str) -> float:
        """Run the cost operation."""
        count = self.counts.get(word, 1)  # Give unseen domain seeds a finite cost.
        return -math.log(count / self.total)  # Prefer frequent Juniper vocabulary during segmentation.


class SourceTextRepairer:
    """Repair stripped prose only when measurement proves the repair is safe."""

    ACCURACY_BAR = 0.95  # Require high command accuracy before command auto-repair is trusted.
    MAX_WORD = 32  # Bound dynamic programming so long lines stay cheap.

    def __init__(self, dictionary: WordFrequencyDictionary) -> None:
        """Initialize the SourceTextRepairer instance."""
        self.dictionary = dictionary  # Store the measured healthy-corpus dictionary.
        self.command_words = {"set", "show", "delete", "edit", "commit", "request", "clear"}  # Detect commands.

    def repair_line(self, line: str, repair_commands: bool = False) -> str:
        """Run the repair line operation."""
        logger.info("Repairing one stripped source line")  # Log before repair attempt.
        if self._looks_like_command(line) and not repair_commands:  # Command repair needs measured proof first.
            logger.debug("Skipped command repair because command auto-repair is disabled")  # Report decision.
            return "Command unavailable because source text failed quality repair. Read the source PDF."  # Warn safely.
        repaired = self._repair_compact(line) if " " not in line.strip() else line  # Segment only spaceless text.
        logger.debug("Repaired line length changed from %s to %s", len(line), len(repaired))  # Report size change.
        return repaired  # Return repaired prose or the safe command placeholder.

    def measure_accuracy(
        self, lines: tuple[str, ...], command_accuracy_bar: float | None = None
    ) -> RepairAccuracyReport:
        """Run the measure accuracy operation."""
        logger.info("Measuring stripped-text repair accuracy on %s held-out lines", len(lines))  # Log measurement.
        bar = command_accuracy_bar or self.ACCURACY_BAR  # Allow tests to set a deterministic threshold.
        repaired = [self._repair_compact(line.replace(" ", "")) for line in lines]  # Repair stripped held-out lines.
        line_accuracy = self._line_accuracy(lines, repaired)  # Measure exact full-line matches.
        token_accuracy, tokens = self._token_accuracy(lines, repaired)  # Measure token-level recovery.
        command_accuracy, command_count = self._command_accuracy(lines, repaired)  # Measure command-line safety.
        auto_commands = command_count > 0 and command_accuracy >= bar  # Trust commands only above the safety bar.
        decision = self._decision(auto_commands, command_count, bar)  # Build a clear decision from the gate result.
        logger.debug("Repair accuracy was %.4f lines and %.4f tokens", line_accuracy, token_accuracy)  # Report result.
        return RepairAccuracyReport(
            len(lines), line_accuracy, tokens, token_accuracy, command_count, command_accuracy, auto_commands, decision
        )

    def _repair_compact(self, text: str) -> str:
        clean = text.strip()  # Remove edge whitespace created by a converter or a test fixture.
        if not clean:  # Empty lines need no segmentation.
            return clean  # Preserve empty lines.
        costs = [0.0] + [float("inf")] * len(clean)  # Store best path cost for each character index.
        links = [0] * (len(clean) + 1)  # Store previous index for the best path.
        for index in range(1, len(clean) + 1):  # Find the best prior word boundary for each index.
            self._update_cost(clean, index, costs, links)  # Evaluate candidate words ending at this index.
        words = self._backtrack(clean, links)  # Convert dynamic-programming links into words.
        return " ".join(words)  # Return a readable repaired line.

    def _update_cost(self, clean: str, index: int, costs: list[float], links: list[int]) -> None:
        start_min = max(0, index - self.MAX_WORD)  # Limit candidate length to a plausible Junos token.
        for start in range(start_min, index):  # Test every bounded substring ending at the active index.
            word = clean[start:index].lower()  # Normalize the candidate for dictionary lookup.
            if word not in self.dictionary.words and len(word) > 1:  # Reject unknown multi-character words.
                continue  # Keep searching for a valid segmentation boundary.
            cost = costs[start] + self.dictionary.cost(word)  # Add corpus-based cost for this candidate word.
            if cost < costs[index]:  # Keep the lowest-cost path to this index.
                costs[index] = cost  # Store the best cost.
                links[index] = start  # Store the matching prior boundary.

    def _backtrack(self, clean: str, links: list[int]) -> list[str]:
        words: list[str] = []  # Accumulate words in reverse order.
        index = len(clean)  # Start from the end of the compact line.
        while index > 0:  # Walk backward until the first character is reached.
            start = links[index] if links[index] < index else index - 1  # Fall back to one character on failure.
            words.append(clean[start:index])  # Store the selected segment.
            index = start  # Continue from the previous boundary.
        words.reverse()  # Restore original reading order.
        return words  # Return segmented tokens.

    def _line_accuracy(self, expected: tuple[str, ...], actual: list[str]) -> float:
        matches = sum(1 for left, right in zip(expected, actual, strict=True) if left == right)  # Count exact matches.
        return matches / len(expected) if expected else 0.0  # Report zero when no held-out lines exist.

    def _token_accuracy(self, expected: tuple[str, ...], actual: list[str]) -> tuple[float, int]:
        expected_tokens = [token for line in expected for token in line.split()]  # Count original tokens.
        actual_tokens = [token for line in actual for token in line.split()]  # Count repaired tokens.
        matches = sum(
            1 for left, right in zip(expected_tokens, actual_tokens, strict=False) if left == right
        )  # Compare.
        return (matches / len(expected_tokens) if expected_tokens else 0.0, len(expected_tokens))  # Return accuracy.

    def _command_accuracy(self, expected: tuple[str, ...], actual: list[str]) -> tuple[float, int]:
        pairs = [(left, right) for left, right in zip(expected, actual, strict=True) if self._looks_like_command(left)]
        matches = sum(1 for left, right in pairs if left == right)  # Count exact command recovery.
        return (matches / len(pairs) if pairs else 0.0, len(pairs))  # Return accuracy and command count.

    def _looks_like_command(self, line: str) -> bool:
        compact = line.strip().lower()  # Normalize a possible stripped command.
        return any(compact.startswith(word) for word in self.command_words)  # Detect Junos command starts.

    def _decision(self, auto_commands: bool, count: int, bar: float) -> str:
        if auto_commands:  # Only high measured command accuracy permits automatic command repair.
            return "Command auto-repair is enabled because held-out command accuracy met the safety bar."
        return "Command auto-repair is disabled because held-out command accuracy was below the safety bar or absent."
