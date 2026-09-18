"""Build useful one-line subjects for document indexes."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class TopicSubjectBuilder:
    """Derive an index subject from title, content concepts, and command verbs."""

    CONCEPTS = (
        "alarm",
        "BGP",
        "chassis",
        "CLI",
        "commit",
        "configuration",
        "firewall",
        "hardware",
        "interface",
        "Junos",
        "log",
        "management",
        "OSPF",
        "packet capture",
        "pipe filter",
        "routing",
        "security",
        "SNMP",
        "system",
    )  # Keep subjects on known network nouns.
    PIPE_FILTERS = ("match", "count", "except", "find", "compare", "trim")  # Name filters that route output questions.
    SUBJECT_PATTERNS = (
        ("introduction", "Introduces Junos, routing, switching, and the learning scope."),
        ("mx series", "Compares Juniper router platforms, hardware roles, and deployment fit."),
        ("srx series", "Explains SRX firewall and routing roles for branch and security designs."),
        ("transit traffic", "Explains transit traffic, exception traffic, and control plane processing."),
        ("user interface", "Introduces Junos CLI modes, prompts, and command entry behavior."),
        ("interfaces used to manage", "Explains management interface options for access to a Junos device."),
        ("management port", "Explains the dedicated management port and out-of-band access."),
        ("keyboard shortcuts", "Shows CLI shortcuts that reduce Junos command input."),
        ("cli help", "Shows help commands that find syntax, topics, and command options."),
        ("configuration file", "Explains how to find and read statements in a Junos configuration."),
        ("set command", "Shows how the set command changes candidate configuration statements."),
        ("rename", "Shows how to rename, replace, and copy configuration statements."),
        ("protecting", "Shows how to comment and protect configuration statements."),
        ("reverting", "Shows how to compare and recover earlier configuration versions."),
    )  # Prefer useful human-written subjects for common Junos guide headings.

    def build(self, title: str, text: str, lifecycle: list[str]) -> str:
        """Run the build operation."""
        logger.info("Building index subject for topic %s", title)  # Log before subject extraction.
        direct = self._pattern_subject(title)  # Use a precise subject for known heading patterns.
        if direct:  # Return high-confidence subjects before generic composition.
            logger.debug("Built pattern subject for %s", title)  # Report pattern subject use.
            return direct
        commands = self._command_terms(text)  # Extract command verbs and pipe filters from the topic.
        concepts = self._concept_terms(title, text)  # Extract dominant network nouns from the topic.
        subject = self._compose(title, commands, concepts, lifecycle)  # Compose one useful index sentence.
        logger.debug("Built subject for %s with %s concepts", title, len(concepts))  # Report extraction count.
        return subject  # Return the one-line subject for INDEX.md.

    def _command_terms(self, text: str) -> list[str]:
        terms: list[str] = []  # Accumulate command words that help routing.
        lower = text.lower()  # Normalize command text for matching.
        for term in self.PIPE_FILTERS:  # Prefer pipe filters when the topic discusses output filtering.
            if term in lower and term not in terms:  # Include each term once.
                terms.append(term)  # Store the filter term for the subject.
        for match in re.finditer(
            r"(?:^|\s)(show|set|commit|edit|delete|clear|monitor|ping|traceroute)\b", lower
        ):  # Find verbs.
            if match.group(1) not in terms:  # Avoid duplicate command verbs.
                terms.append(match.group(1))  # Store the command verb.
        return terms[:4]  # Keep the subject short.

    def _concept_terms(self, title: str, text: str) -> list[str]:
        context = f"{title}\n{text}".lower()  # Give the title and body equal access to concept matching.
        found = [
            term for term in self.CONCEPTS if term.lower() in context
        ]  # Select concepts that appear in topic text.
        return found[:3]  # Keep the sentence short and readable.

    def _compose(self, title: str, commands: list[str], concepts: list[str], lifecycle: list[str]) -> str:
        """Create the compose output."""
        subject_target = self._join(concepts) or title  # Reuse the fallback subject target in each branch.
        if "filter" in title.lower() and commands:  # Use a specific subject for output filtering topics.
            return f"Use {self._join(commands[:3])} pipe filters to read operational command output."
        lifecycle_subject = self._lifecycle_subject(title, commands, lifecycle, subject_target)
        if lifecycle_subject:  # Prefer lifecycle-specific language when the lifecycle is known.
            return lifecycle_subject
        command_target = self._join(commands) or title  # Reuse the fallback command target in the default subject.
        return f"Verify and troubleshoot {subject_target} with {command_target}."

    def _lifecycle_subject(self, title: str, commands: list[str], lifecycle: list[str], subject_target: str) -> str:
        """Return the lifecycle subject when one is available."""
        command_target = self._join(commands) or title  # Reuse the fallback command target in the change subject.
        if "day0" in lifecycle:  # Explain design and selection topics with selection wording.
            return f"Select or compare {subject_target} for {title}."
        if "day1" in lifecycle and commands:  # Explain setup topics by naming the command terms.
            return f"Configure {subject_target} with {self._join(commands)} commands."
        if "day2plus" in lifecycle:  # Explain change topics with change and recovery wording.
            return f"Change or recover {subject_target} with {command_target}."
        return ""  # Tell the caller to use the generic troubleshooting subject.

    def _pattern_subject(self, title: str) -> str:
        lowered = title.lower()  # Normalize the title for pattern matching.
        for pattern, subject in self.SUBJECT_PATTERNS:  # Check high-confidence topic title patterns.
            if pattern == "introduction" and lowered != pattern:  # Use the introduction subject only for that topic.
                continue
            if pattern in lowered:  # Return the first matching subject.
                return subject
        return ""  # Tell the caller to use generic subject composition.

    def _join(self, values: list[str]) -> str:
        if not values:  # Return an empty string when no terms exist.
            return ""
        if len(values) == 1:  # Return one term without punctuation.
            return values[0]
        return ", ".join(values[:-1]) + ", and " + values[-1]  # Return a serial list for STE readability.
