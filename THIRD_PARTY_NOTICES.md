# Third-party notices and provenance

## Project code

New audioseyX code is provided under the MIT license in `LICENSE`. The application is an independent project; product and company names describe compatibility targets, not sponsorship or certification.

## Data-only Java stream reader and synthetic fixtures

`src/audioseyx/_java_stream.py` is adapted from the data reader in Jamesits/eqx, pinned to commit `427b7d17479806a3b35e9c66500a334f0ad86077`. It creates data objects only, does not load Java classes, and adds size limits.

The artificial MQX and MDAT fixtures under `examples/` come from that same upstream revision. `examples/manifest.json` records their origin and hashes. These fixtures must never be used as real calibration data.

Their upstream GLWTS license is preserved verbatim in `third_party/eqx-LICENSE.txt`. It is not replaced by the project's MIT license. Distribution metadata therefore includes `LicenseRef-GLWTS` in addition to MIT.

Upstream source: https://github.com/Jamesits/eqx/tree/427b7d17479806a3b35e9c66500a334f0ad86077

## Dependencies

NumPy, SciPy, Flask, Werkzeug, Waitress and platformdirs are separately distributed dependencies. Their licenses govern their own code. The Windows build collects the installed dependency metadata, including license files. Waitress's notice is also retained at `third_party/waitress-LICENSE.txt`.

## Excluded compatibility material

The public project does not include `native_tables.json`, `channel_addresses.json`, legacy native-codec golden vectors, original AcoustiX/GSonic executables or extracted JavaScript, the Audyssey APK, receiver firmware, private calibration files or microphone calibration databases.

Native framing and the Python packing adapter were independently implemented against observed interface behavior. Numerical resources from the original local research package have not been assigned an open-source license by this project. Supplying a local compatibility directory is a personal configuration path, not a redistribution grant or hardware certification.

Public feature descriptions and documentation were used to understand design goals. No GSonic closed-source engine is copied or bundled.
