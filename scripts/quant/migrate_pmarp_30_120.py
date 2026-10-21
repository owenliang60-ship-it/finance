#!/usr/bin/env python3
"""Review/dry-run by default; --apply changes only the audited Quant source.

Run on the host containing the scanner. Never prints source or credentials.
Finance and Quant must be deployed together because breadth checks their parameters.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

EXPECTED_SOURCE_SHA256 = '2759f2f23bedf4ce72e2e797adb0d3f03e6055eb8ebf971fd2eea7555859f8f4'
REPLACEMENTS = (
    (b'    "ema_period": 20,', b'    "ema_period": 30,'),
    (b'    "lookback": 150,', b'    "lookback": 120,'),
    (b'def calculate_pmarp(prices: pd.Series, ema_period: int = 20, lookback: int = 150) -> pd.Series:',
     b'def calculate_pmarp(prices: pd.Series, ema_period: int = 30, lookback: int = 120) -> pd.Series:'),
)


def transform(source):
    lines=source.splitlines()
    for old,new in REPLACEMENTS:
        if lines.count(old)!=1 or source.count(old)!=1:
            raise ValueError('Expected one exact parameter line; scanner requires fresh review')
        source=source.replace(old,new,1)
    return source


def migrate(path,apply=False):
    path=Path(path);source=path.read_bytes();before=hashlib.sha256(source).hexdigest()
    if before!=EXPECTED_SOURCE_SHA256:
        raise ValueError('Source hash differs from audited scanner; refusing mutation')
    proposed=transform(source)
    compile(proposed,str(path),'exec') # Syntax only; no imports/API requests/notifications.
    result={'status':'ready','path':str(path),'before_sha256':before,'after_sha256':hashlib.sha256(proposed).hexdigest(),'changed_lines':3,'ema_period':30,'lookback':120}
    if not apply:return result
    mode=path.stat().st_mode&0o777
    if path.read_bytes()!=source:raise ValueError('Source changed while preparing migration')
    backup=path.with_suffix(path.suffix+'.pre-pmarp-30-120.bak')
    fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(source);stream.flush();os.fsync(stream.fileno())
    fd,name=tempfile.mkstemp(prefix=path.name+'.pmarp-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(proposed);stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),mode)
        if path.read_bytes()!=source:raise ValueError('Source changed before replacement')
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
    result.update(status='applied',backup=str(backup));return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('scanner',type=Path);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:result=migrate(args.scanner,args.apply)
    except (ValueError,OSError,SyntaxError) as exc:
        print(json.dumps({'status':'refused','reason':str(exc)},ensure_ascii=False));return 1
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
