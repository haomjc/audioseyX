# Channel layouts and receiver settings

The workbench uses explicit output identifiers and the measured layout. It does not require twelve outputs or assume that every project is 7.1.4.

## Examples and custom layouts

The interface provides examples from mono and stereo through 5.1, 6.1, 7.1, height-speaker layouts and 9.4.6. Override the example subwoofer count from zero to four, or enter explicit custom channel identifiers. A custom example can use front heights, rear heights, upward-firing channels, a centre height, or a top-surround output. Original import and capture data are not synthesized by choosing an example layout.

Examples are synthetic, carry no receiver certification and cannot be transferred to a real receiver. An explicit channel list determines the topology; a count such as 5.1.2 alone does not identify where the two height speakers are installed.

```python
from audioseyx.dsp import demo_project

stereo = demo_project("2.0")
four_subs = demo_project("9.4.6", positions=3)
custom_heights = demo_project(["FL", "FR", "C", "SLA", "SRA",
                              "FHL", "FHR", "SHL", "SHR", "TS", "SW1"])
```

Real projects infer the layout from imported or captured output identifiers. The summary separates bed, subwoofer and height outputs and lists missing output/position entries. The joint designer requires complete coverage of its selected outputs at every training or held-out position. It never invents a response for an unmeasured speaker.

The same independent and joint workflows support different counts. The selected tier still determines each filter's budget. Available memory, actual receiver routing and native channel addresses remain separate constraints. A large offline example does not increase an AVR's output capacity.

## Configured outputs and physical wiring

Connect using the receiver's LAN IP or its HTTP(S) web address. Read-only native queries obtain the DSP format, Audyssey type, amplifier assignment, microphone state and configured outputs. On compatible HEOS devices, the model is read through the official `player/get_players` command, selecting only the entry with the exact requested IP. Other network players are not used to identify this receiver. The reported HEOS version is a component version, not necessarily the complete AVR firmware version.

The observed HTTPS speaker-settings adapter reads current speaker types, crossover selection and values, bass mode, LFE low-pass, distances and trims. The adapter uses only fixed `get_config` endpoints, rejects redirects, and accepts self-signed receiver certificates at the selected LAN address for these read-only requests. XML index namespaces for crossovers, distances and trims differ and are decoded separately. Unrecognized data remain unavailable or raw rather than being guessed.

Click **Read current receiver routing settings** in the joint-design controls to review these values as the offline model's settings. Per-channel crossovers have an explicit checkbox. To search a new global crossover list, disable that checkbox. Loading a value into the offline designer does not change the receiver menu or validate its electrical crossover slopes.

The receiver may enable a subwoofer output with no connected cabinet. Select the physically connected outputs explicitly before capture. Empty capture selection means all receiver-configured outputs. `SWMIX1` through `SWMIX4` are retained as configured identifiers, while individual sweep/download commands and measurement names use `SW1` through `SW4`. Configured outputs omitted by the selection are recorded in measurement metadata.

Multiple subwoofer jacks, separate distance/trim values and separate measurement IDs do not prove independently writable EQ. Joint multi-subwoofer design requires explicit confirmation of independent control. Native transfer continues to require exact receiver layout coverage and a reviewed plan.

## Hardware evidence

Read-only native information, exact-IP HEOS model identification and speaker-setting reads were checked on an X3700H. Float capture transport, including individual subwoofer commands, has also been checked. Microphone compensation, filter reconstruction, transfer and after-correction playback measurements remain unverified. Plugging in a microphone does not automatically start tones or calibration.

References: [official HEOS CLI specification](https://assets.denon.com/documentmaster/us/heos_cli_protocolspecification-version_04062020.pdf) and [Denon X3700H crossover settings](https://manuals.denon.com/AVRX3700H/NA/EN/GFNFSYitmloggd.php).

## Seat priorities and missing data

The saved three-seat row plan starts at 50 percent centre priority and 25 percent per side. Each seat's priority is divided among its measured positions; repeated scans at one point do not multiply it. Native minimum-phase magnitude statistics can use weighted position medians and position-balanced error evaluation. The full joint mode retains every complex observation and applies the same spatial mass to its objective.

The main-seat position is `p0`, left/right seat ear positions are `p1`/`p2`, with optional nearby points `p3` through `p6`. These IDs are assignments, not generated measurements. Missing required seats prevent final row design and transfer preparation. An explicitly provisional calculation may use observed seats, but cannot be prepared as final row calibration. Direct position weights and hierarchical seat priorities are mutually exclusive inputs.

Held-out positions are excluded from both independent and joint fitting. Validation keeps the training level reference and does not renormalize error after design. A held-out point must not be the only training coverage for a required seat.

Saved speaker-setting snapshots can be loaded into the offline model without contacting the receiver. Their origin remains a saved, unrefreshed snapshot; powering off a receiver does not cause automatic reconnection or measurement.
