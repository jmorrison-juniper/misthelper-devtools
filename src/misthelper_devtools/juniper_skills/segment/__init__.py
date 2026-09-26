"""Segment Juniper source Markdown into bounded topic inputs."""

from misthelper_devtools.juniper_skills.segment.commands import (  # Export command tools.
    CommandBlockDetector,
    CommandDetectionResult,
)
from misthelper_devtools.juniper_skills.segment.lifecycle import (  # Export tag tools.
    LifecycleClassification,
    LifecycleClassifier,
)
from misthelper_devtools.juniper_skills.segment.parts import JoinedDocument, PartSetJoiner  # Export part join tools.
from misthelper_devtools.juniper_skills.segment.repair import (  # Export repair tools.
    DefectRepairResult,
    OrphanWordRepairer,
)
from misthelper_devtools.juniper_skills.segment.segmenter import (
    DocumentSegmenter,
    SegmenterResult,
    TopicSegment,
)  # Export segment tools.
from misthelper_devtools.juniper_skills.segment.subjects import TopicSubjectBuilder  # Export index subject tools.

__all__ = [  # Keep the public import surface explicit for other factory agents.
    "CommandBlockDetector",  # Let callers re-fence command samples before rewriting.
    "CommandDetectionResult",  # Let callers inspect command block counts and text.
    "DefectRepairResult",  # Let callers report measured repair counts.
    "DocumentSegmenter",  # Let callers build contract-sized topic units.
    "JoinedDocument",  # Let callers inspect the ordered source document.
    "LifecycleClassification",  # Let callers inspect life cycle classifier output.
    "LifecycleClassifier",  # Let callers classify segment life cycle stages.
    "OrphanWordRepairer",  # Let tests and callers repair converter line defects.
    "PartSetJoiner",  # Let the inventory agent provide grouped parts.
    "SegmenterResult",  # Let callers read aggregate segmentation metrics.
    "TopicSubjectBuilder",  # Let callers build index subjects.
    "TopicSegment",  # Let callers write topic files with page metadata.
]
