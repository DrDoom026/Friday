"""PART 7: Security / Permission Engine.

The decision layer that sits between Core and every tool execution, replacing the
hardcoded "always refuse" stubs with real authorization decisions based on the
tool's permission metadata, the requesting device's trust level, and policy rules.
"""
