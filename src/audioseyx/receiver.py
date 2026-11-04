from __future__ import annotations

import ipaddress
import json
import socket
import time
from urllib.parse import urlparse
from urllib.request import build_opener, ProxyHandler
import xml.etree.ElementTree as ET

from .native import frame, parse_frame


def private_host(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError("Enter the receiver's LAN IP address") from error
    if not (address.is_private or address.is_loopback) or address.is_multicast or address.is_unspecified:
        raise ValueError('Only loopback and LAN receiver addresses are accepted')
    return str(address)


class ReceiverClient:
    def __init__(self, host, port=1256, timeout=4):
        self.host = private_host(host)
        self.port = port
        self.timeout = timeout

    def read_info(self):
        result = {"host": self.host, "read_only": True}
        try:
            with socket.create_connection((self.host, self.port), self.timeout) as connection:
                connection.settimeout(self.timeout)
                for command, key in [("GET_AVRINF", "info"), ("GET_AVRSTS", "status")]:
                    connection.sendall(frame(command))
                    buffer = bytearray()
                    deadline = time.monotonic() + self.timeout
                    while time.monotonic() < deadline:
                        chunk = connection.recv(65536)
                        if not chunk:
                            raise ValueError('The receiver closed the information connection')
                        buffer.extend(chunk)
                        if len(buffer) > 512 * 1024:
                            raise ValueError('The receiver response exceeded the size limit')
                        while len(buffer) >= 3:
                            length = int.from_bytes(buffer[1:3], "big")
                            if length < 19 or length > 65535:
                                raise ValueError('The receiver returned an invalid frame')
                            if len(buffer) < length:
                                break
                            packet = bytes(buffer[:length])
                            del buffer[:length]
                            response_command, payload = parse_frame(packet)
                            if response_command == command:
                                result[key] = json.loads(payload.decode("utf-8"))
                                break
                        if key in result:
                            break
                    if key not in result:
                        raise ValueError('No receiver response for ' + command)
            result["connected"] = True
            if not any(k.casefold() in ["model", "modelname", "targetmodelname"] for k in result["info"]):
                for port, endpoint in [(8080, "/description.xml"), (80, "/description.xml")]:
                    try:
                        with build_opener(ProxyHandler({})).open(f"http://{self.host}:{port}{endpoint}", timeout=1.5) as response:
                            root = ET.fromstring(response.read(256000))
                        model = next((e.text for e in root.iter() if e.tag.split("}")[-1] == "modelName" and e.text), None)
                        if model:
                            result["info"]["Model"] = model
                            result["info"]["ModelSource"] = "device_description"
                            break
                    except (OSError, ValueError, ET.ParseError):
                        continue
        except (OSError, ValueError) as error:
            result.update({"connected": False, "error": str(error)})
        return result


def discover(timeout=2.5):
    message = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
               "MX: 1\r\nST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n").encode()
    locations = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.settimeout(0.4)
        sock.sendto(message, ("239.255.255.250", 1900))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                payload, address = sock.recvfrom(65535)
            except socket.timeout:
                continue
            headers = {}
            for line in payload.decode("utf-8", errors="replace").splitlines()[1:]:
                if ":" in line:
                    key, content = line.split(":", 1)
                    headers[key.lower()] = content.strip()
            location = headers.get("location")
            if location:
                locations[location] = address[0]
    devices = []
    for location, host in locations.items():
        try:
            parsed = urlparse(location)
            private_host(parsed.hostname)
            if parsed.scheme != "http" or parsed.hostname != host:
                continue
            with build_opener(ProxyHandler({})).open(location, timeout=1.5) as response:
                root = ET.fromstring(response.read(256000))
            fields = {element.tag.split("}")[-1]: element.text for element in root.iter() if element.text}
            manufacturer = fields.get("manufacturer", "")
            if any(brand in manufacturer.lower() for brand in ["denon", "marantz", "d&m"]):
                devices.append({"host": host, "name": fields.get("friendlyName", host),
                                "model": fields.get("modelName"), "manufacturer": manufacturer})
        except (OSError, ValueError, ET.ParseError):
            continue
    return {"devices": devices, "notice": 'Only LAN discovery and information reads were performed. Power and calibration settings were not changed.'}
