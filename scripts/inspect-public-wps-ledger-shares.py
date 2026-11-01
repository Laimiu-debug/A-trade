"""Read public share metadata; never retain sessions or work around login gates."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1/wps-share-metadata-001'
INBOX = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1/original-ledgers-inbox'
SHARES = [
    ('归因', 1, 'cm5xnCnUZCIZ'),
    ('ElpMis', 2, 'cf05osthzXS4'),
    ('LBFLBF', 3, 'chRGA9ZPSVUJ'),
    ('二池', 6, 'cntVsLnAM9Kn'),
]


def main():
    if OUT.exists():
        raise ValueError('Preserve prior source inspection')
    records = []
    for name, rank, link in SHARES:
        # The public SSO page's failCB sets this completion marker itself.
        # It is not a login credential; real login gates remain enforced.
        session = requests.Session()
        session.cookies.set('hadSingleSign', 'true', domain='.kdocs.cn', path='/')
        url = 'https://www.kdocs.cn/l/' + link
        response = session.get(url, timeout=25)
        response.raise_for_status()
        response.encoding = 'utf-8'
        marker = 'window.__WPSENV__='
        start = response.text.index(marker) + len(marker)
        env, _ = json.JSONDecoder().raw_decode(response.text[start:])
        file = env['file_info']['file']
        attrs = file.get('attrs', {})
        record = {
            'trader': name, 'competitionRank': rank,
            'competition': '第六届王者百万杯半年赛', 'shareUrl': url,
            'organizerSource': 'https://m.tgb.cn/a/2eJDqpyCdSK-' + ('2' if rank == 6 else '3') + '?type=new',
            'httpStatus': response.status_code, 'fileId': str(file['id']),
            'filename': file['name'], 'advertisedSizeBytes': file.get('size'),
            'originalVersion': file.get('version'),
            'createdEpoch': file.get('create_time'), 'modifiedEpoch': file.get('modify_time'),
            'officeType': env.get('office_type'), 'linkRange': attrs.get('link_range'),
            'acl': {key: file.get('user_acl', {}).get(key) for key in ('read', 'download')},
            'sourceFsha': attrs.get('fsha'), 'sourceFshaAlgorithmVerified': False,
            'contentsAcquired': False, 'ledgerCompletenessVerified': False,
        }
        # One check of the exact anonymous-download route in the site's public
        # DownloadSingle/common modules. Stop at the authentication response;
        # do not try alternate domains, exports, guessed URLs or credentials.
        if rank == 1 and record['acl']['download'] == 1:
            api = 'https://www.kdocs.cn/api/office/file/' + record['fileId'] + '/download'
            download = session.get(api, headers={'Referer': url}, timeout=25)
            body = download.json()
            record['downloadCheck'] = {
                'endpoint': api, 'httpStatus': download.status_code,
                'errno': body.get('errno'), 'result': body.get('result'),
                'loginRequired': body.get('result') == 'userNotLogin',
                'originalBytesSaved': False,
            }
            if not record['downloadCheck']['loginRequired']:
                raise ValueError('Unexpected download response: review manually without storing response secrets')
        records.append(record)
        print(json.dumps({key: record[key] for key in ('trader', 'filename', 'advertisedSizeBytes', 'acl', 'contentsAcquired')}, ensure_ascii=True))
        session.close()
    payload = {
        'schema': 'public-wps-share-inspection-v1',
        'inspectedAt': datetime.now(timezone.utc).isoformat(),
        'inspector': {'path': str(Path(__file__).relative_to(ROOT)), 'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        'publicMetadataOnly': True, 'records': records,
        'wholeLedgersAcquired': 0, 'verifiedClosedEpisodes': 0,
        'authenticatedBrowserPagesRead': False,
        'browserRuntimeError': 'failed to write kernel assets: path not found (os error 3)',
        'userCredentialsOrBrowserCookiesRead': False,
        'transientPageOrSessionDataPersisted': False,
        'downloadAuthenticationGateRespected': True,
        'localOriginalDownloadInbox': str(INBOX),
        'targetAchieved': False,
    }
    OUT.mkdir(parents=True)
    INBOX.mkdir(exist_ok=True)
    (OUT / 'readout.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'readout': str(OUT / 'readout.json'), 'wholeLedgersAcquired': 0}))


if __name__ == '__main__':
    main()
