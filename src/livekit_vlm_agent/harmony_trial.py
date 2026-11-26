"""Browser assets and invite authorization for an optional trial gateway."""

import hmac
import secrets

from aiohttp import web

from .config import PROJECT_ROOT

COOKIE = 'harmony_trial'
HEADERS = {
    'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    'Permissions-Policy': 'camera=(self), microphone=(self)',
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
}


def add_trial_routes(app, code, *, api_url=None, get_http=None, invites=None):
    if not invites and (len(code) < 20 or not code.isascii() or any(ch.isspace() for ch in code)):
        raise ValueError('HARMONY_TRIAL_CODE needs at least 20 non-whitespace ASCII characters')
    cookie_value = secrets.token_urlsafe(32)
    dist = PROJECT_ROOT / 'web/dist'

    def https(request):
        return request.secure or request.headers.get('X-Forwarded-Proto') == 'https'

    def same_origin(request):
        if https(request):
            scheme = 'https'
        elif request.host.partition(':')[0] in ('127.0.0.1', 'localhost'):
            scheme = 'http'
        else:
            return False
        return request.headers.get('Origin') == f'{scheme}://{request.host}'

    def authorized(request):
        if invites:
            return invites.from_cookie(request.cookies.get(COOKIE, ''))
        return hmac.compare_digest(request.cookies.get(COOKIE, '').encode('utf-8'), cookie_value.encode('ascii'))

    def browser_authorized(request):
        return same_origin(request) and authorized(request)

    async def index(_):
        html = (dist / 'index.html').read_text(encoding='utf-8')
        flags = 'data-trial data-private-trial' if invites else 'data-trial'
        html = html.replace('<html lang="zh-CN">', f'<html lang="zh-CN" {flags}>', 1)
        return web.Response(text=html, content_type='text/html', headers=HEADERS)

    async def asset(request):
        return web.FileResponse(dist / 'assets' / request.match_info['name'], headers=HEADERS)

    async def access(request):
        return web.Response(status=204 if authorized(request) else 401, headers=HEADERS)

    async def login(request):
        if not same_origin(request):
            return web.Response(status=403, headers=HEADERS)
        limit = 1024
        if request.content_length is None or request.content_length > limit:
            return web.Response(status=400, headers=HEADERS)
        supplied = await request.read()
        digest = invites.credential(supplied) if invites else None
        if not (digest if invites else hmac.compare_digest(supplied, code.encode('ascii'))):
            return web.Response(status=401, headers=HEADERS)
        response = web.Response(status=204, headers=HEADERS)
        response.set_cookie(COOKIE, invites.cookie(digest) if invites else cookie_value,
                            secure=https(request), httponly=True, samesite='Strict')
        return response

    async def capture(request):
        if not browser_authorized(request):
            return web.json_response({'detail': '请先输入试用码。'}, status=401, headers=HEADERS)
        headers = {name: request.headers[name] for name in ('Authorization', 'Content-Type')
                   if name in request.headers}
        async with get_http().post(api_url.rstrip('/') + request.path, data=await request.read(),
                                   headers=headers, allow_redirects=False) as response:
            return web.Response(body=await response.read(), status=response.status, content_type='application/json',
                                headers=HEADERS)

    app.router.add_get('/', index)
    app.router.add_get(r'/assets/{name:[\w-]+\.[\w]+}', asset)
    if api_url:
        app.router.add_post('/api/mobile-tests', capture)
        app.router.add_post(r'/api/mobile-tests/{run_id:[0-9a-f]{32}}/events', capture)
    app.router.add_get('/trial/access', access)
    app.router.add_post('/trial/access', login)
    return browser_authorized
