"""PART 9: hybrid LLM layer - local classification (Ollama) + cloud routing
(OmniRoute), gated by a deterministic privacy filter. The LLM never executes
tools; it only produces a :class:`~app.core.models.Plan` for FIRDAY Core to
resolve through the existing Security Engine and Tool Framework.
"""
