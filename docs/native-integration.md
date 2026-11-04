# Native receiver integration

## Public distribution boundary

The public package includes independently written protocol handling and filter-design code. It does **not** include legacy coefficient tables, address maps or golden vectors copied from the original local research package, because redistribution permission has not been established for those resources.

Consequently:

- Analysis, minimum-phase design and general filter WAV export work without native resources.
- XT32 native candidate encoding is marked unavailable rather than replaced with guessed coefficients.
- Preparing actual transfer requires a valid local channel map and, for XT32, native tables.
- Supplying local data does not establish a license to redistribute it or prove hardware compatibility.

The existing private 0.3 prototype remains separate. Its files are not copied into public source, wheels, source archives or Windows builds.

## Optional local configuration

If you have lawfully obtained the necessary data for personal use, keep it outside the repository and point audioseyX to the directory:

```console
audioseyx --compat-dir /path/to/local-data --compat-status
audioseyx --compat-dir /path/to/local-data
```

`AUDIOSEYX_COMPAT_DIR` is an equivalent environment variable. Only the fixed JSON filenames `native_tables.json` and `channel_addresses.json` are read. Payload sizes, table lengths, finite values and address ranges are checked. No JavaScript, firmware or executable is loaded from that directory, and no data are downloaded implicitly.

## Experimental transfer

Read-only native information, exact-IP HEOS model identification and current speaker-setting reads have been checked on an X3700H. The [layout guide](layouts.md) describes variable layouts, output selection and the HTTPS settings adapter. This is distinct from acoustic capture or successful DSP reconstruction.

Transfer uses the observed calibration coefficient interface, not firmware flashing. A plan checks the reported model, tier, numeric format, full channel coverage, microphone state and gain budget. Synthetic measurements are rejected; limited mixed-phase designs are export-only.

A complete imported OCA retains an original-filter recovery copy. A settings-only snapshot has no old filters and may require rerunning stock calibration for recovery. The experimental transfer replaces both calibration curves and disables Dynamic EQ and Dynamic Volume while preserving existing crossover, distance and amplifier-assignment settings.

No model has completed physical validation. Packet acknowledgements and simulated tests do not establish actual reconstruction, sample-rate behavior or audible results. The project does not implement arbitrary cross-channel convolution or change the receiver DSP topology.
