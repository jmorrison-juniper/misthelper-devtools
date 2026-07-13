"""Data models used by the refactor analyzer package."""

from __future__ import annotations  # Enable modern union/builtin generic annotations.

from dataclasses import dataclass, field  # Dataclasses keep record types concise.

# Category thresholds by reference count; single-source-of-truth for the analyzer.
CATEGORY_UNUSED: str = "unused"  # Zero references outside the def site.
CATEGORY_SINGLE_USE: str = "single-use"  # Exactly one caller anywhere in the graph.
CATEGORY_LOW_USE: str = "low-use"  # 2..3 callers; evaluate before moving.
CATEGORY_HOT: str = "hot"  # 4+ callers; leave alone.
CATEGORY_SKIPPED: str = "skipped"  # Bootstrap/module-load pinned; must stay in entrypoint.


# Guideline-flag identifiers surfaced to SpecKit so the downstream spec can gate them.
FLAG_OVERSIZE: str = "oversize_25_lines"  # Function body exceeds 25 physical lines.
FLAG_TOO_MANY_PARAMS: str = "too_many_params"  # Function signature exceeds 5 parameters.
FLAG_MISSING_INLINE_COMMENTS: str = "missing_inline_comments"  # <50% of code lines have inline #.
FLAG_MISSING_ACTION_LOGGING: str = "missing_action_logging"  # No logging.info/debug calls found.
FLAG_NON_ASCII_LOGS: str = "non_ascii_logs"  # String literal contains non-ASCII bytes.
FLAG_RAW_INPUT: str = "raw_input_call"  # Bare input() call, not safe_input().
FLAG_HARDCODED_SEPARATOR: str = "hardcoded_separator"  # Literal "/" or "\\" in a path context.


@dataclass(frozen=True)
class Definition:
    """A single top-level symbol declared at column 0 in the entrypoint file."""

    name: str  # Public symbol name (function, class, or bound variable).
    kind: str  # One of "function" | "async_function" | "class" | "assignment".
    lineno: int  # 1-based start line of the definition in the entrypoint source.
    end_lineno: int  # 1-based end line of the definition (inclusive).
    line_count: int  # Physical line count of the definition; drives LOC accounting.
    is_private: bool  # True when the name starts with a single underscore.
    decorators: tuple[str, ...]  # Decorator names (best-effort dotted-attribute strings).


@dataclass(frozen=True)
class Reference:
    """A single Name/Attribute node in the module graph that resolves to a Definition."""

    target_name: str  # Definition name this reference points at (post-alias resolution).
    file_path: str  # Absolute or repo-relative path of the file that holds the reference.
    lineno: int  # 1-based line number of the reference site.
    enclosing_symbol: str | None  # Function/class containing the ref; None if module scope.


@dataclass
class Candidate:
    """A definition plus its reference footprint plus SpecKit-ready move guidance."""

    definition: Definition  # The symbol being evaluated for extraction.
    references: list[Reference]  # All references discovered across the module graph.
    category: str  # One of the CATEGORY_* constants above.
    suggested_class: str | None = None  # Semantic class the symbol should land inside.
    suggested_module: str | None = None  # Destination module path (e.g. src/foo/bar.py).
    move_rationale: str | None = None  # Human-readable rationale for the suggestion.
    reference_files: dict[str, list[Reference]] = field(default_factory=dict)  # Refs grouped by file.
    guideline_flags: list[str] = field(default_factory=list)  # Pre-existing guideline violations.


@dataclass
class AnalysisResult:
    """The full output of a refactor analysis run for one entrypoint."""

    entrypoint: str  # Path of the entrypoint file that was analyzed.
    module_graph_size: int  # Count of first-party modules reached during import discovery.
    definitions: list[Definition]  # Every top-level symbol inventoried in the entrypoint.
    candidates: list[Candidate]  # One candidate per definition; sorted by LOC saved.
    loc_saveable: int  # Sum of line_count across unused + single-use candidates.
