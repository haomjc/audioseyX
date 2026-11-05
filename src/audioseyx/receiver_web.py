"""Read-only speaker settings from the observed Denon/Marantz web interface.

Only get_config endpoints are used. Index namespaces differ between speaker
configuration, crossovers, distances and levels; they are never interchanged.
"""
from __future__ import annotations

import math
import ssl
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from urllib.request import build_opener, ProxyHandler, HTTPSHandler, HTTPRedirectHandler

from .layouts import configured_channels

CROSSOVER_GROUPS={
    0:["FL","FR"],1:["C"],2:["SLA","SRA"],3:["SBL","SBR","SB"],4:["FWL","FWR"],
    5:["FHL","FHR"],6:["FDL","FDR"],7:["TFL","TFR"],8:["TML","TMR"],9:["SDL","SDR"],
    10:["TRL","TRR"],11:["SHL","SHR"],12:["RHL","RHR"],13:["BDL","BDR"],14:["CH"],15:["TS"]}
CONFIG_GROUPS={0:["FL","FR"],1:["C"],3:["SLA","SRA"],4:["SBL","SBR","SB"],6:["FWL","FWR"],
               7:["FHL","FHR"],8:["FDL","FDR"],9:["TFL","TFR"],10:["TML","TMR"],11:["SDL","SDR"],
               12:["TRL","TRR"],13:["SHL","SHR"],14:["RHL","RHR"],15:["BDL","BDR"],16:["CH"],17:["TS"]}
DISTANCE_CHANNELS="FL FR C SW1 SW2 SW3 SW4 SLA SRA SBL SBR SB FWL FWR FHL FHR FDL FDR TFL TFR TML TMR SDL SDR TRL TRR SHL SHR RHL RHR BDL BDR CH TS".split()
LEVEL_CHANNELS="FL C FR FWR SRA SBR SB SBL SLA FWL FHR FDR TFR TMR SDR TRR SHR RHR BDR BDL RHL SHL TRL SDL TML TFL FDL FHL CH TS SW1 SW2 SW1+SW2 SW3 SW4 ALL_SUBS".split()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def _number(text,low,high):
    number=float(text)
    if not math.isfinite(number) or not low<=number<=high:raise ValueError("Receiver setting is outside its supported range")
    return number


def parse_speaker_settings(documents,status):
    roots={name:ET.fromstring(raw) for name,raw in documents.items()}
    for name,tag in {"configuration":"SpeakerConfig","crossovers":"Crossovers","bass":"Bass",
                     "distances":"Distances","levels":"Levels"}.items():
        if name in roots and roots[name].tag!=tag:raise ValueError("Unexpected receiver settings XML root: "+name)
    active=configured_channels(status)
    result={"read_only":True,"configured_channels":active,"raw_xml":dict(documents),"unknown_indices":{},
            "independent_subwoofer_filters_verified":False,"electrical_routing_verified":False}
    if "configuration" in roots:
        types={};unknown=[]
        for element in roots["configuration"].findall("Speaker"):
            index=int(element.get("index","-1"));value=element.findtext("Value")
            if index==2:
                result["configured_subwoofer_count"]={"6":1,"7":2,"8":3,"9":4,"1":0}.get(value)
            elif index in CONFIG_GROUPS:
                for channel in CONFIG_GROUPS[index]:
                    if channel in active:types[channel]={"1":"none","2":"small","3":"large"}.get(value,"unknown")
            elif index!=5:unknown.append(index)
        result.update(speaker_types=types,large_channels=[c for c,t in types.items() if t=="large"])
        result["unknown_indices"]["configuration"]=unknown
    if "crossovers" in roots:
        root=roots["crossovers"];selection=root.findtext("Selection")
        crossover={};unknown=[]
        if selection=="1":
            value=_number(root.findtext("All"),20,1000)
            crossover={channel:value for channel in active if not channel.startswith("SW")}
        elif selection=="2":
            for element in root.findall("List/Speaker"):
                if element.get("display")=="1":continue
                index=int(element.get("index","-1"))
                if index not in CROSSOVER_GROUPS:unknown.append(index);continue
                try:value=_number(element.text,20,1000)
                except (ValueError,TypeError):continue  # Keep full-band/unrecognized values in raw XML.
                for channel in CROSSOVER_GROUPS[index]:
                    if channel in active:crossover[channel]=value
        else:raise ValueError("Unrecognized receiver crossover selection mode")
        result.update(crossover_selection="individual" if selection=="2" else "all",crossovers=crossover,
                      selectable_crossovers_hz=[_number(e.text,20,1000) for e in root.findall("SelectableValue/List/Item")])
        result["unknown_indices"]["crossovers"]=unknown
    if "bass" in roots:
        root=roots["bass"];mode=root.findtext("SubwooferMode")
        result.update(bass_mode={"1":"lfe","2":"lfe_plus_main"}.get(mode),lfe_lowpass_hz=_number(root.findtext("LPFforLFE"),20,1000))
    if "distances" in roots:
        root=roots["distances"];unit=root.findtext("Unit")
        # The observed metric representation is centimetres. Other units stay
        # raw until independently decoded; they are not silently reinterpreted.
        values={};unknown=[]
        for element in root.findall("List/Speaker"):
            index=int(element.get("index","-1"))
            if not 0<=index<len(DISTANCE_CHANNELS):unknown.append(index);continue
            if unit=="1":values[DISTANCE_CHANNELS[index]]=_number(element.text,0,100000)/100
        result.update(distances_m=values,distance_unit_raw=unit)
        result["unknown_indices"]["distances"]=unknown
    if "levels" in roots:
        values={};unknown=[]
        for element in roots["levels"].findall("List/Speaker"):
            index=int(element.get("index","-1"))
            if not 0<=index<len(LEVEL_CHANNELS):unknown.append(index);continue
            values[LEVEL_CHANNELS[index]]=_number(element.text,-120,120)/10
        result["levels_db"]=values;result["unknown_indices"]["levels"]=unknown
    return result


