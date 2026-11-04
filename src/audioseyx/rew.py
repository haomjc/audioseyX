from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

import numpy as np

from .model import Measurement, Project, channel_and_position


class RewClient:
    def __init__(self, port=4735, timeout=8):
        if not 1024 <= int(port) <= 65535:
            raise ValueError('Invalid REW port')
        self.base = f"http://127.0.0.1:{int(port)}"
        self.timeout = timeout

    def request(self, endpoint, payload=None, method="GET", timeout=None):
        if not endpoint.startswith("/"):
            raise ValueError('Invalid REW API path')
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base + endpoint, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout or self.timeout) as response:
                raw = response.read(64 * 1024 * 1024)
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            message = error.read(8192).decode("utf-8", errors="replace")
            raise ValueError(f"REW returned {error.code}：{message}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ValueError('REW API is not connected. Start the API service in REW first.') from error

    def status(self):
        try:
            measurements = self.request("/measurements")
            return {"connected": True, "measurement_count": len(measurements),
                    "address": self.base, "measurements": measurements}
        except ValueError as error:
            return {"connected": False, "address": self.base, "error": str(error)}

    def import_project(self):
        listing = self.request("/measurements")
        items = listing.items() if isinstance(listing, dict) else [(str(i + 1), m) for i, m in enumerate(listing)]
        rows = []
        errors = []
        for identifier, item in items:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("id", identifier))
            if not identifier.replace("-", "").isalnum():
                raise ValueError('Invalid REW measurement identifier')
            name = item.get("title", item.get("name", identifier))
            channel, position = channel_and_position(name)
            try:
                impulse = self.request(f"/measurements/{identifier}/impulse-response?windowed=false&normalised=false")
                raw = base64.b64decode(impulse["data"], validate=True)
                if len(raw) % 4:
                    raise ValueError('Invalid REW impulse data length')
                rate = impulse.get("sampleRate")
                if rate is None:
                    interval = float(impulse["sampleInterval"])
                    if interval <= 0:
                        raise ValueError('REW did not return a valid sample rate')
                    rate = 1 / interval
                rows.append(Measurement(channel, position, name, float(rate),
                                        np.frombuffer(raw, dtype=">f4").astype(float),
                                        start_time_s=float(impulse.get("startTime", 0)),
                                        timing_reference=str(impulse.get("timingReference", "unknown")),
                                        metadata={"rew_id": identifier, "ir_unit_request": "default_linear",
                                                  "microphone_calibration_applied": None}))
            except (ValueError, KeyError, TypeError) as error:
                errors.append(name + "：" + str(error))
        if not rows:
            raise ValueError('No readable impulse responses were found in REW.' + "；".join(errors[:2]))
        return Project('Measurements from REW', rows, warnings=errors + ['Check channel and position names and the common timing reference. Reading has not deleted or modified REW measurements.'])

    def load_mdat(self, path):
        path = Path(path).resolve()
        return self.request("/measurements/command", {"command": "Open", "parameters": [str(path)]}, "POST", timeout=30)

    def measurement_controls(self):
        result = {}
        for key, endpoint in [("audio", "/audio"), ("measurement", "/measure"),
                              ("commands", "/measure/commands"), ("driver", "/audio/driver"),
                              ("java_outputs", "/audio/java/output-channels")]:
            try:
                result[key] = self.request(endpoint)
            except ValueError as error:
                result[key] = {"error": str(error)}
        result["notice"] = 'Automatic sweeps depend on REW version and Pro licensing. Measurement requires an explicit start; no background sound is played.'
        result["command_names"] = command_names(result.get("commands", []))
        return result

    def measure(self, command, parameters):
        available = self.request("/measure/commands")
        allowed = command_names(available)
        if command not in allowed:
            raise ValueError('REW does not currently offer this measurement command')
        return self.request("/measure/command", {"command": command, "parameters": parameters}, "POST", timeout=60)


def command_names(data):
    if isinstance(data, dict):
        if "commands" in data:
            return command_names(data["commands"])
        if isinstance(data.get("command"), str):
            return [data["command"]]
        if isinstance(data.get("name"), str):
            return [data["name"]]
        return []
    if isinstance(data, list):
        result = []
        for item in data:
            result.extend([item] if isinstance(item, str) else command_names(item))
        return result
    return []


def find_rew():
    candidates = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "REW/roomeqwizard.exe",
                  Path("D:/Program Files/REW/roomeqwizard.exe")]
    registry_versions = []
    if os.name == "nt":
        import winreg
        for hive, prefix in [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")]:
            try:
                with winreg.OpenKey(hive, prefix) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        with winreg.OpenKey(root, winreg.EnumKey(root, index)) as child:
                            try:
                                name = winreg.QueryValueEx(child, "DisplayName")[0]
                                if name.startswith("REW ") or "Room EQ Wizard" in name:
                                    location = winreg.QueryValueEx(child, "InstallLocation")[0]
                                    candidates.insert(0, Path(location) / "roomeqwizard.exe")
                                    registry_versions.append(winreg.QueryValueEx(child, "DisplayVersion")[0])
                            except OSError:
                                pass
            except OSError:
                pass
    installers = []
    for directory in [Path("D:/Downloads"), Path.home() / "Downloads"]:
        if directory.is_dir():
            installers.extend(str(p) for p in directory.glob("REW_windows*.exe"))
    executable = next((str(p) for p in candidates if p.is_file()), None)
    return {"executable": executable, "installed": executable is not None,
            "version": registry_versions[0] if registry_versions else None, "installers": installers}


def launch_rew(visible=True):
    detected = find_rew()
    if not detected["executable"]:
        raise ValueError('An installed REW executable was not found. Install REW or specify it in the tool configuration.')
    arguments = [detected["executable"], "-api"]
    if not visible:
        arguments += ["-nogui"]
    startup = None
    flags = 0
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 1 if visible else 0
        flags = subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(arguments, cwd=str(Path(detected["executable"]).parent), startupinfo=startup,
                               creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"pid": process.pid, "started": True, "notice": 'Requested REW API startup. Complete any first-run or audio-device prompts in REW, then check the connection again.'}
