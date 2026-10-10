"""固定、可追溯的信号与标签协议。"""
from dataclasses import asdict, dataclass
import hashlib
import json

CHANNELS = tuple(('FP1-F7 F7-T7 T7-P7 P7-O1 FP1-F3 F3-C3 C3-P3 P3-O1 '
                  'FP2-F4 F4-C4 C4-P4 P4-O2 FP2-F8 F8-T8 T8-P8 P8-O2 FZ-CZ CZ-PZ').split())
BANDS = ((0.5, 4), (4, 8), (8, 13), (13, 30), (30, 45))
FEATURE_SCHEMA = 'welch_log10_uv2_hann_2s_boundary_interp_channel_major_v1'

@dataclass(frozen=True)
class Protocol:
    sampling_hz: int = 128
    window_seconds: int = 10
    stride_seconds: int = 10
    warmup_seconds: int = 30
    sph_seconds: int = 300
    sop_seconds: int = 1800
    postictal_seconds: int = 1800
    protocol_id: str = 'recording_conditional_r1'

    def to_dict(self):
        return asdict(self)

    @property
    def horizon(self):
        return self.sph_seconds + self.sop_seconds

PROTOCOL = Protocol()

def feature_contract():
    return dict(feature_schema=FEATURE_SCHEMA, channels=list(CHANNELS),
                bands_hz=[list(b) for b in BANDS], protocol=PROTOCOL.to_dict())

def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
