"""REST API layer exposing the CDSS pipeline over HTTP.

MVP scope: ``/run`` is the batch path (full answer set in, full result out).
The stateful dynamic questionnaire is intentionally deferred.
"""

from cdss.api.app import app
from cdss.api.registry import PipelineRegistry, UnknownVersionError

__all__ = ["app", "PipelineRegistry", "UnknownVersionError"]
