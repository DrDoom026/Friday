"""FIRDAY client-side code (PART 12d).

Everything under ``clients/`` is an I/O front-end for the FIRDAY voice
backend (``app.voice``, PARTS 12a-12c) - microphone/speaker, local wake-word
detection, and the WebSocket transport. No FIRDAY business logic (planning,
tool execution, security decisions) lives here; a client only ever forwards
audio to ``/ws/voice`` and speaks back whatever the Pi returns.

These packages are deployed on a *different* machine than the FIRDAY server
in the normal case (a laptop, a phone) and intentionally do not import
``app.*`` - see ``clients/laptop/requirements.txt`` for their own, separate
dependency set.
"""