def read_speaker_settings(host,status,base_url=None,timeout=2.5,*,sections=None,stop_on_error=False):
    from .receiver import private_host
    host=private_host(host)
    base_url=base_url or f"https://{'['+host+']' if ':' in host else host}:10443"
    parsed=urlparse(base_url)
    if parsed.scheme not in ["http","https"] or private_host(parsed.hostname)!=host or parsed.username or parsed.password:
        raise ValueError("Receiver web settings must use the selected LAN host")
    origin=f"{parsed.scheme}://{parsed.netloc}"
    # Self-signed local receiver certificates are accepted for these reads
    # only. No process-wide TLS defaults or authenticated sessions are changed.
    client=build_opener(ProxyHandler({}),HTTPSHandler(context=ssl._create_unverified_context()),NoRedirect)
    queries=[('configuration',3),('crossovers',6),('bass',7),('distances',4),('levels',5)]
    if sections is not None:
        if not isinstance(sections,(list,tuple,set)) or set(sections)-{n for n,_ in queries}:raise ValueError('Unknown read-only speaker settings section')
        queries=[q for q in queries if q[0] in sections]
    documents={};errors={}
    deadline=time.monotonic()+min(7.5,timeout*5)
    for index,(name,kind) in enumerate(queries):
        try:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ValueError("Read-only speaker settings time budget exhausted")
            with client.open(f"{origin}/ajax/speakers/get_config?type={kind}",timeout=min(timeout,remaining)) as response:
                raw=response.read(262145)
            if len(raw)>262144:raise ValueError("Receiver settings response is too large")
            documents[name]=raw.decode("utf-8")
        except (OSError,ValueError) as error:
            errors[name]=str(error)
            if stop_on_error:
                errors.update({n:'Skipped after a read failure; availability is unknown' for n,_ in queries[index+1:]})
                break
    parsed_settings=parse_speaker_settings(documents,status)
    return {**parsed_settings,"available":bool(documents),"origin":origin,"certificate_identity_validated":False,
            "errors":errors,"acoustic_capture_verified":False}


def joint_design_defaults(settings,channels):
    mains=[channel for channel in channels if not channel.startswith("SW")]
    crossovers=settings.get("crossovers",{})
    large=settings.get("large_channels",[])
    has_sub=any(channel.startswith("SW") for channel in channels)
    missing=[channel for channel in mains if channel not in crossovers and (channel not in large or settings.get("bass_mode")=="lfe_plus_main")]
    if has_sub and (missing or settings.get("bass_mode") is None or "lfe_lowpass_hz" not in settings):
        raise ValueError("Current receiver routing settings are incomplete for this layout: "+", ".join(missing))
    return {"crossovers":{channel:crossovers[channel] for channel in mains if channel in crossovers},
            "bass_mode":settings.get("bass_mode","lfe"),"large_channels":[channel for channel in large if channel in mains],
            "lfe_lowpass_hz":settings.get("lfe_lowpass_hz",120),
            "receiver_settings_reference":{"source":"read_only_web_settings","electrical_routing_verified":False}}
