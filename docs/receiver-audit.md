# Receiver evidence and local input levels

The **Project and devices** page distinguishes a read-only receiver inspection
from a measured signal-processing result. It does not change receiver settings,
play a test tone, enter calibration mode or select a capture receiver.

## What the inspection reads

The native information path uses the existing identity/status reads and optional
exact-IP identity lookup. The web path reads the actual speaker menu first, then
only the exposed basic speaker settings. It stops the optional settings sequence
after the first failure. It never sweeps endpoint numbers, requests firmware
updates or queries feature pages merely because a shared frontend names them.

A menu can explicitly report an item as unavailable, disabled or selectable.
Unknown values, timeouts and unqueried controls remain unknown. For example,
common firmware JavaScript can contain a Dirac setup handler while the current
receiver menu marks that item unavailable. The string's presence does not unlock
a DSP feature or license.

Separate SW1/SW2 stored level/distance fields are evidence of readable parameters.
They do not prove independent electrical gain/delay or separately writable EQ.
Those require controlled normal-playback measurements and a recoverable baseline.
Mixed-phase reconstruction and arbitrary support routing also remain unverified
by this inspection. The report never changes `native_playback_verified` or
`independent_subwoofer_filters_verified` to true from menu data alone.

The report records the HEOS component version when available; it does not label
that value as the complete receiver firmware version. Meter-to-feet display
conversion metadata likewise does not establish the DSP distance/delay law.

Each successful audit is archived under `diagnostics/` and cached as
`receiver-audit.json`. Its observation time and integrity hash remain visible.
Failed native reads retain the previous report. Restarting restores saved
evidence without making a network request. Current raw settings are retained in
the downloadable private JSON; existing measurement references are not replaced.

## Five-second input-level checks

On Windows, **List recording inputs** enumerates device names without capturing
audio. Choose one input and explicitly allow a five-second check before starting.
If automatic selection finds multiple microphone inputs, it stops instead of
silently choosing one.

The check uses a bounded input buffer and discards the samples. It saves only
device/rate/duration, peak and RMS dBFS, clipping count and half-second RMS
statistics. It does not create an audio file, play sound or adjust gain. Restoring
a report or reopening the workbench never starts another check. Other platforms
keep acoustic analysis available but do not expose this Windows input backend.

An active sound-card input is not proof of microphone calibration. Nonzero input
levels confirm received samples, not absolute SPL, useful frequency coverage,
absence of noise suppression, shared clock accuracy or an acoustic transfer
function. A five-second check is not a room measurement.

## Next signal-verification stage

Testing whether native filters operate independently requires measurements through
the normal playback path. Internal calibration sweeps must not be assumed to
exercise that same path. A stable microphone/ADC path, correct output routing,
controlled gain and timing references are needed for useful before/after ratios.
Compatibility of an original AVR microphone with a computer's analog input must
be checked separately. No recording, playback or EQ-write sequence is started by
the read-only audit or by a saved input-level result.

Local APIs (POST requests require the local session token):

| Endpoint | Scope |
|---|---|
| `POST /api/receiver/audit` | Start a bounded read-only audit for the explicit LAN host |
| `GET /download-receiver-audit` | Download the saved evidence and its raw read snapshot |
| `GET /api/audio-inputs` | Enumerate recording inputs without opening a recording stream |
| `POST /api/audio-inputs/level` | Explicit `ready: true`; fixed five-second statistics only |
