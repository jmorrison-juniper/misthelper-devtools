"""Classify segment topics by life cycle signals from the locked contract."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


@dataclass(frozen=True)
class LifecycleClassification:
    """One topic life cycle decision with the matched evidence."""

    tags: list[str]  # Store one or more contract life cycle tags.
    signals: dict[str, list[str]]  # Store matched words that justify each tag.


class LifecycleClassifier:
    """Score day0, day1, day2, and day2plus from topic title and text."""

    TAG_ORDER = ["day0", "day1", "day2", "day2plus"]  # Keep report order stable.
    SIGNALS = {  # Keep contract signal words local to the classifier.
        "day0": (
            "architecture",
            "capacity",
            "choose",
            "compare",
            "design",
            "feature",
            "hardware",
            "limit",
            "model",
            "platform",
            "product",
            "requirement",
            "select",
            "series",
            "sizing",
            "throughput",
            "topology",
        ),
        "day1": (
            "activate",
            "configuration",
            "bootstrap",
            "cable",
            "claim",
            "commit",
            "configure",
            "connect",
            "deploy",
            "edit",
            "enroll",
            "initial",
            "install",
            "login",
            "mount",
            "power",
            "rack",
            "set",
            "setup",
            "start",
        ),
        "day2": (
            "alarm",
            "check",
            "clear",
            "counter",
            "diagnostic",
            "event",
            "health",
            "log",
            "monitor",
            "packet capture",
            "repair",
            "show",
            "statistic",
            "statistics",
            "test",
            "trace",
            "troubleshoot",
            "verify",
            "view",
        ),
        "day2plus": (
            "archive",
            "archival",
            "automate",
            "change",
            "convert",
            "copy",
            "expand",
            "export",
            "extend",
            "import",
            "integrate",
            "migrate",
            "modifying",
            "rename",
            "replace",
            "rescue",
            "revert",
            "reverting",
            "rollback",
            "saving",
            "upgrade",
        ),
    }
    COMMAND_TAGS = {  # Map Junos command verbs to their common life cycle stage.
        "clear": "day2",
        "commit": "day1",
        "configure": "day1",
        "delete": "day2plus",
        "edit": "day1",
        "help": "day2",
        "monitor": "day2",
        "ping": "day2",
        "request": "day2plus",
        "run": "day2",
        "set": "day1",
        "show": "day2",
        "traceroute": "day2",
    }

    def classify(self, title: str, text: str) -> LifecycleClassification:
        """Run the classify operation."""
        logger.info("Classifying life cycle tags for topic %s", title)  # Log before signal scoring.
        title_context = title.lower()  # Give the heading the strongest classification weight.
        body_context = self._context(text)  # Use the body only when the heading lacks strong evidence.
        signals = {tag: self._title_signals(title_context, tag) for tag in self.TAG_ORDER}  # Score headings first.
        self._add_command_signals(text, signals)  # Add repeated command verb signals from fenced Junos samples.
        self._add_body_signals(body_context, signals)  # Use body signals only when title and commands give no tag.
        tags = self._selected_tags(signals)  # Select positive tags without tagging every broad topic.
        signals = self._ensure_signal(tags, signals)  # Store a reason for each assigned tag.
        logger.debug("Topic %s classified as %s", title, ",".join(tags))  # Report tag decision.
        return LifecycleClassification(tags, signals)  # Return the decision and evidence.

    def _context(self, text: str) -> str:
        cleaned = re.sub(r"`{3}.*?`{3}", " ", text, flags=re.DOTALL)  # Remove code blocks from prose scoring.
        return cleaned.lower()  # Return prose context without command samples.

    def _title_signals(self, title: str, tag: str) -> list[str]:
        matches = [signal for signal in self.SIGNALS[tag] if signal in title]  # Find contract signals in the title.
        return matches[:5]  # Keep stored evidence small and readable.

    def _add_body_signals(self, body: str, signals: dict[str, list[str]]) -> None:
        if any(signals[tag] for tag in self.TAG_ORDER):  # Do not let broad prose turn every topic into every stage.
            return
        matches = {tag: [signal for signal in self.SIGNALS[tag] if signal in body] for tag in self.TAG_ORDER}  # Find.
        best = max(self.TAG_ORDER, key=lambda tag: len(matches[tag]))  # Select the strongest content-only stage.
        if len(matches[best]) >= 3:  # Require several content signals before using body-only evidence.
            signals[best].extend(matches[best][:3])  # Add a compact proof set for the selected tag.

    def _add_command_signals(self, text: str, signals: dict[str, list[str]]) -> None:
        counts = self._command_counts(text)  # Count command verbs inside the topic text.
        for verb, count in counts.items():  # Inspect each command verb count.
            tag = self.COMMAND_TAGS.get(verb)  # Look up the stage that this command verb supports.
            if tag and count >= 2 and verb not in signals[tag]:  # Require repeated command evidence.
                signals[tag].append(verb)  # Add the command signal to the matching tag.

    def _command_counts(self, text: str) -> dict[str, int]:
        counts: dict[str, int] = {}  # Accumulate command verb counts.
        for line in text.splitlines():  # Inspect each line for prompt or command syntax.
            verb = self._verb_from_line(line.strip())  # Read the Junos command verb when present.
            if verb:  # Count only lines that carry known Junos command verbs.
                counts[verb] = counts.get(verb, 0) + 1  # Increment the verb count.
        return counts  # Return all command verbs found in the topic.

    def _verb_from_line(self, line: str) -> str:
        prompt = re.match(r"^[\w.-]+@[\w.-]+[>#]\s+(\w+)", line)  # Read command after a Junos prompt.
        if prompt:  # Prompt commands are explicit operational evidence.
            return prompt.group(1).lower()
        first = line.split(maxsplit=1)[0].lower() if line.split() else ""  # Read bare command line verbs.
        return first if first in self.COMMAND_TAGS else ""  # Return only known Junos command verbs.

    def _selected_tags(self, signals: dict[str, list[str]]) -> list[str]:
        tags = [tag for tag in self.TAG_ORDER if signals[tag]]  # Select every tag with strong evidence.
        return tags or ["day0"]  # Infer day0 when no signal exists per contract.

    def _ensure_signal(self, tags: list[str], signals: dict[str, list[str]]) -> dict[str, list[str]]:
        for tag in tags:  # Ensure each selected tag can explain why it was selected.
            if not signals[tag]:  # Add the required inferred reason only for zero-signal fallbacks.
                signals[tag] = ["inferred-general"]  # Match the contract wording for inferred coverage.
        return signals  # Return evidence for all selected tags.
