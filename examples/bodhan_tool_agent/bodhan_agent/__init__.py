"""Top-level shim so ``python -m bodhan_agent`` works from a clean clone.

The implementation lives in ``src/bodhan_agent`` per the Cookbook recipe
layout. This package exists only to satisfy the assessment's mandated
entrypoint (``python -m bodhan_agent``) without requiring an install step
or ``PYTHONPATH=src``.
"""
