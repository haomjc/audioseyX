"""Explicit local input-level checks; no playback, gain changes or audio files."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from datetime import datetime,timezone
import math
import sys
import time
import numpy as np


def summarize_levels(samples,rate):
    values=np.asarray(samples,dtype=float)
    if values.ndim!=1 or not len(values) or not np.isfinite(values).all() or not 4000<=rate<=192000 or len(values)>rate*5:
        raise ValueError('A finite input window of at most five seconds is required')
    def db(value):return 20*math.log10(max(float(value),1e-12))
    size=int(rate//2)
    return dict(schema='audioseyx-input-level-1',observed_at_utc=datetime.now(timezone.utc).isoformat(),
        sample_rate_hz=rate,seconds=len(values)/rate,peak_dbfs=db(np.max(np.abs(values))),
        rms_dbfs=db(np.sqrt(np.mean(values**2))),clipped_samples=int(np.count_nonzero(np.abs(values)>=32767/32768)),
        half_second_rms_dbfs=[db(np.sqrt(np.mean(values[i:i+size]**2))) for i in range(0,len(values),size)],
        audio_file_saved=False,sound_played=False,settings_changed=False,
        microphone_calibrated=False,notice='Input levels only. Audio samples are discarded; absolute SPL, microphone response and transfer-function accuracy are not established.')


def _backend():
    if sys.platform!='win32':raise ValueError('The local level check currently supports Windows audio inputs only')
    mm=C.WinDLL('winmm')
    class Caps(C.Structure):
        _fields_=[('mid',W.WORD),('pid',W.WORD),('version',W.DWORD),('name',W.WCHAR*32),('formats',W.DWORD),('channels',W.WORD),('reserved',W.WORD)]
    class Format(C.Structure):
        _pack_=1
        _fields_=[('tag',W.WORD),('channels',W.WORD),('rate',W.DWORD),('bytes_sec',W.DWORD),('align',W.WORD),('bits',W.WORD),('extra',W.WORD)]
    class Header(C.Structure):
        _fields_=[('data',C.c_void_p),('length',W.DWORD),('recorded',W.DWORD),('user',C.c_size_t),('flags',W.DWORD),('loops',W.DWORD),('next',C.c_void_p),('reserved',C.c_size_t)]
    mm.waveInGetNumDevs.restype=W.UINT
    mm.waveInGetDevCapsW.argtypes=[C.c_size_t,C.POINTER(Caps),W.UINT]
    mm.waveInOpen.argtypes=[C.POINTER(C.c_void_p),W.UINT,C.POINTER(Format),C.c_size_t,C.c_size_t,W.DWORD]
    for name in ('waveInPrepareHeader','waveInAddBuffer','waveInUnprepareHeader'):
        getattr(mm,name).argtypes=[C.c_void_p,C.POINTER(Header),W.UINT]
    for name in ('waveInStart','waveInStop','waveInReset','waveInClose'):getattr(mm,name).argtypes=[C.c_void_p]
    mm.waveInGetErrorTextW.argtypes=[W.UINT,W.LPWSTR,W.UINT]
    return mm,Caps,Format,Header


def _checked(mm,code):
    if code:
        message=C.create_unicode_buffer(256);mm.waveInGetErrorTextW(code,message,256)
        raise ValueError(f'Audio input error {code}: {message.value}')


def list_inputs():
    mm,Caps,_,_=_backend();result=[]
    for index in range(mm.waveInGetNumDevs()):
        caps=Caps();_checked(mm,mm.waveInGetDevCapsW(index,C.byref(caps),C.sizeof(caps)))
        result.append(dict(index=index,name=caps.name,channels=caps.channels))
    return result


def select_input(devices,index=None):
    if index is not None:
        if isinstance(index,bool) or not isinstance(index,int):raise ValueError('Select an explicit recording-device index')
        candidates=[d for d in devices if d['index']==index]
    else:
        candidates=[d for d in devices if '麦克风' in d['name'] or 'microphone' in d['name'].lower()]
    if len(candidates)!=1:raise ValueError('Select one unambiguous microphone input before checking its level')
    return candidates[0]


def check_input_level(index=None):
    device=select_input(list_inputs(),index)
    mm,_,Format,Header=_backend();rate=48000
    fmt=Format(1,1,rate,rate*2,2,16,0)
    _checked(mm,mm.waveInOpen(None,device['index'],C.byref(fmt),0,0,1))  # Format query only.
    handle=C.c_void_p();raw=C.create_string_buffer(rate*5*2);prepared=False
    header=Header(C.cast(raw,C.c_void_p),len(raw),0,0,0,0,None,0)
    try:
        _checked(mm,mm.waveInOpen(C.byref(handle),device['index'],C.byref(fmt),0,0,0))
        _checked(mm,mm.waveInPrepareHeader(handle,C.byref(header),C.sizeof(header)));prepared=True
        _checked(mm,mm.waveInAddBuffer(handle,C.byref(header),C.sizeof(header)))
        _checked(mm,mm.waveInStart(handle));started=time.monotonic()
        while not header.flags&1 and time.monotonic()-started<5.1:time.sleep(.01)
        _checked(mm,mm.waveInStop(handle));_checked(mm,mm.waveInReset(handle))
    finally:
        if handle.value:
            mm.waveInReset(handle)
            if prepared:mm.waveInUnprepareHeader(handle,C.byref(header),C.sizeof(header))
            mm.waveInClose(handle)
    count=min(int(header.recorded),len(raw))//2
    if not count:raise ValueError('The microphone input returned no samples')
    report=summarize_levels(np.frombuffer(raw,dtype='<i2',count=count).astype(float)/32768,rate)
    report['device']=device
    return report
