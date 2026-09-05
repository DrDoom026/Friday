# FIRDAY laptop voice client (Part 12d)

A local wake-word ("Friday") daemon for Linux/Omarchy. It is an I/O client
only - no planning, no tool execution, no LLM calls. All of that stays on
the Pi, reached through the existing `/ws/voice` endpoint (Parts 12a-12c).

## Setup

```bash
cd clients/laptop
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create `clients/laptop/.env`:

```bash
FIRDAY_VOICE_WS_URL=wss://<pi-tailscale-name>/ws/voice
FIRDAY_DEVICE_ID=laptop          # must match a device registered/trusted via POST /devices
PORCUPINE_ACCESS_KEY=...         # from https://console.picovoice.ai
PORCUPINE_KEYWORD_PATH=/path/to/friday_linux.ppn   # a custom "Friday" keyword, trained on that console
```

No default voice/keyword ships with FIRDAY - obtain your own Picovoice
access key and train a "Friday" keyword file yourself; nothing is
downloaded automatically. `FIRDAY_VOICE_SAMPLE_RATE` (default `16000`) must
match the server's `STT_SAMPLE_RATE`.

Device identity is **not** re-invented here: `FIRDAY_DEVICE_ID` must name a
device already registered and `TRUSTED` via the existing Part 5 flow
(`POST /devices`, trust derived from Tailscale identity - see the main
project README). Reaching the Pi over Tailscale is necessary but not
sufficient; the device must also be trusted at the application layer.

Run once to test: `python -m clients.laptop`

Run at login (no root, no system-wide changes):

```bash
mkdir -p ~/.config/systemd/user
cp firday-voice-client.service ~/.config/systemd/user/
# edit WorkingDirectory/ExecStart in that file for your checkout path
systemctl --user enable --now firday-voice-client.service
```

## Behavior

- Microphone audio is processed locally by Porcupine and never leaves the
  machine until "Friday" is detected.
- On wake: connects to `/ws/voice`, authenticates via the existing device
  trust check, and drives the server's state machine
  (`wake` → `listening` → `audio.start`/frames/`audio.end`).
- End-of-utterance is a simple configurable silence-RMS threshold
  (`FIRDAY_VOICE_SILENCE_RMS_THRESHOLD`, `FIRDAY_VOICE_SILENCE_DURATION_SECONDS`),
  bounded by `FIRDAY_VOICE_MAX_UTTERANCE_SECONDS`.
- The server's transcript/response text and synthesized audio are logged
  and played back through the default speaker.
- A confirmation-required response (e.g. "The email is waiting for
  confirmation.") is only ever spoken/logged - the client has no code path
  that executes anything locally, regardless of what the response says.
- Reconnect uses bounded exponential backoff (capped at
  `FIRDAY_VOICE_RECONNECT_MAX_SECONDS`, capped attempt count); a
  disconnect mid-utterance returns cleanly to the local wake loop rather
  than crashing the daemon.

## Known limitation

No barge-in: while `RESPONDING` is playing, a new wake word is not yet
handled (12a-12c's protocol has no interruption/cancellation message).
Wait for the response to finish before saying "Friday" again.
