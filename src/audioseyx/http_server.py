from __future__ import annotations

import json
import hashlib
import logging
import os
from pathlib import Path
import socket
import struct
import threading
import time
import webbrowser

from flask import Flask, Response, request, send_file
from werkzeug.exceptions import HTTPException
from waitress import create_server, wasyncore
from waitress.channel import HTTPChannel

from .dsp import demo_project
from .receiver import ReceiverClient, discover
from .rew import find_rew, launch_rew


class GracefulWindowsChannel(HTTPChannel):
    """Give Windows a bounded acknowledgement wait when closing a full response."""

    def handle_close(self):
        if (self.connected and self.will_close and not self.total_outbufs_len
                and self.socket is not None):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH", 1, 2))
                self.socket.setblocking(True)
            except OSError:
                pass
        super().handle_close()


class LocalServer:
    """Own the Waitress event loop so shutdown can drain pending responses."""

    def __init__(self, application, port):
        self.channels = {}
        self.transport = create_server(application, host="127.0.0.1", port=port, map=self.channels,
                                       threads=6, max_request_body_size=180 * 1024 * 1024,
                                       channel_timeout=30, ident="AudysseyWorkbench")
        if os.name == "nt":
            self.transport.channel_class = GracefulWindowsChannel
        self.server_port = int(self.transport.effective_port)
        self.stopping = threading.Event()
        self.finished = threading.Event()
        self.owner = None
        self.closed = False

    def serve_forever(self):
        self.owner = threading.current_thread()
        try:
            while not self.stopping.is_set():
                wasyncore.loop(timeout=.1, count=1, map=self.channels)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                pending = any(channel.requests or any(len(buffer) for buffer in tuple(channel.outbufs))
                              for channel in tuple(self.transport.active_channels.values()))
                if not pending:
                    break
                wasyncore.loop(timeout=.02, count=1, map=self.channels)
        finally:
            self._close()
            self.finished.set()

    def _close(self):
        if not self.closed:
            self.closed = True
            wasyncore.close_all(map=self.channels)
            self.transport.task_dispatcher.shutdown()

    def shutdown(self):
        self.stopping.set()
        if self.owner and not self.finished.is_set():
            self.transport.pull_trigger()
            if threading.current_thread() is not self.owner:
                self.finished.wait(8)

    def server_close(self):
        self.shutdown()
        if self.owner is None:
            self._close()


