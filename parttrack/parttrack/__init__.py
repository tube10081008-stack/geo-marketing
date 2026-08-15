"""parttrack — batch generator for choral/musical part-practice tracks.

Given one score, produce a full set of per-part practice audio and scrolling
piano-roll videos plus ready-to-paste publishing metadata. The score is the
single source of truth: nothing in the pipeline invents or re-harmonises notes.
"""

from .config import PartConfig, ProjectConfig, RenderConfig, VideoConfig
from .mixdown import MixSpec, build_mix, plan_mixes
from .score import Score, load_score

__version__ = "0.1.0"

__all__ = [
    "PartConfig",
    "ProjectConfig",
    "RenderConfig",
    "VideoConfig",
    "MixSpec",
    "Score",
    "build_mix",
    "plan_mixes",
    "load_score",
    "__version__",
]
