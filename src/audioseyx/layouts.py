"""Variable channel layouts and measurement coverage, separate from AVR limits."""
from collections import Counter

HEIGHT_CHANNELS=frozenset("CH FHL FHR RHL RHR SHL SHR TFL TFR TML TMR TRL TRR TS FDL FDR SDL SDR BDL BDR".split())
BED_LAYOUTS={1:["C"],2:["FL","FR"],3:["FL","FR","C"],4:["FL","FR","SLA","SRA"],
             5:["FL","FR","C","SLA","SRA"],6:["FL","FR","C","SLA","SRA","SB"],
             7:["FL","FR","C","SLA","SRA","SBL","SBR"],
             9:["FL","FR","C","SLA","SRA","SBL","SBR","FWL","FWR"]}
HEIGHT_LAYOUTS={0:[],2:["TML","TMR"],4:["TFL","TFR","TRL","TRR"],
                6:["TFL","TFR","TML","TMR","TRL","TRR"]}
PRESET_COUNTS=[(1,0,0),(2,0,0),(2,1,0),(3,1,0),(4,1,0),(5,0,0),(5,1,0),(5,2,0),
               (6,1,0),(7,0,0),(7,1,0),(7,2,0),(5,1,2),(5,1,4),(7,1,2),(7,1,4),
               (7,2,4),(7,1,6),(9,1,4),(9,1,6),(9,4,6)]


def validate_channels(channels):
    from .model import CHANNELS
    if not isinstance(channels,(list,tuple)) or not channels or any(not isinstance(c,str) for c in channels) or len(channels)!=len(set(channels)):
        raise ValueError("A layout needs an explicit nonempty list of distinct channels")
    if any(channel not in CHANNELS for channel in channels):
        raise ValueError("Map every layout channel to a recognized output identifier")
    return list(channels)


def preset_channels(name,subwoofers=None):
    parts=str(name).split(".")
    if len(parts)==2:parts.append("0")
    if len(parts)!=3 or any(not part.isdigit() for part in parts):
        raise ValueError("Use a layout such as 2.0, 5.1.2 or 7.2.4")
    bed,subs,height=map(int,parts)
    if subwoofers is not None:
        if isinstance(subwoofers,bool) or not isinstance(subwoofers,int):raise ValueError("Subwoofer count must be an integer")
        subs=subwoofers
    if bed not in BED_LAYOUTS or height not in HEIGHT_LAYOUTS or not 0<=subs<=4:
        raise ValueError("Select a supported example layout, or supply explicit channel identifiers")
    return BED_LAYOUTS[bed]+HEIGHT_LAYOUTS[height]+[f"SW{i+1}" for i in range(subs)]


def presets():
    return [{"id":f"{bed}.{sub}"+(f".{height}" if height else ""),
             "channels":preset_channels(f"{bed}.{sub}.{height}")}
            for bed,sub,height in PRESET_COUNTS]


def summarize_layout(channels,measurements=None):
    from .model import CHANNELS
    channels=list(dict.fromkeys(channels))
    unknown=[channel for channel in channels if channel not in CHANNELS]
    subs=[channel for channel in channels if channel.startswith("SW") and channel in CHANNELS]
    heights=[channel for channel in channels if channel in HEIGHT_CHANNELS]
    bed=[channel for channel in channels if channel in CHANNELS and channel not in subs+heights]
    signature=f"{len(bed)}.{len(subs)}"+(f".{len(heights)}" if heights else "")
    result={"signature":signature if not unknown else None,"bed_channels":bed,"subwoofer_outputs":subs,
            "height_channels":heights,"unmapped_channels":unknown,"output_count":len(channels),
            "independent_subwoofer_filters_verified":False,"receiver_layout_supported":None}
    if measurements is not None:
        positions=sorted({measurement.position for measurement in measurements})
        counts=Counter((measurement.channel,measurement.position) for measurement in measurements)
        missing=[{"channel":channel,"position":position} for channel in channels for position in positions if not counts[(channel,position)]]
        result["coverage"]={"positions":positions,"complete":bool(positions) and not missing,
                            "missing":missing,"measurements":sum(counts.values()),
                            "repeats": [{"channel":channel,"position":position,"count":count} for (channel,position),count in sorted(counts.items())]}
    return result


def configured_channels(status):
    from .model import ALIASES
    raw=[str(key) for row in status.get("ChSetup",[]) for key,setting in row.items() if setting!="N"]
    normalized=[ALIASES.get(channel,channel) for channel in raw]
    if len(normalized)!=len(set(normalized)):
        raise ValueError("Receiver output aliases are ambiguous; explicit channel mapping is required")
    return normalized
