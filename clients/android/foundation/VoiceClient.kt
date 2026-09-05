package com.firday.voice

/**
 * The Android voice client's orchestration contract (PART 12d foundation).
 *
 * Mirrors `clients/laptop/voice_client.py`'s flow exactly - same protocol,
 * same server-authoritative state machine, same "I/O only" boundary: this
 * client never plans, executes a tool, or calls an LLM directly. It speaks
 * `session.start` → `voice.state` (wake, listening) → `audio.start`/frames/
 * `audio.end` → `voice.transcript`/`voice.response` → `voice.response.start`/
 * audio/`voice.response.end`, then returns to local wake-word waiting.
 *
 * Foreground-only for this milestone - see clients/android/README.md for
 * why true background always-on listening is not implemented.
 *
 * NOT wired to a real implementation yet.
 */
data class VoiceClientConfig(
    val serverWsUrl: String,
    /** Must name a device already registered/trusted via POST /devices. */
    val deviceId: String,
    val sampleRate: Int = 16000,
    val silenceRmsThreshold: Int = 500,
    val silenceDurationMs: Long = 1200,
    val maxUtteranceMs: Long = 30_000,
    val reconnectBaseMs: Long = 1_000,
    val reconnectMaxMs: Long = 30_000,
)

interface VoiceClient {
    /** Local wake-word loop → one utterance → back to the wake loop, forever. */
    suspend fun runForever()
}
