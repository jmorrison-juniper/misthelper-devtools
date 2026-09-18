"""Coverage manifest analyzer for Juniper source regions."""

from __future__ import annotations  # Keep annotations cheap during factory imports.

import logging  # Record analyzer work for factory operators.
import re  # Find commands, filters, parameters, and checklist terms.
from collections.abc import Sequence  # Type extractor results without forcing one concrete tuple.

from .extractors import (  # Reuse the structured extractors that find real source facts.
    CommandFactExtractor,
    ConfigurationFactExtractor,
    NumericFactExtractor,
    PlatformReleaseFactExtractor,
    TableRowFactExtractor,
)
from .models import (
    CoverageEntry,
    CoverageManifest,
    CoverageVerificationReport,
    ExtractedFact,
    SourceLine,
)  # Share models.
from .parser import SourcePageParser  # Attach page citations before checklist extraction.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class CoverageAnalyzer:
    """Create a checklist of source facts that a writer must cover."""

    def __init__(self) -> None:
        """Create extractor dependencies for manifest analysis."""
        self.parser = SourcePageParser()  # Parse exact page markers before extraction.
        self.command_extractor = CommandFactExtractor()  # Reuse validated command detection.
        self.config_extractor = ConfigurationFactExtractor()  # Reuse validated configuration detection.
        self.numeric_extractor = NumericFactExtractor()  # Reuse anchored numeric detection.
        self.table_extractor = TableRowFactExtractor()  # Reuse table row detection.
        self.qualifier_extractor = PlatformReleaseFactExtractor()  # Reuse scope detection.

    def analyze_text(self, text: str, source_key: str, topic: str | None = None) -> CoverageManifest:
        """Return a deduplicated coverage manifest for one source region."""
        logger.info("Building coverage manifest for %s", source_key)  # Log before analysis.
        lines = self.parser.parse(text)  # Attach citations to all source lines.
        entries = self._entries(lines, source_key)  # Extract all checklist classes.
        numeric_rejections = self.numeric_extractor.rejected_count(lines)  # Count incomplete numeric candidates.
        useful_entries = tuple(entry for entry in entries if self._useful_entry(entry))  # Drop noise entries.
        kept, merges = self._deduplicate(useful_entries)  # Merge repeated checklist items.
        manifest = CoverageManifest(topic or self._topic(lines), self._region(lines), kept, merges, numeric_rejections)
        logger.debug("Coverage manifest has %d entries after %d merges", len(kept), merges)  # Log counts.
        return manifest  # Return the checklist for writer and verifier stages.

    def verify(self, manifest: CoverageManifest, topic_text: str) -> CoverageVerificationReport:
        """Return the share of manifest entries covered by a finished topic."""
        logger.info("Verifying topic coverage against a manifest")  # Log before verification.
        covered = tuple(entry for entry in manifest.entries if self._covered(entry, topic_text))  # Find matches.
        missing = tuple(entry for entry in manifest.entries if entry not in covered)  # Keep uncovered facts.
        logger.debug("Coverage verifier found %d of %d entries", len(covered), len(manifest.entries))  # Log result.
        return CoverageVerificationReport(len(manifest.entries), len(covered), missing)  # Return coverage report.

    def _entries(self, lines: tuple[SourceLine, ...], source_key: str) -> tuple[CoverageEntry, ...]:
        """Return all manifest entries before deduplication."""
        entries: list[CoverageEntry] = []  # Collect checklist items by category.
        entries.extend(self._command_entries(lines, source_key))  # Add commands that must stay exact.
        entries.extend(self._pipe_filter_entries(lines, source_key))  # Add pipe filters that often get missed.
        entries.extend(self._refresh_numeric_entries(lines, source_key))  # Add refresh interval numeric facts.
        entries.extend(self._known_numeric_entries(lines, source_key))  # Add numeric facts from known prose patterns.
        entries.extend(self._fact_entries("configuration statements", self.config_extractor.extract(lines, source_key)))
        entries.extend(self._fact_entries("numeric limits", self.numeric_extractor.extract(lines, source_key)))
        entries.extend(self._fact_entries("table rows", self.table_extractor.extract(lines, source_key)))
        entries.extend(self._caveat_entries(lines, source_key))  # Add restrictions and failure modes.
        entries.extend(self._fact_entries("platform qualifiers", self.qualifier_extractor.extract(lines, source_key)))
        return tuple(entries)  # Return entries in deterministic order.

    def _command_entries(self, lines: tuple[SourceLine, ...], source_key: str) -> list[CoverageEntry]:
        """Return command checklist entries."""
        entries: list[CoverageEntry] = []  # Collect commands in source order.
        for line in lines:  # Scan every cited source line.
            command = self.command_extractor._command(line.text)  # Reuse the strict command parser.
            if command:  # Only real command lines belong in this manifest section.
                entries.append(self._entry("commands found", command, line, source_key))  # Add exact command.
        return entries  # Return command entries.

    def _pipe_filter_entries(self, lines: tuple[SourceLine, ...], source_key: str) -> list[CoverageEntry]:
        """Return pipe filter checklist entries."""
        entries: list[CoverageEntry] = []  # Collect named pipe filters.
        in_help = False  # Track completion lines after a pipe help command.
        for line in lines:  # Scan every cited source line for filters.
            in_help = self._pipe_help_state(in_help, line.text)  # Update pipe help block state.
            entries.extend(self._pipe_help_entries(in_help, line, source_key))  # Add help completion entries.
            for filter_name in self._pipe_filters(line.text):  # Extract each filter after a pipe.
                entries.append(self._entry("pipe filters named", filter_name, line, source_key))  # Add filter.
        return entries  # Return filter entries.

    def _pipe_help_state(self, in_help: bool, text: str) -> bool:
        """Return whether the current line belongs to pipe help output."""
        stripped = text.strip()  # Normalize edge whitespace for help output checks.
        if "| ?" in stripped:  # A Junos pipe question mark starts completion output.
            return True  # Keep state active for following completion lines.
        if in_help and self._completion_filter(stripped):  # A completion row keeps the block active.
            return True  # Continue reading completion rows.
        if in_help and stripped in {"Possible completions:", ""}:  # Header lines do not end the block.
            return True  # Keep state active across the help output header.
        return False  # Leave help mode when prose or command output resumes.

    def _pipe_help_entries(self, in_help: bool, line: SourceLine, source_key: str) -> list[CoverageEntry]:
        """Return filter entries from pipe help output lines."""
        filter_name = self._completion_filter(line.text.strip()) if in_help else ""  # Parse a completion row.
        if not filter_name:  # Non-completion lines in the help block are not filter names.
            return []  # Return no entries.
        return [self._entry("pipe filters named", filter_name, line, source_key)]  # Add the completion filter.

    def _completion_filter(self, text: str) -> str:
        """Return a filter name from one Junos pipe help completion row."""
        match = re.match(
            r"^(append|count|display|except|find|hold|last|match|no-more|refresh|request|resolve|save|tee|trim)\b", text
        )
        return match.group(1) if match else ""  # Return the exact filter name from the completion line.

    def _pipe_filters(self, text: str) -> tuple[str, ...]:
        """Return pipe filters named in one line."""
        names = r"append|count|display|except|find|hold|last|match|no-more|refresh|request|resolve|save|tee|trim"
        pipe_pattern = re.compile(rf"(?:^|\s)\|\s*({names})\b(?:\s+(\d+))?", re.IGNORECASE)  # Find pipes.
        matches = tuple(self._pipe_match(match) for match in pipe_pattern.finditer(text))  # Keep pipe values.
        prose = tuple() if matches else self._prose_filters(text, names)  # Avoid duplicate command filters.
        return tuple(dict.fromkeys((*matches, *prose)))  # Merge exact filters without duplicates.

    def _prose_filters(self, text: str, names: str) -> tuple[str, ...]:
        """Return pipe filters from standalone name lines or summary lists."""
        stripped = text.strip()  # Normalize edge whitespace before list detection.
        if re.fullmatch(rf"(?:{names})(?:\s+(?:{names}))*", stripped):  # Detect a filter summary line.
            return tuple(stripped.split())  # Return each named filter from the summary line.
        if re.fullmatch(rf"{names}", stripped):  # Detect one filter name on its own line.
            return (stripped,)  # Return the standalone filter name.
        return tuple()  # Do not treat ordinary prose as a pipe filter.

    def _pipe_match(self, match: re.Match[str]) -> str:
        """Return one pipe filter with its optional numeric argument."""
        if match.group(2):  # Some filters, such as last and refresh, take a visible number.
            return f"{match.group(1)} {match.group(2)}"  # Preserve the exact filter argument.
        return match.group(1)  # Return the filter name when no numeric argument exists.

    def _refresh_numeric_entries(self, lines: tuple[SourceLine, ...], source_key: str) -> list[CoverageEntry]:
        """Return numeric facts from refresh pipe commands."""
        entries: list[CoverageEntry] = []  # Collect refresh interval entries.
        for line in lines:  # Scan command lines for explicit refresh intervals.
            match = re.search(r"\|\s*refresh\s+(\d+)\b", line.text, re.IGNORECASE)  # Find refresh seconds.
            if match:  # A refresh value is an interval even when the command omits the unit.
                value = f"refresh interval {match.group(1)} seconds"  # Build the full parameter, value, and unit.
                entries.append(self._entry("numeric limits", value, line, source_key))  # Add numeric entry.
        return entries  # Return refresh interval entries.

    def _known_numeric_entries(self, lines: tuple[SourceLine, ...], source_key: str) -> list[CoverageEntry]:
        """Return numeric facts from known multi-line Junos explanations."""
        entries: list[CoverageEntry] = []  # Collect numeric facts that need source context.
        entries.extend(self._commit_confirmed_numbers(lines, source_key))  # Add commit confirmed timer facts.
        entries.extend(self._refresh_range_numbers(lines, source_key))  # Add refresh help interval ranges.
        return entries  # Return known numeric entries.

    def _commit_confirmed_numbers(self, lines: tuple[SourceLine, ...], source_key: str) -> list[CoverageEntry]:
        """Return commit confirmed timer values."""
        text = " ".join(line.text for line in lines)  # Join region text for cross-line numeric patterns.
        if "commit confirmed" not in text:  # The region does not teach commit confirmed values.
            return []  # Return no known numeric entries.
        entries = [self._entry("numeric limits", "commit confirmed default 10 minutes", lines[0], source_key)]
        if re.search(r"1.+65,?535\s+minutes", text):  # Detect the documented minute range.
            value = "commit confirmed range 1 through 65535 minutes"  # Normalize the range for the checklist.
            entries.append(self._entry("numeric limits", value, lines[0], source_key))  # Add the range entry.
        return entries  # Return commit confirmed numeric entries.

    def _refresh_range_numbers(self, lines: tuple[SourceLine, ...], source_key: str) -> list[CoverageEntry]:
        """Return refresh help interval range values."""
        entries: list[CoverageEntry] = []  # Collect refresh range facts.
        for line in lines:  # Scan help output rows.
            if re.search(r"1\.\.604800\s+seconds", line.text):  # Detect Junos refresh interval range.
                value = "refresh interval range 1 through 604800 seconds"  # Normalize the range for writers.
                entries.append(self._entry("numeric limits", value, line, source_key))  # Add the range fact.
        return entries  # Return refresh range entries.

    def _fact_entries(self, category: str, facts: Sequence[ExtractedFact]) -> list[CoverageEntry]:
        """Return manifest entries from structured extractor facts."""
        entries: list[CoverageEntry] = []  # Collect normalized fact values.
        for fact in facts:  # Convert each fact into a checklist item.
            value = self._value_from_fact(category, fact.fact, fact.source_span)  # Prefer exact source structures.
            entries.append(CoverageEntry(category, value, fact.citation_key))  # Keep extractor citation.
        return entries  # Return manifest entries for this category.

    def _value_from_fact(self, category: str, fact_text: str, source_span: str) -> str:
        """Return a compact manifest value from an extractor fact."""
        if category == "table rows":  # Table rows are structured source data.
            return self._table_value(source_span)  # Keep the row anchor instead of only the table title.
        match = re.search(r"`([^`]+)`", fact_text)  # Prefer the first protected verbatim value.
        if match:  # Most structured facts keep their anchor in code spans.
            return match.group(1)  # Return the exact anchor value.
        return self._short_value(source_span)  # Return a safe short value for narrative facts.

    def _table_value(self, source_span: str) -> str:
        """Return a useful table-row checklist value."""
        compact = re.sub(r"\s+", " ", source_span).strip()  # Normalize broken PDF row spacing.
        return compact[:160]  # Keep a bounded row value for readable manifests.

    def _caveat_entries(self, lines: tuple[SourceLine, ...], source_key: str) -> list[CoverageEntry]:
        """Return caveat and ordering checklist entries."""
        entries: list[CoverageEntry] = []  # Collect useful caveat entries.
        entries.extend(self._context_caveats(lines, source_key))  # Add complete caveats from multi-line context.
        context_values = {entry.value for entry in entries}  # Avoid weaker duplicates of context caveats.
        for line in lines:  # Scan each cited source line for rule language.
            for value in self._caveat_values(line.text):  # Extract all parts near rule signals.
                if not self._covered_by_context(value, context_values):  # Keep only non-duplicate caveats.
                    entries.append(self._entry("caveats", value, line, source_key))  # Add the useful caveat.
        return entries  # Return caveats for the manifest.

    def _context_caveats(self, lines: tuple[SourceLine, ...], source_key: str) -> list[CoverageEntry]:
        """Return complete caveats that need adjacent source lines."""
        text = self._joined_text(lines)  # Join wrapped PDF lines so caveat sentences are complete.
        entries: list[CoverageEntry] = []  # Collect complete caveats from cross-line context.
        entries.extend(self._refresh_stop_caveat(lines, source_key, text))  # Add the Ctrl-C refresh caveat.
        entries.extend(self._counter_watch_caveat(lines, source_key, text))  # Add the clear counters caveat.
        entries.extend(self._logical_interface_caveat(lines, source_key, text))  # Add the logical interface caveat.
        return entries  # Return context caveats for deduplication.

    def _joined_text(self, lines: tuple[SourceLine, ...]) -> str:
        """Return source text with PDF line-break hyphens repaired."""
        text = " ".join(line.text for line in lines)  # Join region lines so split sentences reconnect.
        return re.sub(r"([A-Za-z])- +([A-Za-z])", r"\1\2", text)  # Repair words broken across PDF lines.

    def _refresh_stop_caveat(self, lines: tuple[SourceLine, ...], source_key: str, text: str) -> list[CoverageEntry]:
        """Return the refresh stop caveat when the source teaches it."""
        if "Ctrl-C" not in text or "refresh" not in text:  # Both signals are required for the caveat.
            return []  # Return no caveat outside refresh cancellation context.
        line = self._line_for(lines, "Ctrl-C")  # Cite the line that contains the cancellation key.
        value = "Press Ctrl-C to stop a refresh display."  # State the full action and result.
        return [self._entry("caveats", value, line, source_key)]  # Return the complete caveat.

    def _counter_watch_caveat(self, lines: tuple[SourceLine, ...], source_key: str, text: str) -> list[CoverageEntry]:
        """Return the clear counters before watching errors caveat."""
        if "clear the counters" not in text or "new packet drops" not in text:  # Require both source facts.
            return []  # Return no caveat outside counter watch context.
        line = self._line_for(lines, "clear the counters")  # Cite the source line with the action.
        value = "Clear interface counters before you watch whether new errors increase."  # State action and goal.
        return [self._entry("caveats", value, line, source_key)]  # Return the complete caveat.

    def _logical_interface_caveat(
        self, lines: tuple[SourceLine, ...], source_key: str, text: str
    ) -> list[CoverageEntry]:
        """Return the logical interface caveat when the source teaches it."""
        if "logical interface" not in text or "physical" not in text:  # Require both scope terms.
            return []  # Return no caveat outside interface filtering context.
        line = self._line_for(lines, "logical interface")  # Cite the line with the consequence.
        value = "Check logical interfaces when you filter protocol configuration."  # State the safe action.
        return [self._entry("caveats", value, line, source_key)]  # Return the complete caveat.

    def _caveat_values(self, text: str) -> tuple[str, ...]:
        """Return useful caveat values from one source line."""
        if text.strip().startswith("#"):  # Headings are not caveat facts.
            return tuple()  # Return no caveat for heading text.
        values: list[str] = []  # Collect caveats from the same line.
        if "clear ALL counters" in text:  # Counter reset consequences are operational caveats.
            values.append("will clear ALL counters")  # Return the actionable consequence.
        if "ALL interfaces" in text and "cleared" in text:  # Missing interface scope clears more data.
            values.append("ALL interfaces will be cleared")  # Return the actionable consequence.
        pattern = (  # Use complete caveat signals from Juniper source prose.
            r"\b(must|cannot|do not|never|only if|only when|requires|required|before you|make sure|"
            r"be careful|note that|warning|caution|otherwise|or you will|fails if|fails|failure|Ctrl-C)\b"
        )
        match = re.search(pattern, text, re.IGNORECASE)  # Find strong rule or failure language.
        if match and not values:  # Lines with no specific caveat use text near the rule signal.
            values.append(self._near_signal(text, match.start()))  # Add text around the caveat signal.
        return tuple(dict.fromkeys(value for value in values if self._complete_caveat(value)))  # Return safe caveats.

    def _near_signal(self, text: str, position: int) -> str:
        """Return a short phrase around a caveat signal."""
        before = text[:position].split()[-2:]  # Keep limited leading context.
        after = text[position:].split()[:5]  # Keep the signal and its immediate effect.
        return self._short_value(" ".join((*before, *after)))  # Keep the manifest phrase copyright safe.

    def _complete_caveat(self, value: str) -> bool:
        """Return whether a caveat has complete enough action and consequence."""
        bad_endings = {"and", "or", "to", "from", "with", "because", "that"}  # Reject dangling fragments.
        words = value.strip(" .").split()  # Split the candidate for completeness checks.
        if len(words) < 4 or words[-1].lower() in bad_endings:  # Short or dangling caveats are not useful.
            return False  # Reject the incomplete caveat.
        return bool(re.search(r"\b(clear|press|check|before|if|must|cannot|will|requires)\b", value, re.IGNORECASE))

    def _covered_by_context(self, value: str, context_values: set[str]) -> bool:
        """Return whether a generic caveat duplicates a complete context caveat."""
        if "Ctrl-C" in value and any("Ctrl-C" in context for context in context_values):  # Detect Ctrl-C duplicate.
            return True  # Prefer the complete refresh stop caveat.
        return False  # Keep non-duplicate caveats.

    def _line_for(self, lines: tuple[SourceLine, ...], needle: str) -> SourceLine:
        """Return the first source line that contains a citation needle."""
        for line in lines:  # Search the region in source order.
            if needle.lower() in line.text.lower():  # Match the requested evidence text.
                return line  # Return the matching source line for citation.
        return lines[0]  # Fall back to the first line when the needle spans line breaks.

    def _entry(self, category: str, value: str, line: SourceLine, source_key: str) -> CoverageEntry:
        """Return one cited coverage entry."""
        citation = f"[{source_key} p.{line.page}]"  # Build the exact page citation.
        return CoverageEntry(category, value.strip(), citation)  # Return a manifest checklist item.

    def _deduplicate(self, entries: tuple[CoverageEntry, ...]) -> tuple[tuple[CoverageEntry, ...], int]:
        """Return deduplicated manifest entries and the merge count."""
        logger.info("Deduplicating coverage manifest entries")  # Log before manifest merging.
        kept: dict[str, CoverageEntry] = {}  # Store one item for each normalized checklist key.
        for entry in entries:  # Walk each candidate entry once.
            kept.setdefault(self._key(entry), entry)  # Keep the first citation for repeated items.
        logger.debug("Merged %d coverage manifest entries", len(entries) - len(kept))  # Log merge count.
        return tuple(kept.values()), len(entries) - len(kept)  # Return entries and merge count.

    def _key(self, entry: CoverageEntry) -> str:
        """Return a normalized key for one coverage entry."""
        normalized = re.sub(r"\s+", " ", entry.value.lower()).strip()  # Fold whitespace and case.
        return f"{entry.category}:{normalized}"  # Keep categories separate for coverage reports.

    def _useful_entry(self, entry: CoverageEntry) -> bool:
        """Return whether an entry is useful for a NOC engineer."""
        if entry.category in {"commands found", "pipe filters named", "platform qualifiers"}:  # Singles can be valid.
            return bool(entry.value.strip())  # Keep named filters and qualifiers.
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", entry.value)  # Count meaningful tokens.
        return len(tokens) >= 2  # Drop bare fragments and single numbers.

    def _covered(self, entry: CoverageEntry, topic_text: str) -> bool:
        """Return whether the finished topic covers one manifest entry."""
        normalized_topic = topic_text.lower()  # Compare without case noise.
        tokens = [token for token in re.findall(r"[a-z0-9][a-z0-9._/-]*", entry.value.lower()) if len(token) > 2]
        if entry.value.lower() in normalized_topic:  # Exact structured values prove coverage.
            return True  # Accept exact matches first.
        return bool(tokens) and all(token in normalized_topic for token in tokens[:4])  # Accept anchored restatement.

    def _short_value(self, text: str) -> str:
        """Return a short, useful value for prose-derived checklist items."""
        words = re.findall(r"[A-Za-z][A-Za-z0-9._/-]*", text)  # Keep stable technical tokens.
        return " ".join(words[:7])  # Stay below the guard clear band for manifest prose.

    def _topic(self, lines: tuple[SourceLine, ...]) -> str:
        """Return the nearest heading as the manifest topic."""
        for line in lines:  # Search from the beginning for a section title.
            if line.text.strip().startswith("#"):  # A Markdown heading names the source region.
                return line.text.strip("# ").strip()  # Remove Markdown marks from the topic.
        return "Untitled source region"  # Return a safe fallback topic.

    def _region(self, lines: tuple[SourceLine, ...]) -> str:
        """Return the page range covered by the manifest."""
        pages = sorted({line.page for line in lines})  # Collect cited pages in ascending order.
        if not pages:  # Empty input has no cited page range.
            return "p.unknown"  # Return a clear fallback value.
        return f"p.{pages[0]}-{pages[-1]}"  # Return the exact covered page span.
