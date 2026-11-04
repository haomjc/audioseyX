# audioseyX

**Measure your system. Understand the room. Design within the hardware.**

audioseyX is a local acoustic workbench for measurement import, room analysis and Audyssey-aware filter design. Individual positions, repeats and original timing remain available throughout the workflow.

Start with the synthetic 7.1.4 demo to explore the interface without connecting a receiver. Inspect response and decay, design a filter for a selected tier, and export the actual truncated filter WAVs and reports.

:::{important}
This is the **0.4.0a1 public-release candidate**. Physical receiver operation and real acoustic results have not been verified. Public distributions do not include legacy native compatibility tables with unresolved redistribution permission. Offline analysis, design and general WAV export work without them.
:::

## Find what you need

- [Quick start](quickstart.md): install locally and try the demo.
- [Measurement workflow](measurements.md): stock microphone, REW, imports and timing.
- [Acoustic analysis](analysis.md): response, group delay, ETC, decay quality and STFT.
- [Filter design](design.md): targets, native budgets, boost protection and predictions.
- [Native integration](native-integration.md): experimental receiver support and optional local data.
- [Troubleshooting](troubleshooting.md): connections, units, downloads and diagnostics.
- [Releasing](releasing.md): GitHub, PyPI, Read the Docs and Windows artifacts.

```{toctree}
:maxdepth: 2
:hidden:
:caption: User guide

quickstart
measurements
analysis
design
native-integration
troubleshooting
```

```{toctree}
:maxdepth: 2
:hidden:
:caption: Project and development

api
validation
releasing
contributing
licensing
changelog
```
