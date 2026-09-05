"""FIRDAY laptop voice client (PART 12d).

A local wake-word ("Friday") gates everything: microphone audio never
leaves this machine until the wake word fires, and only the audio captured
during one active utterance is streamed to the Pi - never a continuous
feed. See ``voice_client.VoiceClient`` for the state flow and
``README.md`` for setup.
"""
