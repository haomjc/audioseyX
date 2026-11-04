import json
import threading
import unittest
from unittest.mock import patch

import numpy as np

from audioseyx.native import frame, parse_frame
from audioseyx.native_measure import capture_position, CaptureCancelled


class MeasurementReceiver:
    def __init__(self, silent=False, cancel=None):
        self.commands = []
        self.reply = bytearray()
        self.silent = silent
        self.cancel = cancel

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def settimeout(self, *_):
        pass

    def sendall(self, packet):
        command, payload = parse_frame(packet)
        self.commands.append((command, payload))
        if command == "START_CHNL":
            sub=json.loads(payload).get('Channel','').startswith('SW')
            report = {"SpConnect": "N" if self.silent else 'E' if sub else "S", "Distance": 250, "ResponseCoef": 2}
            self.reply.extend(frame(command, json.dumps(report).encode()))
        elif command == "GET_RESPON":
            ir = np.zeros(16384, dtype="<f4")
            ir[200] = 0.25
            raw = ir.tobytes()
            pieces = [raw[i:i + 512] for i in range(0, len(raw), 512)]
            for index, piece in enumerate(pieces):
                self.reply.extend(frame(command, piece, index, len(pieces) - 1))
            if self.cancel:
                self.cancel.set()
        else:
            self.reply.extend(frame(command, b'{"Comm":"ACK"}'))

    def recv(self, *_):
        result = bytes(self.reply[:1200])
        del self.reply[:1200]
        return result


def status(dtype="Float32"):
    return {"connected": True, "host": "127.0.0.1", "info": {"DType": dtype, "SysDelay": 150},
            "status": {"Mic": True, "HPPlug": False, "ChSetup": [{"FL": "S"}, {"SW1": "E"}]}}


class NativeMicTests(unittest.TestCase):
    def test_subwoofer_command_falling_back_to_a_main_speaker_is_rejected(self):
        class WrongRole(MeasurementReceiver):
            def sendall(self,packet):
                command,payload=parse_frame(packet)
                if command=='START_CHNL':
                    self.commands.append((command,payload))
                    self.reply.extend(frame(command,b'{"Comm":"ACK","SpConnect":"S","Distance":250}'))
                else:super().sendall(packet)
        receiver=WrongRole()
        with patch('audioseyx.native_measure.socket.create_connection',return_value=receiver):
            with self.assertRaisesRegex(ValueError,'confirm a subwoofer'):
                capture_position(status(),channels=['SW1'],settle_seconds=0)
        self.assertNotIn('GET_RESPON',[command for command,_ in receiver.commands])
        self.assertEqual(receiver.commands[-1][0],'EXIT_AUDMD')
    def test_native_capture_preserves_raw_scale_and_position(self):
        receiver = MeasurementReceiver()
        with patch("audioseyx.native_measure.socket.create_connection", return_value=receiver):
            rows = capture_position(status(), position=3, repeats=2, settle_seconds=0)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(m.position == "p2" and m.ir[200] == 0.5 for m in rows))
        self.assertTrue(all(m.metadata["system_delay_reported"] == 150 for m in rows))
        self.assertEqual(receiver.commands[-1][0], "EXIT_AUDMD")

    def test_native_capture_checks_mic_and_dtype_before_connecting(self):
        for device in [status("FixedA"), {**status(), "status": {**status()["status"], "Mic": False}}]:
            with patch("audioseyx.native_measure.socket.create_connection") as connect, self.assertRaises(ValueError):
                capture_position(device, settle_seconds=0)
            connect.assert_not_called()

    def test_silent_sweep_is_not_saved_and_exits_calibration(self):
        receiver = MeasurementReceiver(silent=True)
        with patch("audioseyx.native_measure.socket.create_connection", return_value=receiver), self.assertRaises(ValueError):
            capture_position(status(), settle_seconds=0)
        self.assertNotIn("GET_RESPON", [row[0] for row in receiver.commands])
        self.assertEqual(receiver.commands[-1][0], "EXIT_AUDMD")

    def test_cancelled_capture_sends_abort_and_exit(self):
        event = threading.Event()
        receiver = MeasurementReceiver(cancel=event)
        with patch("audioseyx.native_measure.socket.create_connection", return_value=receiver), self.assertRaises(CaptureCancelled):
            capture_position(status(), cancel=event, settle_seconds=0)
        commands = [row[0] for row in receiver.commands]
        self.assertIn("ABORT_OPRT", commands)
        self.assertEqual(commands[-1], "EXIT_AUDMD")
