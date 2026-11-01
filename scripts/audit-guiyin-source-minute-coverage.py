"""Check exact source entry dates in local LC1/LC5. No price strategy or returns."""
import hashlib
import json
from pathlib import Path
import struct
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/strategy-search/guiyin-dual-path-system-v1/attempt-001'
SOURCE=ROOT/'artifacts/strategy-search/public-trading-evidence-v1/guiyin-ledger-validation-001/canonical-closed-cycles.json'
TDX=Path('E:/TDX')


def decode(packed):
    rest=int(packed)&2047
    return f'{(int(packed)>>11)+2004:04d}-{rest//100:02d}-{rest%100:02d}'


def main():
    cycles=[e for e in json.loads(SOURCE.read_text(encoding='utf-8')) if e['securityType']=='equity' and e['entryDate']>='2024-09-24']
    cache={};rows=[]
    dtype=np.dtype([('packed','<u2'),('minute','<u2'),('rest','V28')])
    for e in cycles:
        code=e['code'];market='sh' if code.startswith('6') else 'sz' if code.startswith(('0','3')) else 'bj'
        result=dict(episodeId=e['episodeId'],code=code,name=e['name'],entryDate=e['entryDate'])
        for label,folder,ext in [('oneMinute','minline','lc1'),('fiveMinute','fzline','lc5')]:
            path=TDX/'vipdoc'/market/folder/f'{market}{code}.{ext}'
            if path not in cache:
                if not path.is_file():cache[path]=dict(path=str(path),exists=False,entryDates={})
                else:
                    before=path.stat();raw=path.read_bytes();after=path.stat()
                    assert (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns) and len(raw)%32==0
                    records=np.frombuffer(raw,dtype=dtype)
                    times=records['packed'].astype(np.int64)*1440+records['minute']
                    monotone=bool((np.diff(times)>=0).all());assert monotone
                    dates,counts=np.unique(records['packed'],return_counts=True)
                    cache[path]=dict(path=str(path),exists=True,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),
                                     firstDate=decode(dates[0]),lastDate=decode(dates[-1]),timestampsNondecreasing=monotone,
                                     entryDates={decode(d):int(c) for d,c in zip(dates,counts)})
            source=cache[path];count=source.get('entryDates',{}).get(e['entryDate'],0)
            result[label]=dict(path=str(path),exists=source['exists'],exactEntryDateBars=count,firstDate=source.get('firstDate'),lastDate=source.get('lastDate'))
        rows.append(result)
    report=dict(schema='guiyin-source-exact-entry-minute-coverage-v1',originalCycles=134,
                oneMinuteEntryDatesPresent=sum(r['oneMinute']['exactEntryDateBars']>0 for r in rows),
                fiveMinuteEntryDatesPresent=sum(r['fiveMinute']['exactEntryDateBars']>0 for r in rows),
                files=[{k:v for k,v in c.items() if k!='entryDates'} for c in cache.values()],rows=rows,
                limitations=['This checks exact record date availability and timestamp order; it does not establish intraday execution capacity or source order times.',
                             'File modification date is not market-data cutoff; actual minute-record dates are used.'],newStrategyTrials=0,newAccounts=0)
    with (OUT/'source-minute-coverage.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:report[k] for k in ['originalCycles','oneMinuteEntryDatesPresent','fiveMinuteEntryDatesPresent']},ensure_ascii=False))


if __name__=='__main__':main()
