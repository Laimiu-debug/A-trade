"""Archive all public author screenshots on the two observed Q4 reply pages."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import bs4
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1/mangfu-risk-window-001'
PAGES = [
    'https://m.tgb.cn/a/2daTrvW8us2-9?type=',
    'https://m.tgb.cn/a/2daTrvW8us2-8?type=',
]


def main():
    if OUT.exists():
        raise ValueError('Preserve prior original sources')
    OUT.mkdir(parents=True)
    session = requests.Session()
    pages, posts, images, seen = [], [], [], set()
    for page_url in PAGES:
        response = session.get(page_url, timeout=25)
        response.raise_for_status()
        response.encoding = 'utf-8'
        soup = bs4.BeautifulSoup(response.text, 'html.parser')
        pages.append({'url': page_url, 'httpStatus': response.status_code,
                      'responseSha256': hashlib.sha256(response.content).hexdigest(),
                      'responseRetained': False})
        for node in soup.select('.plItem'):
            author = node.select_one('.plName')
            hidden = node.select_one('[contentid][userid]')
            when = node.select_one('.pl_time')
            body = node.select_one('.pl_text')
            if hidden is None or hidden.get('userid') != '2210094':
                raise ValueError('Expected the original author only on this bounded page')
            if not (author and when and body):
                raise ValueError('Missing public post field')
            stamp = datetime.strptime(when.get_text(strip=True), '%y-%m-%d %H:%M').isoformat(timespec='minutes')
            post_id = hidden['contentid']
            record = {'replyId': post_id, 'author': author.get_text(strip=True),
                      'publishedAtLocal': stamp, 'timezone': 'Asia/Shanghai',
                      'sourcePage': page_url,
                      'bodySha256': hashlib.sha256(body.get_text(' ', strip=True).encode()).hexdigest(),
                      'bodyRetained': False, 'imagePaths': []}
            for ordinal, img in enumerate(body.select('img[data-type="contentImage"]'), 1):
                url = img.get('src2') or img.get('data-original')
                if not url or urlparse(url).scheme != 'https' or urlparse(url).hostname != 'image.tgb.cn':
                    raise ValueError('Use only exact public original-image URLs in the observed DOM')
                if url in seen:
                    raise ValueError('Unexpected repeated image: preserve and review rather than silently deduplicate')
                original = session.get(url, headers={'Referer': page_url}, timeout=25)
                original.raise_for_status()
                raw = original.content
                if raw.startswith(b'\x89PNG\r\n\x1a\n'):
                    suffix = '.png'
                elif raw.startswith(b'\xff\xd8\xff'):
                    suffix = '.jpg'
                else:
                    raise ValueError('Unexpected source image format')
                filename = stamp[:10] + '-' + post_id + '-' + str(ordinal) + suffix
                path = OUT / filename
                path.write_bytes(raw)
                image = {'file': str(path.relative_to(ROOT)).replace('\\', '/'),
                         'url': url, 'httpStatus': original.status_code, 'bytes': len(raw),
                         'sha256': hashlib.sha256(raw).hexdigest(), 'replyId': post_id,
                         'publishedAtLocal': stamp, 'sourcePage': page_url,
                         'transcribed': False}
                images.append(image)
                record['imagePaths'].append(image['file'])
                seen.add(url)
            posts.append(record)
    result = {'schema': 'public-author-risk-window-images-v1',
              'acquiredAt': datetime.now(timezone.utc).isoformat(),
              'collector': {'path': str(Path(__file__).relative_to(ROOT)).replace('\\', '/'),
                            'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
              'authorProfile': 'https://m.tgb.cn/blog/2210094', 'authorUserId': '2210094',
              'selection': 'All author replies and images on observed pages 9 and 8; excludes the repeated main-post image.',
              'pages': pages, 'posts': posts, 'images': images,
              'wholeLedger': False, 'accountAuthenticationIndependentlyVerified': False,
              'accountReturnOrPayoffCalculated': False, 'targetAchieved': False}
    (OUT / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'posts': len(posts), 'images': len(images), 'manifest': str(OUT / 'manifest.json')}))


if __name__ == '__main__':
    main()
