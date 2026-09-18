"""Rewrite backend seam for Juniper documentation skills."""

from __future__ import annotations  # Keep abstract type annotations cheap.

import logging  # Record backend actions for factory observability.
import re  # Extract command blocks, tables, and numeric limits deterministically.
from abc import ABC, abstractmethod  # Define the backend plug-in seam.

from .cards import CardExtractor  # Reuse card extraction for deterministic facts.
from .models import CardClassMark, KnowledgeCard, RewriteResult, RewriteWorkPacket  # Use shared rewrite models.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.

_LOG = logging.getLogger(__name__)  # Give the rewrite backend a stable logger name.


class RewriteBackend(ABC):
    """Interface that every rewrite backend must implement."""

    @abstractmethod
    def rewrite(self, packet: RewriteWorkPacket) -> RewriteResult:
        """Return publishable cards and verbatim command blocks for one packet."""


class RuleBasedBackend(RewriteBackend):
    """Extract only facts that deterministic rules can handle honestly."""

    def __init__(self) -> None:
        """Create the deterministic backend dependencies."""
        self._extractor = CardExtractor()  # Reuse page-aware candidate card extraction.

    def rewrite(self, packet: RewriteWorkPacket) -> RewriteResult:
        """Return cards for structured content without pretending to restate prose."""
        logger.info("Running the rule-based rewrite backend")  # Log before deterministic extraction.
        command_blocks = self._command_blocks(packet)  # Preserve detected commands unchanged.
        cards = self._structured_cards(packet)  # Extract tables and numeric facts as INFO cards.
        limitations = self._limitations(packet, cards)  # State the prose that this backend cannot restate.
        logger.debug(
            "Rule-based backend emitted %d cards and %d command blocks",
            len(cards),
            len(command_blocks),
        )  # Log counts.
        return RewriteResult(tuple(cards), tuple(command_blocks), limitations)  # Return deterministic output.

    def _command_blocks(self, packet: RewriteWorkPacket) -> tuple[str, ...]:
        """Return command blocks that must pass through verbatim."""
        if packet.detected_commands:  # The segmenter already supplied command blocks.
            return packet.detected_commands  # Trust the segmenter command extraction.
        return tuple(line for line in packet.source_segment.splitlines() if self._is_command(line))  # Fallback scan.

    def _structured_cards(self, packet: RewriteWorkPacket) -> list[KnowledgeCard]:
        """Return INFO cards from tables and numeric facts."""
        cards = self._table_cards(packet)  # Convert Markdown table rows to structured facts.
        cards.extend(self._numeric_cards(packet))  # Add numeric limits that need exact preservation.
        if not cards:  # Use conservative extraction only when structure is absent.
            cards.extend(self._extractor.extract(packet.source_segment, packet.citation_key))  # Extract candidates.
        return cards  # Return the candidate card list.

    def _table_cards(self, packet: RewriteWorkPacket) -> list[KnowledgeCard]:
        """Return one INFO card for each meaningful table row."""
        lines = packet.source_segment.splitlines()  # Read source lines once for table detection.
        rows = [line.strip() for line in lines if line.strip().startswith("|")]  # Find Markdown table rows.
        body_rows = [row for row in rows if not re.match(r"^\|?\s*-", row)]  # Drop Markdown divider rows.
        return [self._table_card(row, packet) for row in body_rows[1:]] if len(body_rows) > 1 else []  # Skip header.

    def _table_card(self, row: str, packet: RewriteWorkPacket) -> KnowledgeCard:
        """Return a structured INFO card for one table row."""
        cells = [cell.strip() for cell in row.strip("|").split("|") if cell.strip()]  # Keep non-empty table cells.
        fact = "The table row gives: " + ", ".join(cells) + "."  # Preserve structured values without prose copying.
        citation = self._citation(packet)  # Build the exact citation for the row.
        return KnowledgeCard(CardClassMark.INFO, fact, citation)  # Return one structured card.

    def _numeric_cards(self, packet: RewriteWorkPacket) -> list[KnowledgeCard]:
        """Return INFO cards for sentences that contain numeric limits."""
        cards: list[KnowledgeCard] = []  # Collect numeric facts that rules can preserve.
        for sentence in self._sentences(packet.source_segment):  # Check sentence-sized facts.
            if re.search(r"\b\d+(\.\d+)?\s?(%|dB|Gb/s|Mb/s|nm|km|m|W|V|A)?\b", sentence):  # Find numeric data.
                cards.append(KnowledgeCard(CardClassMark.INFO, sentence, self._citation(packet)))  # Preserve values.
        return cards  # Return all numeric cards.

    def _sentences(self, text: str) -> tuple[str, ...]:
        """Return candidate sentences after obvious structures are removed."""
        prose = " ".join(line.strip() for line in text.splitlines() if not self._is_command(line))  # Remove commands.
        return tuple(part.strip() for part in re.split(r"(?<=[.!?])\s+", prose) if part.strip())  # Split sentences.

    def _citation(self, packet: RewriteWorkPacket) -> str:
        """Return the citation for a work packet."""
        return f"[{packet.citation_key} {packet.page_range}]"  # Keep the exact page range with the source key.

    def _limitations(self, packet: RewriteWorkPacket, cards: list[KnowledgeCard]) -> tuple[str, ...]:
        """Return honest limits for deterministic rewriting."""
        limits = ["RuleBasedBackend does not restate narrative prose."]  # State the main backend limit.
        if not cards:  # A structured segment can still have no extractable fact.
            limits.append("RuleBasedBackend found no table row or numeric fact.")  # State the empty-output reason.
        return tuple(limits)  # Return immutable limits for the orchestrator report.

    def _is_command(self, line: str) -> bool:
        """Return whether a line is a CLI or configuration line."""
        stripped = line.strip()  # Normalize whitespace before CLI detection.
        prompt = re.search(r"\b[\w.-]+@[\w.-]+[>#]\s*\S+", stripped)  # Detect prompt-style commands.
        return bool(prompt or re.match(r"^(set|delete|show|run|commit|edit)\b", stripped))  # Detect direct commands.


