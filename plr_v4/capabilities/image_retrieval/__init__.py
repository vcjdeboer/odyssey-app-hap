from plr_v4.capabilities.image_retrieval.backend import ImageRetrievalBackend
from plr_v4.capabilities.image_retrieval.capability import ImageRetrieval


class ImageRetrievalError(Exception):
    """Capability-generic exception for image retrieval failures."""


__all__ = ["ImageRetrievalBackend", "ImageRetrieval", "ImageRetrievalError"]
