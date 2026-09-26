"""Module-level symbol table comparison for an automated sweep.

Reports the module-level names that a change lost and the names that it added.
An automated comment sweep must delete comment lines only. A sweep that deletes
a declaration still compiles in many cases, so no gate reports it. This tool
reports it. See ``specs/1796-comment-sweep-safety/`` for the full specification.
"""

# Package-level semantic version. Bump this when the report format changes.
__version__ = "1.0.0"  # Read by the CLI --version flag and by the report header.
