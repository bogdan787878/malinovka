#!/usr/bin/env python3
# !!! С 2026-10-13 репозиторий — источник правды. Запуск перезапишет index.html и откатит правки клиента. Не запускать.
"""Step 1 of the Tilda sync: fetch the live page into _raw/index.html, download every
tildacdn asset (css/js/img/fonts) and write a localised index.html. Then run postprocess.py."""
import os, re, sys, gzip, zlib, urllib.request, urllib.parse, concurrent.futures as cf

ROOT = os.path.expanduser('~/dev/malinovka-park2')
SRC = os.path.join(ROOT, '_raw', 'index.html')
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36',
      'Accept-Encoding': 'identity'}

CDN = re.compile(r'https?://(?:static|thb|neo|ws|static3)\.tildacdn\.(?:com|net)(?:/[^\s"\'<>()\\,]*)?')


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = r.read()
            enc = r.headers.get('Content-Encoding', '')
    except Exception as e:
        return None, str(e)
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    return data, None


def classify(url):
    """-> (download_url, local_rel_path) or None to skip."""
    p = urllib.parse.urlparse(url)
    path = urllib.parse.unquote(p.path)
    host = p.netloc
    if path in ('', '/'):
        return None
    base = os.path.basename(path)
    if path.startswith('/css/') or (path.startswith('/ws/') and path.endswith('.css')):
        return f'https://static.tildacdn.com{path}', f'css/{base}'
    if path.startswith('/js/') or (path.startswith('/ws/') and path.endswith('.js')):
        return f'https://static.tildacdn.com{path}', f'js/{base}'
    if path.startswith('/fonts/'):
        return f'https://static.tildacdn.com{path}', f'fonts/{base}'
    m = re.match(r'/(tild[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/(.+)$', path)
    if m:
        tid, rest = m.groups()
        name = rest.split('/')[-1]
        return f'https://static.tildacdn.com/{tid}/{urllib.parse.quote(name)}', f'img/{tid}_{name}'
    return ('skip', path)


def main():
    os.makedirs(os.path.dirname(SRC), exist_ok=True)
    data, err = fetch('https://malinovka-park2.ru')
    if data is None:
        sys.exit('cannot fetch live page: ' + str(err))
    open(SRC, 'wb').write(data)
    html = data.decode('utf-8')
    urls = sorted(set(CDN.findall(html)))
    plan = {}          # download_url -> local path
    url2local = {}     # original url -> local path
    skipped = []
    for u in urls:
        c = classify(u)
        if c is None:
            continue
        if c[0] == 'skip':
            skipped.append(u)
            continue
        plan[c[0]] = c[1]
        url2local[u] = c[1]
    print(f'{len(urls)} urls -> {len(plan)} files to download, skipped {len(skipped)}')
    for s in skipped:
        print('  skip:', s)

    # Fonts referenced from CSS
    css_extra = {}
    def dl(item):
        u, rel = item
        out = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        data, err = fetch(u)
        if data is None:
            return rel, u, err
        open(out, 'wb').write(data)
        return rel, u, None

    failed = []
    with cf.ThreadPoolExecutor(12) as ex:
        for rel, u, err in ex.map(dl, plan.items()):
            if err:
                failed.append((u, err))

    # CSS: pull nested url()s (fonts), rewrite to relative
    for rel in [r for r in plan.values() if r.endswith('.css')]:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        css = open(path, encoding='utf-8').read()
        nested = set(CDN.findall(css))
        for nu in nested:
            c = classify(nu.split('?')[0])
            if not c or c[0] == 'skip':
                continue
            css_extra[c[0]] = c[1]
        def repl(m):
            c = classify(m.group(0).split('?')[0])
            if not c or c[0] == 'skip':
                return m.group(0)
            q = m.group(0)[len(m.group(0).split('?')[0]):]
            return '../' + c[1] + q
        css = CDN.sub(repl, css)
        open(path, 'w', encoding='utf-8').write(css)
    with cf.ThreadPoolExecutor(12) as ex:
        for rel, u, err in ex.map(dl, css_extra.items()):
            if err:
                failed.append((u, err))
    print(f'css-nested files: {len(css_extra)}')

    # Rewrite HTML
    def hrepl(m):
        u = m.group(0)
        if u in url2local:
            return url2local[u]
        return u
    new = CDN.sub(hrepl, html)
    open(os.path.join(ROOT, 'index.html'), 'w', encoding='utf-8').write(new)

    print('FAILED:', len(failed))
    for u, e in failed:
        print('  ', u, e)
    # leftover
    left = sorted(set(CDN.findall(new)))
    print('leftover tildacdn refs in html:', len(left))
    for u in left:
        print('  ', u)


main()
