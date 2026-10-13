"""Require credentials for direct remote API access and reject cross-site calls."""
from __future__ import annotations

import ipaddress
import os
import secrets
from urllib.parse import urlsplit

from fastapi import Request


def api_access_error(request: Request) -> str | None:
    if not request.url.path.startswith('/api/'):
        return None
    origin = request.headers.get('origin')
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return '不允许跨站点访问 API。'
        if parsed.scheme not in {'http', 'https'} or parsed.netloc.lower() != request.headers.get('host', '').lower():
            return '不允许跨站点访问 API。'
    host = request.client.host if request.client else ''
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if local:
        # Prevent DNS rebinding from turning a public site into a local API client.
        trusted = {'127.0.0.1', 'localhost', '::1'}
        trusted.update(item.strip().lower() for item in os.environ.get('FINAL_TRADE_TRUSTED_HOSTS', '').split(',') if item.strip())
        if (request.url.hostname or '').lower() in trusted:
            return None
    token = os.environ.get('FINAL_TRADE_API_TOKEN', '')
    supplied = request.headers.get('authorization', '')
    if token and secrets.compare_digest(supplied.encode('utf-8'), f'Bearer {token}'.encode('utf-8')):
        return None
    return '远程 API 访问需要有效凭据。'
