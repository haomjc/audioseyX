"""Atomic local run receipts with dataset binding and export integrity checks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid

from . import __version__
from .calibration_curves import curve_designs, fir_fingerprint
from .saved_controls import project_signature, safe_controls

_RECEIPT_COMMIT_LOCK=threading.Lock()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _payload_digest(document):
    payload = {k:v for k,v in document.items() if k != 'receipt_sha256'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def target_signature(target):
    if target is None:return None
    from .calibration_curves import checked_profile
    checked=checked_profile({'target_curve':target})['target_curve']
    return _payload_digest({k:checked[k] for k in ('frequency','level_db')})


def write_receipt(home, project, settings, status, *, design=None, archive=None, error=None):
    if status not in ('complete', 'failed', 'cancelled'):
        raise ValueError('Invalid design run status')
    home = Path(home).resolve()
    record = dict(schema='audioseyx-design-receipt-1', id=uuid.uuid4().hex,
        created_ns=time.time_ns(), software_version=__version__, status=status,
        project_signature=project_signature(project), controls=safe_controls(settings),
        receiver_written=False, original_receiver_EQ_backup_available=False,
        error=str(error)[:1000] if error is not None else None)
    if status == 'complete':
        target = Path(archive).resolve()
        if not target.is_relative_to(home/'exports') or not target.is_file():
            raise ValueError('A run receipt requires its completed local export')
        curves = curve_designs(design) if design.get('curve_designs') is not None else {'Flat':design}
        reference_profile=settings.get('curve_profiles',{}).get('Reference',{})
        record.update(archive=target.relative_to(home).as_posix(), archive_sha256=file_sha256(target),
            target_signatures={name:target_signature(curve['settings'].get('target_curve')) for name,curve in curves.items()},
            target_sources={name:('Reference' if name=='Reference' and 'target_curve' in reference_profile else 'Flat') for name in curves},
            FIR_sha256={name:{item['channel']:fir_fingerprint(item['taps']) for item in curve['channels']}
                        for name,curve in curves.items()},
            native_proposal=design.get('receiver_calibration', {}).get('proposal'),
            refinement=design.get('receiver_calibration', {}).get('eq_refinement'),
            training_positions=design.get('training_positions', []),
            validation_positions=design.get('validation_positions', []))
    directory = home/'design-history'
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory/(record['id']+'.tmp')
    with _RECEIPT_COMMIT_LOCK:
        committed=[]
        for path in directory.glob('*.json'):
            try:committed.append(read_receipt(home,path.stem))
            except (ValueError,OSError,TypeError,RecursionError):continue
        # Wall-clock resolution can be coarser than consecutive commits. Keep
        # the observed timestamp intact and assign a separate workspace order.
        record['commit_sequence']=max([len(committed)]+[r.get('commit_sequence') or 0 for r in committed])+1
        record['receipt_sha256'] = _payload_digest(record)
        try:
            temporary.write_text(json.dumps(record, allow_nan=False, indent=2), encoding='utf-8')
            temporary.replace(directory/(record['id']+'.json'))
        finally:
            temporary.unlink(missing_ok=True)
    return record


def read_receipt(home, identity):
    if not isinstance(identity, str) or re.fullmatch('[0-9a-f]{32}', identity) is None:
        raise ValueError('Invalid local design receipt ID')
    path = Path(home)/'design-history'/(identity+'.json')
    if not path.is_file() or path.stat().st_size > 2*1024**2:
        raise ValueError('The design receipt is missing or exceeds the size limit')
    def reject(value):raise ValueError('Non-finite design receipt')
    document = json.loads(path.read_text(encoding='utf-8'), parse_constant=reject)
    if not isinstance(document, dict) or document.get('schema') != 'audioseyx-design-receipt-1' or document.get('id') != identity or document.get('receipt_sha256') != _payload_digest(document):
        raise ValueError('The design receipt failed its integrity check')
    if document.get('status') not in ('complete','failed','cancelled') or not isinstance(document.get('created_ns'),int):
        raise ValueError('Invalid design receipt fields')
    sequence=document.get('commit_sequence')
    if sequence is not None and (isinstance(sequence,bool) or not isinstance(sequence,int) or sequence<1):
        raise ValueError('Invalid receipt commit sequence')
    return document


def history(home, project, limit=50):
    directory = Path(home)/'design-history'
    signature = project_signature(project) if project else None
    records, invalid = [], 0
    for path in directory.glob('*.json'):
        try:
            record = read_receipt(home, path.stem)
            records.append({k:record.get(k) for k in ('id','created_ns','commit_sequence','software_version','status','error','training_positions','validation_positions')}
                | dict(dataset_matches=record['project_signature'] == signature, export_available=bool(record.get('archive'))))
        except (ValueError, OSError, TypeError, RecursionError):
            invalid += 1
    records.sort(key=lambda row:(row.get('commit_sequence') or 0,row['created_ns'],row['id']),reverse=True)
    return dict(runs=records[:limit], total_runs=len(records), invalid_receipts=invalid,
        notice='Restoring controls never restores measurement consent or writes the receiver. Original factory EQ coefficients are not backed up by these run receipts.')


def restore_recipe(home, project, identity, current_targets=None):
    record = read_receipt(home, identity)
    if project is None or record['project_signature'] != project_signature(project):
        raise ValueError('This recipe belongs to different measurements, layout or saved receiver settings')
    if record['status'] != 'complete':
        raise ValueError('Only a completed design can supply a saved recipe')
    # Check the saved evidence before presenting the associated processing recipe.
    verified_archive(home, identity)
    current_targets=current_targets or {}
    if any(target_signature(current_targets.get(record.get('target_sources',{}).get(name,name)))!=signature for name,signature in record.get('target_signatures',{}).items()):
        raise ValueError('The active imported target differs from this recipe; first import the saved target from its verified design report')
    return dict(settings=safe_controls(record['controls']), automatic_calculation_started=False,
                receiver_written=False, confirmations_restored=False)


def verified_archive(home, identity):
    home = Path(home).resolve()
    record = read_receipt(home, identity)
    relative = record.get('archive')
    if not isinstance(relative, str):raise ValueError('This run has no completed export')
    target = (home/relative).resolve()
    if not target.is_relative_to(home/'exports') or not target.is_file() or file_sha256(target) != record.get('archive_sha256'):
        raise ValueError('The saved export is missing, outside the local export directory or changed')
    return target
