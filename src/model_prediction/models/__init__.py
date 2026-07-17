"""Unified sport models. One model per sport; versioning via config and git tags."""

from .registry import MODEL_SPECS, get_model, model_spec

__all__ = ["MODEL_SPECS", "get_model", "model_spec"]
