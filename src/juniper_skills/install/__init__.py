"""Install generated Juniper skills into local agent hosts."""

from src.juniper_skills.install.installer import (  # Expose the installer API for the CLI and tests.
    CostReport,
    InstallOutcome,
    SkillCatalogIndex,
    SkillInstaller,
    SkillMetadata,
)

__all__ = [  # Keep the public API explicit for callers.
    "CostReport",
    "InstallOutcome",
    "SkillCatalogIndex",
    "SkillInstaller",
    "SkillMetadata",
]
