from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re
import uuid

import numpy as np

CHANNELS = ("FL", "FR", "C", "SLA", "SRA", "SBL", "SBR", "SB", "SLB", "SRB", "FWL", "FWR", "CH", "FHL", "FHR", "RHL", "RHR", "SHL", "SHR", "TFL", "TFR", "TML", "TMR", "TRL", "TRR", "TS", "FDL", "FDR", "SDL", "SDR", "BDL", "BDR", "SW1", "SW2", "SW3", "SW4")
ALIASES = {"L": "FL", "R": "FR", "LEFT": "FL", "RIGHT": "FR", "SL": "SLA", "SR": "SRA", "SUB": "SW1", "SW": "SW1", "LFE": "SW1"}
ALIASES.update({f"SWMIX{i}":f"SW{i}" for i in range(1,5)})
ALIASES.update({"SBC":"SB","CB":"SB"})


def unsafe_capture_indices(project):
    if project.origin!='native_avr':return []
    aliases={f'SWMIX{i}':f'SW{i}' for i in range(1,5)}
    return [index for index,m in enumerate(project.measurements)
            if m.metadata.get('receiver_raw_channel_id') in aliases
            and m.metadata.get('receiver_measurement_command_id')!=aliases[m.metadata['receiver_raw_channel_id']]]


def channel_and_position(name: str, fallback: str = "p0") -> tuple[str, str]:
    text = str(name).upper().strip()
    for channel in sorted(CHANNELS, key=len, reverse=True):
        match = re.fullmatch(re.escape(channel) + r"(?:[ _:@-]*(?:P|POS|位置)?(\d+))?", text)
        if match:
            return channel, "p" + match[1] if match[1] is not None else fallback
    return ALIASES.get(text, text or "UNKNOWN"), fallback


def finite_vector(values: Any, name: str, minimum: int = 2) -> np.ndarray:
    try:
        data = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError(name + ' is not a valid numeric array') from error
    if data.ndim != 1 or len(data) < minimum or len(data) > 4_194_304:
        raise ValueError(name + ' has an unsupported length or dimension')
    if not np.isfinite(data).all():
        raise ValueError(name + ' contains NaN or infinity')
    return data


@dataclass
class Measurement:
    channel: str
    position: str
    name: str
    sample_rate: float = 48000.0
    ir: np.ndarray | None = None
    frequency: np.ndarray | None = None
    magnitude_db: np.ndarray | None = None
    phase_deg: np.ndarray | None = None
    start_time_s: float = 0.0
    timing_reference: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.channel = ALIASES.get(self.channel.upper(), self.channel.upper())
        self.position = str(self.position)
        if not np.isfinite(self.sample_rate) or not 4000 <= self.sample_rate <= 768000:
            raise ValueError('Invalid measurement sample rate')
        if not np.isfinite(self.start_time_s):
            raise ValueError('Invalid measurement start time')
        if self.ir is not None:
            self.ir = finite_vector(self.ir, 'Impulse response')
            if not np.any(self.ir):
                raise ValueError('The impulse response contains only zeros')
        if self.frequency is not None:
            self.frequency = finite_vector(self.frequency, 'Frequency')
            self.magnitude_db = finite_vector(self.magnitude_db, 'Frequency response')
            if len(self.frequency) != len(self.magnitude_db) or np.any(np.diff(self.frequency) <= 0):
                raise ValueError('Frequencies must increase and match the response length')
            if self.phase_deg is not None:
                self.phase_deg = finite_vector(self.phase_deg, 'Phase')
                if len(self.phase_deg) != len(self.frequency):
                    raise ValueError('Phase and frequency lengths do not match')
        if self.ir is None and self.frequency is None:
            raise ValueError('The measurement contains neither impulse nor frequency-response data')

    def summary(self):
        return {"channel": self.channel, "position": self.position, "name": self.name,
                "sample_rate": self.sample_rate, "ir_samples": len(self.ir) if self.ir is not None else 0,
                "frequency_points": len(self.frequency) if self.frequency is not None else 0,
                "timing_reference": self.timing_reference}


@dataclass
class Project:
    name: str
    measurements: list[Measurement]
    origin: str = "unverified"
    model: str | None = None
    tier: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def channels(self):
        order = {channel: index for index, channel in enumerate(CHANNELS)}
        return sorted({m.channel for m in self.measurements}, key=lambda c: (order.get(c, 99), c))

    def summary(self):
        from .layouts import summarize_layout
        unsafe=unsafe_capture_indices(self)
        area=self.metadata.get('listening_area');coverage=None
        if area:
            from .listening_area import resolve_listening_area
            try:coverage=resolve_listening_area(self,area)
            except ValueError as error:coverage={'required_seats_measured':False,'error':str(error)}
        return {"id": self.id, "name": self.name, "origin": self.origin, "model": self.model,
                "tier": self.tier, "channels": self.channels,
                "positions": sorted({m.position for m in self.measurements}),
                "measurement_count": len(self.measurements),
                "ir_count": sum(m.ir is not None for m in self.measurements),
                "layout":summarize_layout(self.channels,self.measurements),
                "same_model_groups":self.metadata.get("same_model_groups",[]),
                "position_notes":self.metadata.get('position_notes',{}),
                "listening_area_plan":area,"listening_area_coverage":coverage,
                "unsafe_capture_indices":unsafe,
                "warnings": self.warnings+(['Unverified SWMIX sweep addressing is present. Preserve these records but exclude or remeasure them before filter design.'] if unsafe else []),
                "measurements": [m.summary() for m in self.measurements]}
