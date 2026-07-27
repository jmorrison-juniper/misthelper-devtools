"""ASD-STE100 Simplified Technical English compliance linter.

Grades a Markdown or Python file against the Simplified Technical English rules
in ``documentation/ASD-STE100_writing-guide.md`` and reports a score from 0 to
100 percent. See ``specs/1026-ste-linter/`` for the full specification.
"""

# Package-level semantic version. Bump this when scoring or rule semantics change.
__version__ = "1.0.0"  # Read by the CLI --version flag and the JSON report envelope.
