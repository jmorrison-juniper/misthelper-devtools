"""Similarity guard for copyright-safe Juniper skill topics."""

from __future__ import annotations  # Keep type annotations from importing at runtime.

import hashlib  # Build stable token hashes for the rolling n-gram index.
import logging  # Record each guard action for operator evidence.
import re  # Normalize prose and detect verbatim command classes.
import time  # Measure guard speed for calibration reports.
from string import punctuation  # Remove punctuation during word normalization.

from .models import SimilarityCheckInput, SimilarityFileResult, SimilarityGuardReport  # Use shared report models.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.

_LOG = logging.getLogger(__name__)  # Give the rewrite guard a stable logger name.


class VerbatimSimilarityGuard:
    """Measure shared prose while ignoring contract-approved verbatim text."""

    def __init__(self, threshold: int = 12, warn_threshold: int = 8) -> None:
        """Store the maximum allowed shared prose run."""
        self.threshold = threshold  # Keep the locked threshold configurable for tests.
        # Assumption: 30 honest samples had a 4-word maximum, so 8 words is the first review band.
        # If production runs create many warnings, measure a larger honest set and move this threshold.
        self.warn_threshold = warn_threshold  # Use 30 samples as the current basis for the review band.
        self._strip = str.maketrans({char: " " for char in punctuation})  # Normalize punctuation as separators.
        self._token_cache: dict[str, int] = {}  # Cache token hashes so large corpus runs stay fast.

    def check(self, checks: tuple[SimilarityCheckInput, ...]) -> SimilarityGuardReport:
        """Return the similarity report for all generated files."""
        logger.info("Starting the verbatim similarity guard")  # Log before the guard measures files.
        started = time.perf_counter()  # Measure elapsed time for the report.
        if not checks:  # A guard that checks zero files must fail by contract.
            return self._empty_report(started)  # Return a failed report with a clear reason.
        results = tuple(self._check_one(check) for check in checks)  # Measure each requested generated file.
        elapsed = time.perf_counter() - started  # Compute elapsed time after all files finish.
        logger.debug("Similarity guard checked %d files in %.3f seconds", len(results), elapsed)  # Log the count.
        return SimilarityGuardReport(  # Return every measurement and band threshold.
            len(results),
            self.threshold,
            self.warn_threshold,
            results,
            elapsed,
        )

    def _empty_report(self, started: float) -> SimilarityGuardReport:
        """Return the contract failure report for an empty input set."""
        elapsed = time.perf_counter() - started  # Measure even the failure path for consistent reports.
        errors = ("The similarity guard checked zero files.",)  # Explain the locked contract failure.
        logger.debug("Similarity guard failed because it checked zero files")  # Log the guard failure reason.
        report = SimilarityGuardReport(  # Build the zero-file failure report.
            0,
            self.threshold,
            self.warn_threshold,
            tuple(),
            elapsed,
            errors,
        )
        return report  # Return a failed report.

    def _check_one(self, check: SimilarityCheckInput) -> SimilarityFileResult:
        """Return the longest shared prose run for one generated file."""
        logger.info("Reading generated topic file %s", check.generated_path)  # Log before file I/O.
        generated_text = check.generated_path.read_text(encoding="utf-8")  # Read the generated topic text.
        logger.debug("Read %d characters from %s", len(generated_text), check.generated_path)  # Log file size.
        generated_words = self.prose_words(generated_text)  # Remove verbatim classes from generated text.
        source_words = self.prose_words("\n".join(check.source_segments))  # Remove verbatim classes from sources.
        longest, phrase = self._longest_common_run(generated_words, source_words)  # Measure shared prose.
        status = self._status(longest)  # Assign cleared, warned, or failed for the report.
        passed = status != "failed"  # Only hard-fail files stop the build.
        logger.debug("Measured %d shared prose words for %s", longest, check.generated_path)  # Log the result.
        return SimilarityFileResult(check.generated_path, longest, phrase, status, passed)  # Return the file result.

    def _status(self, longest: int) -> str:
        """Return the report band for one measured prose run."""
        if longest > self.threshold:  # A run above the hard threshold is copied prose risk.
            return "failed"  # Stop the build for files above 12 words.
        if longest >= self.warn_threshold:  # A run in the margin band needs human review.
            return "warned"  # Let the build continue and record the file.
        return "cleared"  # The file sits below the measured review band.

    def prose_words(self, text: str) -> tuple[str, ...]:
        """Return normalized prose words with verbatim classes removed."""
        logger.info("Normalizing prose for the similarity guard")  # Log before text normalization.
        lines = self._prose_lines(text)  # Drop fences, commands, tables, and output rows.
        words = tuple(word for line in lines for word in self._line_words(line))  # Normalize remaining prose words.
        logger.debug("Normalized prose to %d guard words", len(words))  # Log the guard token count.
        return words  # Return the filtered words for n-gram indexing.

    def _prose_lines(self, text: str) -> tuple[str, ...]:
        """Return lines that can contain prose subject to the copyright guard."""
        kept: list[str] = []  # Collect prose lines after verbatim filtering.
        fenced = False  # Track fenced code blocks so command blocks pass through.
        command_context = False  # Track command output rows after a detected command.
        for raw_line in text.splitlines():  # Walk the document in order.
            line = raw_line.strip()  # Normalize edge whitespace for pattern checks.
            fenced, command_context, accepted = self._classify_line(line, fenced, command_context)  # Classify the line.
            if accepted:  # Only prose lines are eligible for the guard.
                kept.append(line)  # Keep the prose line for word normalization.
        return tuple(kept)  # Return immutable lines to prevent accidental edits.

    def _classify_line(self, line: str, fenced: bool, command_context: bool) -> tuple[bool, bool, bool]:
        """Return updated state and whether the line is guardable prose."""
        if line.startswith("```"):  # A fenced block is an approved verbatim class.
            return not fenced, False, False  # Toggle the fence and never grade the fence marker.
        if fenced or not line or self._is_markdown_table(line):  # Code, blanks, and tables are not prose runs.
            return fenced, False, False  # Skip non-prose lines and reset command output context on blanks.
        if self._is_command_line(line):  # Commands and configuration lines must stay verbatim.
            return fenced, True, False  # Start command-output context for following rows.
        if command_context and self._is_command_output(line):  # Output rows belong to the prior command.
            return fenced, True, False  # Keep skipping command output until prose resumes.
        return fenced, False, True  # Treat the line as guardable prose.

    def _line_words(self, line: str) -> tuple[str, ...]:
        """Return normalized non-verbatim words from one prose line."""
        without_citation = re.sub(r"\[[^\]]+\]", " ", line)  # Remove citation keys because they are identifiers.
        without_mark = re.sub(r"\*\*(MUST|SHOULD|INFO)\*\*", " ", without_citation)  # Remove card marks.
        scrubbed = without_mark.translate(self._strip).lower()  # Fold case and punctuation before comparison.
        raw_words = re.findall(r"[a-z0-9][a-z0-9._/-]*", scrubbed)  # Extract comparable word tokens.
        return tuple(word for word in raw_words if not self._is_verbatim_token(word))  # Drop safe verbatim tokens.

    def _longest_common_run(self, left: tuple[str, ...], right: tuple[str, ...]) -> tuple[int, str]:
        """Return the longest common consecutive word run using an n-gram index."""
        high = min(len(left), len(right))  # Bound the binary search by the shorter token stream.
        best = (0, "")  # Store the best length and phrase found so far.
        low = 1  # Start at one word so zero-length files return the default.
        while low <= high:  # Binary search because common runs are monotonic by length.
            middle = (low + high) // 2  # Probe the midpoint n-gram length.
            phrase = self._shared_phrase(left, right, middle)  # Look for a shared n-gram at this length.
            if phrase:  # A shared run of this length exists.
                best = (middle, phrase)  # Keep the candidate as the current best.
                low = middle + 1  # Search for a longer shared run.
            else:  # No shared run exists at this length.
                high = middle - 1  # Search the shorter half.
        return best  # Return the exact longest run and one matching phrase.

    def _shared_phrase(self, left: tuple[str, ...], right: tuple[str, ...], size: int) -> str:
        """Return one shared phrase for a fixed n-gram size, or an empty string."""
        if size <= 0 or len(left) < size or len(right) < size:  # Invalid windows cannot match.
            return ""  # Return no match for empty or oversized probes.
        left_index = self._window_index(left, size)  # Build a rolling-hash index for generated n-grams.
        for index, digest in enumerate(self._rolling_hashes(right, size)):  # Scan source n-grams once.
            phrase = self._verified_phrase(left, right, left_index.get(digest, ()), index, size)  # Check collisions.
            if phrase:  # A real shared phrase survived collision verification.
                return phrase  # Return one phrase for the report.
        return ""  # Return no match when the fixed-size scan finds none.

    def _window_index(self, words: tuple[str, ...], size: int) -> dict[int, tuple[int, ...]]:
        """Return a rolling-hash index from digest to start offsets."""
        index: dict[int, list[int]] = {}  # Collect offsets by digest before freezing the values.
        for offset, digest in enumerate(self._rolling_hashes(words, size)):  # Hash each generated window once.
            index.setdefault(digest, []).append(offset)  # Keep offsets so collisions can be checked.
        return {digest: tuple(offsets) for digest, offsets in index.items()}  # Freeze offsets for safe reuse.

    def _rolling_hashes(self, words: tuple[str, ...], size: int) -> tuple[int, ...]:
        """Return stable rolling hashes for all fixed-size windows."""
        mask = (1 << 64) - 1  # Keep arithmetic inside an unsigned 64-bit space.
        base = 1_000_003  # Use a large odd base for a low collision rate.
        power = pow(base, size - 1, 1 << 64)  # Precompute the high-place multiplier.
        values = tuple(self._token_hash(word) for word in words)  # Convert words to stable integers.
        digest = self._initial_hash(values, size, base, mask)  # Hash the first window.
        hashes = [digest]  # Store the first window hash.
        for offset in range(1, len(values) - size + 1):  # Roll across the remaining windows.
            digest = ((digest - values[offset - 1] * power) * base + values[offset + size - 1]) & mask  # Roll hash.
            hashes.append(digest)  # Store the next window hash.
        return tuple(hashes)  # Return immutable hashes for repeatable scans.

    def _initial_hash(self, values: tuple[int, ...], size: int, base: int, mask: int) -> int:
        """Return the hash for the first fixed-size window."""
        digest = 0  # Start from zero for the polynomial hash.
        for value in values[:size]:  # Add each token in the first window.
            digest = ((digest * base) + value) & mask  # Keep the hash in 64-bit space.
        return digest  # Return the first window hash.

    def _verified_phrase(
        self,
        left: tuple[str, ...],
        right: tuple[str, ...],
        offsets: tuple[int, ...],
        right_offset: int,
        size: int,
    ) -> str:
        """Return a phrase when a rolling hash match is exact."""
        right_window = right[right_offset : right_offset + size]  # Slice the source window for collision checks.
        for left_offset in offsets:  # Check every generated window with the same digest.
            if left[left_offset : left_offset + size] == right_window:  # Verify the hash match with exact words.
                return " ".join(right_window)  # Return the shared phrase for the report.
        return ""  # Return no phrase when all digest matches were collisions.

    def _token_hash(self, word: str) -> int:
        """Return a stable 64-bit hash for one normalized word."""
        if word not in self._token_cache:  # Hash each distinct token one time per guard instance.
            digest = hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest()  # Build a stable digest.
            self._token_cache[word] = int.from_bytes(digest, "little")  # Store the digest as an integer.
        return self._token_cache[word]  # Return the cached token hash.

    def _is_markdown_table(self, line: str) -> bool:
        """Return whether a line is a Markdown table or table divider."""
        return line.startswith("|") and line.endswith("|")  # Tables are structured content, not prose paragraphs.

    def _is_command_line(self, line: str) -> bool:
        """Return whether a line is a command or configuration line."""
        prompt = re.search(r"\b[\w.-]+@[\w.-]+[>#]\s*\S+", line)  # Detect Junos prompts with commands.
        command = re.match(r"^(set|delete|show|run|commit|edit|request|clear|ping|traceroute)\b", line)  # Detect CLI.
        hierarchy_tokens = r"^(interfaces|protocols|policy-options|routing-options|security)\b"  # Name config roots.
        hierarchy = re.match(hierarchy_tokens, line)  # Detect hierarchy-style configuration lines.
        return bool(prompt or command or hierarchy)  # Treat any command signal as verbatim text.

    def _is_command_output(self, line: str) -> bool:
        """Return whether a line is likely command output after a command."""
        if re.search(r"\b(ge|xe|et)-\d+/\d+/\d+(\.\d+)?\b|\birb\.\d+\b|\blo0\b", line):  # Detect interfaces.
            return True  # Interface rows are command output or configuration identifiers.
        if re.search(r"\b(up|down|inet|inet6|enabled|disabled)\b", line.lower()):  # Detect status/output words.
            return True  # Status rows after commands are approved verbatim output.
        return bool(re.match(r"^[A-Za-z0-9_.:/-]+(\s{2,}|\t).+", line))  # Column-aligned rows are output.

    def _is_verbatim_token(self, word: str) -> bool:
        """Return whether a token belongs to a verbatim class."""
        checks = (self._is_numeric_limit(word), self._is_model(word), self._is_port(word), self._is_protocol(word))
        return any(checks) or self._is_identifier(word)  # Exclude tokens that must not be rewritten.

    def _is_numeric_limit(self, word: str) -> bool:
        """Return whether a token is a numeric value or numeric limit."""
        return bool(re.search(r"\d", word))  # Numeric data must stay exact.

    def _is_model(self, word: str) -> bool:
        """Return whether a token looks like a device model or standard name."""
        model = re.match(r"^(ex|qfx|srx|mx|ptx|acx|ap|bt)[0-9][a-z0-9.-]*$", word)  # Require a model number.
        standard = re.match(r"^(ieee|iso|tia|ietf|rfc)[a-z0-9.-]*$", word)  # Keep named standards exact.
        return bool(model or standard)  # Keep models and standards unchanged.

    def _is_port(self, word: str) -> bool:
        """Return whether a token is a Junos interface or port name."""
        return bool(re.match(r"^(ge|xe|et|em|fxp|irb|lo|ae)-?[0-9/.:]*$", word))  # Keep interface identifiers.

    def _is_protocol(self, word: str) -> bool:
        """Return whether a token names a standard protocol."""
        protocols = {"bgp", "ospf", "isis", "mpls", "evpn", "vxlan", "stp", "lldp", "snmp", "dhcp"}  # Name protocols.
        return word in protocols  # Keep protocol names unchanged.

    def _is_identifier(self, word: str) -> bool:
        """Return whether a token is likely an identifier rather than prose."""
        return "_" in word or "/" in word or "." in word or "-" in word  # Identifiers use punctuation inside tokens.
