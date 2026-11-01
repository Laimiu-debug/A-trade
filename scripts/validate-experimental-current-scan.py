"""Opt-in real current scan; no account replay, orders or parameter research."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.experimental_scan_service import ExperimentalScanService


def pins(workspace):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in workspace.iterdir()
            if p.is_file() and p.suffix in ('.json', '.sqlite3')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = pins(args.workspace)
    service = ExperimentalScanService(args.workspace)
    started, prior = time.monotonic(), None
    try:
        task = service.create({'requestId': 'user-requested-current-screen-' + args.output.name})
        while True:
            value = service.get({'id': task['id']})
            current = (value['task']['status'], value['task'].get('progress', {}).get('phase'))
            if current != prior:
                print(json.dumps({'id':task['id'], 'status':current[0], 'phase':current[1]}, ensure_ascii=False), flush=True)
                prior = current
            if current[0] not in ('queued', 'running', 'stopping'):
                break
            if time.monotonic() - started > 900:
                service.stop({'id':task['id']})
                raise TimeoutError('Current scan exceeded bounded validation time')
            time.sleep(.25)
        after = pins(args.workspace)
        receipt = {'passed': value['task']['status'] == 'completed', 'task': value['task'],
            'workspace': str(args.workspace.resolve()), 'existingRootDataUnchanged': before == after,
            'protectedBefore': before, 'protectedAfter': after,
            'elapsedSeconds': time.monotonic() - started, 'financialBacktestsRun': 0,
            'sourceCodePins': {str(p.relative_to(ROOT)).replace('\\','/'): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [ROOT/'service/experimental_scan.py', ROOT/'service/experimental_scan_service.py', ROOT/'service/experimental_strategies.py']}}
        (args.output / 'verification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        if 'result' in value:
            (args.output / 'result.json').write_text(json.dumps(value['result'], ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'passed': receipt['passed'], 'dataUnchanged':before == after,
                'asOf':value['result']['asOfDate'], 'stale':value['result']['stale'],
                'coverage':value['result']['coverage'], 'environment':value['result']['environment']['label'],
                'signals':{s['strategyId']:s['counts'] for s in value['result']['strategies']}}, ensure_ascii=False))
        if not receipt['passed'] or before != after:
            raise RuntimeError(value['task'].get('error', 'Source data changed during validation'))
    finally:
        service.close()


if __name__ == '__main__':
    main()
