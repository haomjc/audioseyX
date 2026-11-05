"""Run from a wheel-installed environment, without importing the source checkout."""
import json
from pathlib import Path

import audioseyx
from audioseyx.app import Workbench
from audioseyx.diagnostics import self_test
from audioseyx.dsp import demo_project, design_project
from audioseyx.exporter import export_bundle
import tempfile

package = Path(audioseyx.__file__).resolve().parent
if "site-packages" not in package.parts:
    raise SystemExit("This check must import a wheel installed in site-packages")
if any((package / name).exists() for name in ["native_tables.json", "channel_addresses.json"]):
    raise SystemExit("Private compatibility resources must not be installed")
for name in ["index.html", "app.js", "analysis.js", "downloads.js", "joint.js", "layouts.js", "polarity.js", "transfer.js", "receiver-calibration.js", "clock.js", "verification.js", "curve-profiles.js", "processing-controls.js", "style.css"]:
    if not (package / "web" / name).is_file():
        raise SystemExit("Installed web asset missing: " + name)
report = self_test()
assert report["all_passed"] and report["hardware_tested"] is False
with tempfile.TemporaryDirectory() as directory:
    project = demo_project()
    project.measurements = project.measurements[:3]
    design = design_project(project, "XT32", {})
    archive = export_bundle(project, design, Path(directory) / "demo")
    assert archive.is_file() and archive.stat().st_size > 0
    bench = Workbench(Path(directory) / "workspace")
    bench.set_project(project)
    assert bench.state()["application_id"] == "audioseyx-workbench"
print(json.dumps({"version": audioseyx.__version__, "installed_wheel_smoke_passed": True,
                  "native_tables_bundled": False, "hardware_tested": False}, indent=2))
