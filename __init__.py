"""Hermes runtime plugin entrypoint for LoopCraft, installed as proofrail."""

try:
    from .proofrail import register
except ImportError:  # pragma: no cover - direct source-tree import fallback
    from proofrail import register

__all__ = ["register"]
