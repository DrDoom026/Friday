package com.firday.voice

/**
 * WebSocket transport to the existing `/ws/voice` endpoint (PART 12d
 * foundation). Mirrors `clients/laptop/transport.py`: one endpoint, the
 * existing protocol, never a second one. `server_ws_url` must be a
 * Tailscale address, never a public listener.
 *
 * NOT wired to a real implementation yet - see clients/android/README.md.
 */
sealed class IncomingMessage {
    data class Json(val payload: Map<String, Any?>) : IncomingMessage()
    data class Audio(val pcm16: ByteArray) : IncomingMessage()
}

/** The connection failed to open, or closed while in use. */
class TransportClosedException(message: String) : Exception(message)

interface VoiceTransport {
    @Throws(TransportClosedException::class)
    suspend fun connect()

    /** Safe to call more than once, and even if never connected. */
    suspend fun close()

    @Throws(TransportClosedException::class)
    suspend fun sendJson(message: Map<String, Any?>)

    @Throws(TransportClosedException::class)
    suspend fun sendBytes(data: ByteArray)

    /** Throws [TransportClosedException] when the connection ends. */
    @Throws(TransportClosedException::class)
    suspend fun recv(): IncomingMessage
}
