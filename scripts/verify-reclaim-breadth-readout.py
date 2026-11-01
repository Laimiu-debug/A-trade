"""Readout audit driver for the immutable breadth entry independent verifier.

The frozen main verifier accepts only two-key file references. The readout uses
a four-key verification descriptor. Adapt that one descriptor shape without
changing its file/hash checks, arithmetic, frozen main code or account proof.
"""
from argparse import ArgumentParser
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import traceback

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'scripts/verify-reclaim-breadth-entry.py'
REFERENCE_SHA = 'e7f00aac2db9e3037c79e3f87b797571efcc52027072f38b8614a6d7558eddcf'


def load_reference():
    import hashlib
    with REFERENCE.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != REFERENCE_SHA:
            raise ValueError('Frozen main independent verifier changed')
    spec = importlib.util.spec_from_file_location('frozen_breadth_entry_audit', REFERENCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_pin = module.pinned

    def descriptor_pin(value):
        if isinstance(value, dict) and set(value) == {'path', 'sha256', 'passed', 'meaning'}:
            module.require(value['passed'] is True and isinstance(value['meaning'], str), 'Readout verification descriptor')
            return original_pin({k: value[k] for k in ('path', 'sha256')})
        return original_pin(value)

    module.pinned = descriptor_pin
    return module


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'artifacts/strategy-search/reclaim-breadth-entry-v1')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    audit = load_reference()
    if args.self_test:
        audit.self_test()
        ref = {'path': REFERENCE.relative_to(ROOT).as_posix(), 'sha256': REFERENCE_SHA}
        audit.require(audit.pinned({**ref, 'passed': True, 'meaning': 'synthetic'}) == REFERENCE.resolve(), 'Descriptor reference adaptation')
        try:
            audit.pinned({**ref, 'passed': False, 'meaning': 'synthetic'})
        except ValueError:
            pass
        else:
            raise ValueError('Failed verification descriptor admitted')
        print(json.dumps({'passed': True, 'descriptorAdaptationTests': 2}), flush=True)
        return
    directory = args.directory.resolve()
    try:
        audit.verify_readout(directory)
        path = directory / 'readout-validation.json'
        result = audit.read(path)
        result['calculationVerifierSha256'] = result['verifierSha256']
        result['verifierSha256'] = audit.sha(__file__)
        result['independentReferences'][REFERENCE.relative_to(ROOT).as_posix()] = REFERENCE_SHA
        result['descriptorAdaptation'] = 'Strict four-field passed/meaning/path/sha256 descriptor adapted to original exact two-field path/sha256 check; no arithmetic change.'
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps({'passed': True, 'path': str(path), 'sha256': audit.sha(path),
                          'verifierSha256': audit.sha(__file__), 'counts': result['counts']}), flush=True)
    except Exception as error:
        folder = directory / 'validation-attempts'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        failure = {'passed': False, 'at': stamp, 'scope': 'readout-driver', 'verifierSha256': audit.sha(__file__),
                   'calculationVerifierSha256': REFERENCE_SHA, 'error': str(error), 'traceback': traceback.format_exc()}
        path = folder / (stamp + '-failed.json')
        path.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'passed': False, 'failure': str(path), 'error': str(error)}), flush=True)
        raise


if __name__ == '__main__':
    main()
