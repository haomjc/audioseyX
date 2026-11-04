from dataclasses import dataclass


def identify_tier(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return {0: "MultEQ", 1: "XT", 2: "XT32"}.get(value)
    text = str(value).upper().replace(" ", "").replace("_", "").replace("-", "")
    return {"0": "MultEQ", "1": "XT", "2": "XT32", "MULTEQ": "MultEQ", "XT": "XT",
            "MULTEQXT": "XT", "XT32": "XT32", "MULTEQXT32": "XT32"}.get(text)


@dataclass(frozen=True)
class Capability:
    tier: str
    main_length: int
    sub_length: int
    main_rate: int
    sub_rate: int
    main_packed: int
    sub_packed: int
    codec: str
    hardware_verified: bool = False

    def shape(self, channel):
        sub = channel.startswith("SW")
        return (self.sub_length, self.sub_rate, self.sub_packed) if sub else (self.main_length, self.main_rate, self.main_packed)


CAPABILITIES = {
    "MultEQ": Capability("MultEQ", 128, 512, 48000, 6000, 128, 512, "observed_vector"),
    "XT": Capability("XT", 512, 512, 48000, 6000, 512, 512, "observed_vector"),
    "XT32": Capability("XT32", 16321, 16055, 48000, 48000, 1024, 704, "observed_multirate"),
}


def capability(tier):
    key = identify_tier(tier)
    if key not in CAPABILITIES:
        raise ValueError('Select MultEQ, XT or XT32 explicitly; capability is not guessed for unknown models')
    return CAPABILITIES[key]
