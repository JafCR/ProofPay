"""ProofPay agent package.

Autonomous procurement agent for the All Things Agentic Hackathon. See
``../../docs/SPEC.md`` for the spec that governs this code.

Submodules (``models``, ``settings``, ``policy``, ``state``, ...) are imported
directly, e.g. ``from proofpay import policy``. They are not eagerly imported
here so the package stays importable while later modules are still being built.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
