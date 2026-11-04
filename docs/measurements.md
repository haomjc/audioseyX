# Measurements and timing

## Import routes

| Input | What is retained | Main limitation |
|---|---|---|
| ADY | Per-position raw responses and channel names | Missing sample rate is recorded as the observed 48 kHz family default; timing needs confirmation |
| MQX | Enabled positions, float32 IR data and recorded system-delay metadata | Microphone compensation and delay interpretation are not assumed |
| MDAT | Frequency response and available phase | The private IR representation is not guessed |
| Mono WAV | Signed impulse samples and sample rate | Channel, position, timing and calibration metadata need checking |
| `.awb` / project JSON | Original arrays, metadata, timing and origin | Checksums establish file integrity, not measurement accuracy |

The import limit is 128 MiB. Large projects can be split into smaller batches. Files with NaN, infinity, invalid dimensions or unsupported payloads are rejected.

## Stock microphone

Connect the receiver's own microphone to its calibration input. The experimental Float driver requests test tones and raw responses through the LAN. Place the microphone before starting a position and move it only when that position has finished. Repeats are retained individually, and a partial checkpoint is saved on failure.

This route is not yet hardware-verified. Fixed-device capture requires a separate sequence; imported stock-app measurements are an alternative. A receiver-generated calibration sweep is not automatically equivalent to a normal-playback verification measurement.

Review the [configured and physical output selection](layouts.md) before starting. Subwoofer outputs reported as `SWMIX` keep their raw protocol IDs. Excluded configured outputs are recorded explicitly. A microphone appearing as connected does not start measurement; the ready control and capture action are still required.

## REW

Install REW separately and complete its device and microphone setup. The workbench can request API startup, inspect offered commands and read original responses. It does not bundle REW or alter its license.

Automatic SPL sweeps depend on the available REW commands, version and Pro licensing. Manual measurement in REW followed by import remains possible. No sweep starts in the background.

## Mapping and timing

Check channel and position mappings before design. Typical names include `FL0`, `FR0`, `C0` and `SW1`; a standalone `SW1` is a subwoofer channel, not position 1.

IR peaks are not independently shifted to zero. Original `start_time_s` and relative propagation timing remain available. Phase design and common main/subwoofer alignment require a reliable common reference; do not confirm it merely because the files contain impulses.

## Verification measurements

Measure through the normal playback path after correction, using comparable microphone placement and settings. The comparison groups repeats by channel and position, uses their median magnitude and compares only the common frequency band. Response shapes are median-level aligned by default.

A physical level change is reported only when comparable level units and chain metadata are recorded, or both measurements explicitly establish calibrated absolute SPL. Lower level alone does not prove reduced decay time.
