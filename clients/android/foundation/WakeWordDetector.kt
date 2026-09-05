package com.firday.voice

/**
 * Local wake-word detection abstraction (PART 12d foundation).
 *
 * Mirrors `clients/laptop/wakeword.py` exactly: nothing in the rest of the
 * client depends on Porcupine directly, and no implementation may fabricate
 * a "no wake" result to hide a detector that cannot actually run.
 *
 * NOT wired to a real implementation yet - see clients/android/README.md.
 */
interface WakeWordDetector {
    /** Exact number of int16 samples [process] expects per call. */
    val frameLength: Int

    /**
     * Returns true if the wake word ("Friday") was detected in [frame]
     * (raw little-endian PCM16 mono).
     *
     * Throws [WakeWordUnavailableException] if the detector cannot run at
     * all (bad config, missing key/model) - never returns false to hide it.
     */
    fun process(frame: ShortArray): Boolean

    /** Releases native resources. Safe to call more than once. */
    fun close() {}
}

/** The detector cannot run - missing access key/model/package. */
class WakeWordUnavailableException(message: String) : Exception(message)
