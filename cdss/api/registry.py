from __future__ import annotations

from pathlib import Path

from cdss.knowledge import validate
from cdss.knowledge.models import KnowledgeGraph, ValidationReport
from cdss.pipeline import CDSSPipeline

# Resolve the knowledge_graph directory relative to the repository root so the
# API works regardless of the current working directory uvicorn is launched
# from.  cdss/api/registry.py -> parents[2] == repository root.
DEFAULT_KG_ROOT = Path(__file__).resolve().parents[2] / "knowledge_graph"


class UnknownVersionError(LookupError):
    """Raised when a requested knowledge graph version does not exist."""

    def __init__(self, version: str, available: list[str]) -> None:
        self.version = version
        self.available = available
        super().__init__(f"Unknown knowledge graph version '{version}'")


class PipelineRegistry:
    """Loads and caches one CDSSPipeline per knowledge graph version.

    A pipeline construction reads YAML from disk and runs full validation, so it
    is expensive.  The registry builds each version once and reuses it for every
    request.  Pipelines are stateless, so a single cached instance is safe to
    share across concurrent requests.
    """

    def __init__(self, knowledge_graph_root: str | Path = DEFAULT_KG_ROOT) -> None:
        self._root = Path(knowledge_graph_root)
        self._pipelines: dict[str, CDSSPipeline] = {}
        self._reports: dict[tuple[str, str], ValidationReport] = {}

    def versions(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(path.name for path in self._root.iterdir() if path.is_dir())

    def has_version(self, version: str) -> bool:
        return (self._root / version).is_dir()

    def pipeline(self, version: str) -> CDSSPipeline:
        if version not in self._pipelines:
            if not self.has_version(version):
                raise UnknownVersionError(version, self.versions())
            self._pipelines[version] = CDSSPipeline.from_version(version, self._root)
        return self._pipelines[version]

    def knowledge_graph(self, version: str) -> KnowledgeGraph:
        return self.pipeline(version).kg

    def validation_report(self, version: str, profile: str = "prototype") -> ValidationReport:
        """Validate a cached knowledge graph under the requested profile.

        Cached per (version, profile).  The pipeline's own report is always the
        default profile, so profile-specific validation is computed here against
        the already-loaded graph rather than re-reading from disk.  Raises
        ValueError for an unknown profile (surfaced by the API as a 400).
        """
        key = (version, profile)
        if key not in self._reports:
            kg = self.knowledge_graph(version)
            self._reports[key] = validate(kg, profile=profile)
        return self._reports[key]
