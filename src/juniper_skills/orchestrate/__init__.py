"""Orchestrate the Juniper skill factory pipeline."""

from src.juniper_skills.orchestrate.agent_backend import (  # Export the rewrite backend chooser.
    BackendDiscovery,
    PacketFileBackend,
    SubprocessAgentBackend,
)
from src.juniper_skills.orchestrate.git_store import CanonicalSkillStore  # Export canonical store commits.
from src.juniper_skills.orchestrate.journal import OrchestratorJournal  # Export fine-grained stage tracking.
from src.juniper_skills.orchestrate.models import BackendProbe  # Export backend probe evidence for callers.
from src.juniper_skills.orchestrate.package import SkillPackageEmitter  # Export package file generation.
from src.juniper_skills.orchestrate.pipeline import PipelineRunner  # Export one-document pipeline execution.
from src.juniper_skills.orchestrate.queue import WorkLeaseStore  # Export atomic work leasing.
from src.juniper_skills.orchestrate.runner import FactoryRunConfig, FactoryRunner  # Export long-run control.

__all__ = [  # Keep the public surface explicit for other factory agents.
    "BackendDiscovery",
    "BackendProbe",
    "CanonicalSkillStore",
    "FactoryRunConfig",
    "FactoryRunner",
    "OrchestratorJournal",
    "PacketFileBackend",
    "PipelineRunner",
    "SkillPackageEmitter",
    "SubprocessAgentBackend",
    "WorkLeaseStore",
]
