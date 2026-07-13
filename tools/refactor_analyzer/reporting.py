"""Render AnalysisResult as a SpecKit-consumable Markdown report."""

from __future__ import annotations  # Enable modern annotation syntax.

import logging  # Module-scoped logger for action logging.
from pathlib import Path  # Portable filesystem path handling.

from tools.refactor_analyzer.models import (  # Data models rendered here.
    CATEGORY_HOT,
    CATEGORY_LOW_USE,
    CATEGORY_SINGLE_USE,
    CATEGORY_SKIPPED,
    CATEGORY_UNUSED,
    AnalysisResult,
    Candidate,
)

logger = logging.getLogger(__name__)  # Module-scoped logger for action logging.


class MarkdownReportGenerator:
    """Turn an AnalysisResult into a Markdown string ready for `speckit.specify`."""

    def generate(self, result: AnalysisResult) -> str:
        """Assemble the full report from ordered section builders."""
        logger.info("Generating markdown report for %s", result.entrypoint)  # Log before rendering.
        sections = [  # Ordered list of section fragments for the final report.
            self._header(result),  # Top-level metadata and category counts.
            self._how_to_read(),  # Explain the prioritization scheme up front.
            self._speckit_directives(),  # Non-negotiable SpecKit rules for downstream refactors.
            self._summary_table(result.candidates),  # One-row-per-candidate overview.
            self._detail_sections(result.candidates),  # Per-category deep-dive.
            self._limitations_footer(),  # What the tool cannot detect.
        ]
        logger.debug("Report assembled from %d sections", len(sections))  # Post-log the section count.
        body = "\n\n".join(section for section in sections if section)  # Join with blank-line spacing.
        return f"{body}\n"  # Ensure a single trailing newline per markdownlint MD047.

    @staticmethod
    def _header(result: AnalysisResult) -> str:
        """Return the top-level metadata block with counts by category."""
        counts = {
            CATEGORY_UNUSED: 0,
            CATEGORY_SINGLE_USE: 0,
            CATEGORY_LOW_USE: 0,
            CATEGORY_HOT: 0,
            CATEGORY_SKIPPED: 0,
        }  # Init.
        for candidate in result.candidates:  # Count candidates per category.
            counts[candidate.category] = counts.get(candidate.category, 0) + 1  # Increment bucket.
        entrypoint_name = Path(result.entrypoint).name  # Short name for readability.
        lines = [  # Assemble the header lines.
            f"# Refactor candidates: {entrypoint_name}",  # Report title.
            "",  # Blank line before metadata.
            f"- Entrypoint: `{result.entrypoint}`",  # Full path for tooling.
            f"- Module graph size: {result.module_graph_size} first-party files",  # Reach of the analysis.
            f"- Definitions analyzed: {len(result.definitions)}",  # Total defs considered.
            f"- LOC saveable (unused + single-use): {result.loc_saveable}",  # Movable-line total.
            f"- Category counts: unused={counts[CATEGORY_UNUSED]}, "  # Split for readability.
            f"single-use={counts[CATEGORY_SINGLE_USE]}, "
            f"low-use={counts[CATEGORY_LOW_USE]}, hot={counts[CATEGORY_HOT]}, "
            f"skipped={counts[CATEGORY_SKIPPED]}",
        ]
        return "\n".join(lines)  # Return the joined header string.

    @staticmethod
    def _how_to_read() -> str:
        """Return the priority-explanation block so operators know which files to touch first."""
        return "\n".join(  # Multi-line prioritization guide joined for markdown output.
            [
                "## How to read this report",  # Section header.
                "",  # Blank line for readability.
                "Work the report **top-down inside each category**, then move to the next category:",
                "",  # Blank line before the ordered list.
                "1. **Unused** -- zero references. Delete outright; no move, no callsite rewrite. Highest ROI per PR.",
                "2. **Single-use** -- exactly one caller. Move alongside that caller (or into a new `/src` module when the entrypoint is the sole caller). One PR covers move + rewrite.",
                "3. **Low-use** -- 2-3 callers. Evaluate before moving: worth it only when all callers can be rewritten in one bounded PR cluster.",
                "4. **Hot** -- 4+ callers. Leave in place until dependencies decouple. Listed for completeness only.",
                "5. **Skipped** -- pinned by bootstrap/module-load ordering (e.g. `GlobalImportManager`). DO NOT extract; the tool cannot detect load-order dependencies, so these are curated by hand via the `--skip NAME` CLI flag.",
                "",  # Blank line before secondary rules.
                "Within each bucket, candidates are sorted by **line_count descending** so the biggest LOC wins surface first. The `LOC saveable` headline in the metadata block sums unused + single-use lines only -- that number is your extraction budget for this pass.",
                "",  # Blank line before reference-cluster note.
                "Reference sites are grouped **per file** so each candidate maps cleanly to one PR per reference-holding file (move + rewrite in the same PR). When multiple single-use candidates share the same dominant caller (see `Suggested class`), bundle them into one PR that lands them in the same class body.",
            ]
        )

    @staticmethod
    def _speckit_directives() -> str:
        """Return the fixed instruction block SpecKit workflows must obey."""
        return "\n".join(  # Multi-line directive block joined for markdown output.
            [
                "## SpecKit non-negotiables",  # Section header.
                "",  # Blank line for readability.
                "1. **No wrapper shims**: do NOT create `def old(...): return NewClass().new(...)` "
                "or thin re-export modules. Move the code into a semantic class body and delete the old symbol.",
                "2. **Rewrite every callsite**: for each candidate below, produce one PR per reference-holding "
                "file cluster. Every listed `file:lineno` must be updated in the same PR as the move.",
                "3. **Decompose while moving**: if a candidate lists `guideline_flags`, do NOT lift-and-shift. "
                "Split into <=25-line methods with <=5 params, add inline comments on every executable line, "
                "and add `logging.info/debug` before/after every operation.",
                "4. **Landing target is a class body**: `Suggested class` names the destination. Prefer an "
                "existing class (`WebSocketManager`, `FirmwareManager`, `SFPTransceiverDataProcessor`, "
                "`EnhancedSSHRunner`, etc.) when one already lives in the target module; otherwise create the "
                "proposed new class rather than adding a bare module-level function.",
                "5. **ASCII-only logs, `safe_input()`, `pathlib.Path`**: any candidate flagged for non-ASCII "
                "literals, raw `input()`, or hardcoded separators must be cleaned up during the move.",
            ]
        )

    def _summary_table(self, candidates: list[Candidate]) -> str:
        """Return a single markdown table with one row per candidate."""
        header = "| Name | Kind | Lines | Refs | Category | Suggested class | Flags |"  # Column headings.
        separator = "|---|---|---:|---:|---|---|---|"  # Alignment row for markdown table syntax.
        rows = [self._summary_row(candidate) for candidate in candidates]  # One row per candidate.
        return "\n".join(["## Summary", "", header, separator, *rows])  # Full table with a section header.

    @staticmethod
    def _summary_row(candidate: Candidate) -> str:
        """Format one Candidate as a markdown table row."""
        flags = ",".join(candidate.guideline_flags) if candidate.guideline_flags else ""  # Compact flag list.
        suggested = candidate.suggested_class or ""  # Empty string when no class was suggested.
        return (  # Build the row string; use inline backticks for name to avoid markdown clash.
            f"| `{candidate.definition.name}` | {candidate.definition.kind} | "
            f"{candidate.definition.line_count} | {len(candidate.references)} | "
            f"{candidate.category} | {suggested} | {flags} |"
        )

    def _detail_sections(self, candidates: list[Candidate]) -> str:
        """Return one section per category, with per-candidate detail rendered inside."""
        buckets: dict[str, list[Candidate]] = {  # Pre-seed with ordered category keys.
            CATEGORY_UNUSED: [],
            CATEGORY_SINGLE_USE: [],
            CATEGORY_LOW_USE: [],
            CATEGORY_HOT: [],
            CATEGORY_SKIPPED: [],
        }
        for candidate in candidates:  # Bucket candidates by category.
            buckets.setdefault(candidate.category, []).append(candidate)  # Append into the bucket.
        rendered = []  # Accumulate rendered fragments.
        for category, items in buckets.items():  # Iterate in the fixed order defined above.
            if not items:  # Skip empty categories for a cleaner report.
                continue  # Nothing to render.
            rendered.append(self._category_section(category, items))  # Render this category.
        return "\n\n".join(rendered)  # Blank line between category sections.

    def _category_section(self, category: str, candidates: list[Candidate]) -> str:
        """Render one category header plus every candidate inside it."""
        heading = f"## {category.title()} ({len(candidates)})"  # Section heading with count.
        body = "\n\n".join(self._candidate_detail(candidate) for candidate in candidates)  # Per-cand bodies.
        return f"{heading}\n\n{body}"  # Section header followed by candidate bodies.

    def _candidate_detail(self, candidate: Candidate) -> str:
        """Render one candidate as a bullet block with def, class, refs, and flags."""
        defn = candidate.definition  # Cached alias for readability.
        header = f"### `{defn.name}` ({defn.kind}, {defn.line_count} lines)"  # Sub-section header.
        lines = [  # Bullet list of key facts + refs.
            header,
            "",  # Blank line before bullets.
            f"- Def site: line {defn.lineno}-{defn.end_lineno}",  # Where the symbol is defined.
            f"- References: {len(candidate.references)}",  # Total refs found.
            (
                f"- Suggested class: `{candidate.suggested_class}`"
                if candidate.suggested_class
                else "- Suggested class: _n/a_"
            ),
            (
                f"- Suggested module: `{candidate.suggested_module}`"
                if candidate.suggested_module
                else "- Suggested module: _n/a_"
            ),
            f"- Rationale: {candidate.move_rationale}" if candidate.move_rationale else "- Rationale: _n/a_",
        ]
        if candidate.guideline_flags:  # Only render checklist when flags exist.
            lines.append(self._flag_checklist(candidate.guideline_flags))  # Bullet checklist of flags.
        if candidate.reference_files:  # Only render call-site groupings if we have any.
            lines.append(self._reference_groups(candidate.reference_files))  # File-grouped refs.
        return "\n".join(lines)  # Return the rendered block.

    @staticmethod
    def _flag_checklist(flags: list[str]) -> str:
        """Render guideline flags as a markdown checklist for SpecKit remediation."""
        header = "- Guideline flags (address during the move):"  # Section bullet.
        items = "\n".join(f"  - [ ] {flag}" for flag in flags)  # One checkbox per flag.
        return f"{header}\n{items}"  # Return the assembled checklist.

    @staticmethod
    def _reference_groups(reference_files: dict[str, list]) -> str:
        """Render references grouped by file, one sub-bullet per file with linenos."""
        header = "- Reference sites (one PR cluster per file):"  # Section bullet.
        rows = []  # Accumulate rows.
        for file_path, refs in sorted(reference_files.items()):  # Deterministic ordering.
            linenos = ", ".join(str(r.lineno) for r in refs)  # Comma-joined lineno list.
            rows.append(f"  - `{file_path}`: lines {linenos}")  # One sub-bullet per file.
        return "\n".join([header, *rows])  # Return the assembled bullet block.

    @staticmethod
    def _limitations_footer() -> str:
        """Return the documented limitations block at the bottom of the report."""
        return "\n".join(  # Multi-line footer joined for markdown output.
            [
                "## Limitations",  # Section header.
                "",  # Blank line before content.
                '- `getattr(module, "name")` string-form lookups are not detected.',  # Dynamic lookups miss.
                '- Class-registration decorators (`@registry.register("foo")`) with literal-string wiring '
                "are invisible to static analysis.",
                "- Runtime `importlib` / plugin discovery is not followed.",
                "- Because `src/` files rarely `from MistHelper import ...`, external ref counts are near zero "
                "by design; the tool primarily surfaces intra-entrypoint single-use symbols that can be moved "
                "alongside their sole caller into `src/`.",
                "- Constants inside `if TYPE_CHECKING:` or other conditional module-scope blocks are skipped.",
            ]
        )