class PromptTemplateBuilder:
    """Build the instruction text for a future agent rewrite backend."""

    def build(self, packet: RewriteWorkPacket) -> str:
        """Return the full agent instruction for one rewrite packet."""
        logger.info("Building the agent rewrite prompt")  # Log before prompt construction.
        sections = (self._role(packet), self._ste_rules(), self._copyright_rules(), self._output_rules(packet))
        prompt = "\n\n".join(sections)  # Join stable sections so prompt caching stays effective.
        logger.debug("Built a rewrite prompt with %d characters", len(prompt))  # Log prompt size, not content.
        return prompt  # Return the backend prompt.

    def _role(self, packet: RewriteWorkPacket) -> str:
        """Return the task and routing context section."""
        tags = ", ".join(packet.lifecycle_tags)  # Render life cycle tags for the agent.
        return (
            "Restate Juniper documentation as publishable knowledge cards.\n"
            f"Domain: {packet.target_domain}.\n"
            f"Life cycle tags: {tags}.\n"
            f"Citation key: [{packet.citation_key} {packet.page_range}]."
        )  # Return stable task context.

    def _ste_rules(self) -> str:
        """Return the measurable STE rules the agent must obey."""
        return (
            "Write all prose in Simplified Technical English.\n"
            "Use active voice and simple tenses.\n"
            "Keep instruction sentences at 20 words or fewer.\n"
            "Keep description sentences at 25 words or fewer.\n"
            "Do not use semicolons or Latin abbreviations.\n"
            "Use American spelling.\n"
            "Use one term for one concept."
        )  # Return the STE summary from the locked contract.

    def _copyright_rules(self) -> str:
        """Return the copyright and verbatim-class rules."""
        return (
            "Do not copy Juniper source prose.\n"
            "Restate each fact in new words.\n"
            "Keep commands, configuration lines, command output, identifiers, model numbers, port names, "
            "numeric limits, standard names, and protocol names verbatim.\n"
            "Fence every command and command output block."
        )  # Return the copyright safety rules.

    def _output_rules(self, packet: RewriteWorkPacket) -> str:
        """Return the required card format and source segment."""
        commands = "\n".join(packet.detected_commands)  # Preserve commands for the prompt.
        commands = commands or "None detected."  # State when the segmenter found no commands.
        return (
            "Return only Markdown list items in this format:\n"
            "- **MUST|SHOULD|INFO** Restated fact. [CITATION p.N]\n"
            "Use MUST for a hard limit or a required practice.\n"
            "Use SHOULD for a recommendation with a judged exception.\n"
            "Use INFO for a fact that sets no hard limit.\n"
            f"Detected commands that must pass through:\n{commands}\n"
            f"Source segment:\n{packet.source_segment}"
        )  # Return the complete work payload.
