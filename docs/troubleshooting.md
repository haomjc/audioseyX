# Troubleshooting

## The interface does not open

Run `audioseyx --no-browser` and use the printed loopback URL. Check whether another instance already uses the same data directory. Use **Exit workbench** to stop it, or choose a separate `--data-dir`.

Avoid exposing the loopback service through a public tunnel. The interface is intended for one local user, not as a hosted multi-user application.

## REW is installed but not connected

Open REW, finish its startup and audio-device prompts, enable the API and select **Check again**. An installed executable does not prove the API has started. Manual measurement and file import remain available; automatic commands depend on REW version and licensing.

## The receiver was not discovered

Check that the computer and AVR are on the same LAN, then try the receiver IP directly. Discovery may not cross network segments. Detection reads information only; it does not power on the receiver. Do not assume that an empty discovery result proves the receiver is incompatible.

## Native encoding is unavailable

This is expected when optional local compatibility data are absent. Continue with analysis, design and filter WAV export. `audioseyx --compat-status` explains which data are missing. See [native integration](native-integration.md); do not substitute synthetic tables for real receiver data.

## A decay estimate is blank

Inspect the reason beside the fit. A short response, insufficient tail margin, a nonlinear decay or possible filter ringing can make a value unavailable. A 341 ms stock response cannot establish an arbitrarily slow decay. Frequency-only measurements cannot establish measured time-domain behavior.

## Before/after level change is blank

Without comparable units and measurement-chain metadata, only response shape is compared. ADC-relative measurements and calibrated SPL measurements must not be subtracted as if their absolute levels were interchangeable.

## Downloads fail or change during saving

The interface retries individual segments, checks each range and verifies the final SHA-256 before saving. Retry after the current project or design stops changing. If local traffic is modified by a proxy or browser extension, compare its behavior with the offline self-test and retained report; the application does not change those system settings automatically.

## Reporting an issue

Include the audioseyX version, operating system, minimal synthetic reproduction and whether the issue concerns analysis, file formats or an actual receiver. Redact credentials, private calibration files and identifying paths. A screenshot of an error alone is less useful than the input type and the exact operation that triggered it.
