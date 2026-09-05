# FIRDAY Android voice client — foundation (Part 12d)

**Status: architecture/interfaces only, not a buildable app.** There is no
Gradle project here on purpose - standing one up (SDK, build tooling,
signing, an emulator/device to actually run it on) is disproportionate to
what this milestone needs and untestable in this environment. What exists
is the same contract the laptop client implements, translated to Kotlin, so
a real Android Studio project can be started from a stable design rather
than from scratch.

See `foundation/` for the interfaces: `WakeWordDetector`, `VoiceTransport`,
`VoiceClient` - deliberately mirroring `clients/laptop/wakeword.py`,
`transport.py`, and `voice_client.py` one-to-one. Same protocol, same
device-identity model, same "client is I/O only" boundary: nothing here
plans, executes a tool, or calls an LLM - that stays on the Pi.

## Wire protocol

Identical to the laptop client - `/ws/voice`, `session.start` with
`device_id`/`client:"android"`/`protocol_version`, the same `voice.state`
walk, the same `audio.start`/binary frames/`audio.end`, the same
`voice.transcript`/`voice.response`/`voice.response.start`/binary/`voice.response.end`.
No `/ws/android` - one endpoint, one protocol, for every client.

## Device identity

Reuses Part 5 device trust exactly as the laptop client does: the phone
registers itself via `POST /devices` with a **stable** `device_id`, and
trust is derived from Tailscale identity on that call - not from a
shared secret embedded in the app, and not from "the request arrived over
Tailscale" alone. The stable id itself should live in Android's
`EncryptedSharedPreferences` (Jetpack Security) once a real project exists,
never in a plain preference file or in source.

## Background wake-word: the honest limitation

Android does not let an arbitrary third-party app keep a microphone open
indefinitely in the background across all OEM/battery-management
configurations - a persistent foreground service is the supported way to
keep a mic-using service alive past the app being backgrounded, and even
that is fenced by `RECORD_AUDIO` + (for Android 14+) the
`FOREGROUND_SERVICE_MICROPHONE` type, plus battery-optimization allowlisting
requests the user can decline or Doze can still constrain.

For this milestone: **foreground-only voice operation is the supported
design.** The wake word runs, and the mic is used, only while the app is in
the foreground (or, once implemented, in an explicit, user-visible
foreground service with a persistent notification - "FIRDAY is listening").
There is no code path that claims always-on background listening; doing so
would be a privacy misrepresentation, not a missing feature. A foreground
`HotwordDetectionService` (Porcupine's Android SDK supports this shape) is
the natural next step, not implemented here.

## What's next (not this milestone)

- An actual Gradle/Android Studio project.
- Porcupine's Android SDK wired behind `WakeWordDetector`.
- A foreground service for background-tolerant listening, with a visible
  notification while active.
- `OkHttp`/`kotlinx.coroutines`-based `VoiceTransport` over WebSocket.
- Playback via `AudioTrack`, capture via `AudioRecord`, both at the same
  canonical mono PCM16 contract the server and laptop client use.
