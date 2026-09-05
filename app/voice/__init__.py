"""FIRDAY voice: session transport (12a) + STT (12b) + TTS (12c).

Device trust is reused unchanged from Part 5 (``app.devices``) - this
package adds nothing to *who* is trusted, only what a trusted device's voice
session may do once connected. No wake word (12d) or dashboard/entity wiring
(12e) live here yet.

Lifecycle, one utterance::

    IDLE -> WAKE -> LISTENING            (app.voice.protocol / voice.state)
      -> audio.start / binary frames / audio.end   (app.voice.audio)
      -> THINKING -> STT -> transcript              (app.voice.stt, app.voice.faster_whisper_stt)
      -> existing FIRDAY Core /request path         (app.voice.pipeline._run_through_core)
      -> RESPONDING -> TTS -> audio streamed back    (app.voice.tts, app.voice.piper_tts)
      -> IDLE

``app.voice.pipeline`` is the only module that calls ``Core.handle`` or a
``TextToSpeech``/``SpeechToText`` implementation - it never touches a named
tool, the registry, or an LLM provider directly.

Wire protocol (JSON messages unless noted), client -> server:
    session.start / session.end / voice.state / voice.pause / voice.resume / voice.end
    audio.start {format:"pcm16", sample_rate, channels:1}
    <binary frames>                    -- raw mono PCM16 audio
    audio.end

server -> client:
    session.accepted / session.ended / voice.state.accepted / error
    voice.transcript {session_id, text}
    voice.response {text}                          -- Core's final answer
    voice.response.start {session_id, utterance_id, encoding, sample_rate, channels}
    <binary frames>                                 -- raw mono PCM16 audio (voice.response.start's format)
    voice.response.end {session_id, utterance_id, status}

Canonical audio format, both directions: mono, 16-bit signed little-endian
PCM ("pcm16"). Incoming sample rate is fixed by ``STT_SAMPLE_RATE`` (default
16000, matching faster-whisper); outgoing sample rate is fixed by
``TTS_SAMPLE_RATE`` (default 22050) and must match the configured Piper
voice model.

Pi runtime setup for 12b/12c: install ``faster-whisper``/``numpy`` and
``piper-tts`` (``requirements.txt``; aarch64 wheels available for both), set
``TTS_MODEL_PATH`` to an operator-downloaded Piper ``.onnx`` voice (FIRDAY
never downloads one automatically), and set ``TTS_SAMPLE_RATE`` to that
voice's sample rate. Both models load lazily, once, on first use - importing
this package or starting the app never requires either package installed;
an unconfigured/missing model fails one utterance with a structured
``STT_FAILED``/``TTS_FAILED`` error, not a crash.
"""