def build_server(bench_factory, home, port=0, open_browser=True):
    home = Path(home).resolve()
    bench = bench_factory(home)
    application = Flask(__name__, static_folder=None)
    application.config.update(MAX_CONTENT_LENGTH=180 * 1024 * 1024, TRUSTED_HOSTS=["127.0.0.1", "localhost"])
    assets = Path(__file__).with_name("web")
    download_hashes = {}

    def reply(data, status=200):
        return Response(json.dumps(data, ensure_ascii=False, allow_nan=False), status=status,
                        content_type="application/json; charset=utf-8")

    def download_info(path):
        if path == "/download":
            target, name, mime = bench.export_path, "audioseyX-design.zip", "application/zip"
        elif path == "/download-project":
            target, name, mime = bench.project_path, "measurements.awb", "application/zip"
        elif path == "/download-analysis":
            target, name, mime = bench.analysis_path, "measurement-analysis.json", "application/json"
        elif path == "/download-calibration":
            target = bench.home / "prepared-calibration.oca" if bench.transfer_plan else None
            name = "prepared-calibration.awbcal.json" if bench.template and bench.template.get("_source_kind") == "native_settings_snapshot" else "prepared-calibration.oca"
            mime = "application/json"
        else:
            raise ValueError('Invalid download selection')
        if target is None or not target.is_file():
            raise ValueError('No reviewed result is currently available for download')
        stat = target.stat()
        key = (str(target), stat.st_size, stat.st_mtime_ns)
        digest = download_hashes.get(key)
        if digest is None:
            with target.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if len(download_hashes) > 64:
                download_hashes.clear()
            download_hashes[key] = digest
        return target, {"url": path, "filename": name, "mime": mime, "bytes": stat.st_size, "sha256": digest}

    @application.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @application.errorhandler(Exception)
    def error_response(error):
        status = error.code if isinstance(error, HTTPException) else 400
        return reply({"error": str(error)}, status)

    @application.route("/", defaults={"subpath": ""}, methods=["GET", "POST"])
    @application.route("/<path:subpath>", methods=["GET", "POST"])
    def dispatch(subpath):
        path = "/" + subpath
        if request.method == "GET":
            if path == "/api/state":
                return reply({**bench.state(), "transfer": bench.transfer_state()})
            if path == "/api/environment":
                return reply({"rew": find_rew(), "rew_api": bench.rew.status()})
            if path == "/api/rew/status":
                return reply(bench.rew.status())
            if path == "/api/rew/controls":
                return reply(bench.rew.measurement_controls())
            if path == "/api/download-info":
                return reply(download_info(request.args.get("path", ""))[1])
            if path in ["/download", "/download-project", "/download-analysis", "/download-calibration"]:
                target, info = download_info(path)
                expected = request.headers.get("If-Match")
                if expected and expected != '"' + info["sha256"] + '"':
                    return reply({"error": 'The download changed; restart the download'}, 412)
                return send_file(target, mimetype=info["mime"], as_attachment=True, download_name=info["filename"],
                                 conditional=True, etag=info["sha256"])
            asset = {"/": "index.html", "/app.js": "app.js", "/analysis.js": "analysis.js", "/downloads.js": "downloads.js", "/style.css": "style.css"}.get(path)
            if asset is None:
                return reply({"error": 'Page not found'}, 404)
            content = (assets / asset).read_bytes()
            if asset == "index.html":
                content = content.replace(b"__APP_TOKEN__", bench.token.encode())
            content_type = "text/html" if asset == "index.html" else "text/javascript" if asset.endswith(".js") else "text/css"
            return Response(content, content_type=content_type + "; charset=utf-8")

        if request.headers.get("X-App-Token") != bench.token:
            return reply({"error": 'Invalid local session token; refresh the workbench'}, 403)
        origin = request.headers.get("Origin")
        if origin and origin != address:
            return reply({"error": 'Requests from other origins are rejected'}, 403)
        body = request.get_json() or {}
        if path == "/api/demo":
            result = bench.set_project(demo_project())
        elif path == "/api/import":
            result = bench.upload(body)
        elif path == "/api/mapping":
            result = bench.mapping(body["entries"])
        elif path == "/api/rew/start":
            result = launch_rew(body.get("visible", True) is True)
            bench.log('Requested REW startup.')
        elif path == "/api/rew/import":
            result = bench.set_project(bench.rew.import_project())
        elif path == "/api/rew/measure":
            result = bench.rew.measure(body["command"], body.get("parameters", {}))
        elif path == "/api/native-measure/start":
            result = bench.start_native_measurement(body)
        elif path == "/api/native-measure/cancel":
            bench.capture_cancel.set()
            result = {"cancel_requested": True}
        elif path == "/api/receiver/discover":
            if bench.job["status"] == "running" and bench.job.get("kind") in ["native_measure", "transfer"]:
                raise ValueError('Receiver calibration communication is active; wait or stop capture before discovery')
            result = discover()
        elif path == "/api/receiver/connect":
            if bench.job["status"] == "running" and bench.job.get("kind") in ["native_measure", "transfer"]:
                raise ValueError('Receiver calibration communication is active; reconnecting is temporarily unavailable')
            result = ReceiverClient(body["host"]).read_info()
            bench.receiver = result
            bench.log('Read-only receiver check: ' + ('connected' if result["connected"] else 'not connected'))
        elif path == "/api/design":
            result = bench.start_design(body)
        elif path == "/api/target-curve":
            result = bench.set_curve(body)
        elif path == "/api/self-test":
            from .diagnostics import self_test
            result = self_test()
        elif path == "/api/benchmarks":
            from .benchmarks import run_benchmarks
            result = run_benchmarks()
            (bench.home / "benchmark-report.json").write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        elif path == "/api/compare":
            result = bench.compare(body)
        elif path == "/api/analyze":
            result = bench.analyze(body)
        elif path == "/api/room-modes":
            from .analysis import room_modes
            result = room_modes(body["length"], body["width"], body["height"], body.get("high_hz", 200))
        elif path == "/api/template":
            result = bench.import_template(body)
        elif path == "/api/template/current":
            result = bench.use_current_configuration()
        elif path == "/api/transfer/prepare":
            result = bench.prepare_apply(body)
        elif path == "/api/transfer/apply":
            result = bench.start_apply(body)
        elif path == "/api/shutdown":
            if bench.job["status"] == "running" and bench.job.get("kind") in ["native_measure", "transfer"]:
                raise ValueError('Stop stock-microphone capture or wait for receiver transfer completion before exiting')
            threading.Thread(target=server.shutdown, daemon=True).start()
            result = {"stopping": True}
        else:
            return reply({"error": 'Operation not implemented'}, 404)
        return reply(result)

    server = LocalServer(application, port)
    address = f"http://127.0.0.1:{server.server_port}"
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    (Path(home) / "running.json").write_text(json.dumps({"url": address, "pid": os.getpid()}), encoding="utf-8")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(address)).start()
    return server, bench, address
