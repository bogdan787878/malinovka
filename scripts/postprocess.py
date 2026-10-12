#!/usr/bin/env python3
"""Post-process the localised Tilda index.html (run after mirror_tilda.py).

Strips Tilda-only runtime, moves analytics to js/analytics.js, replaces the inline
map code with js/map.js + js/plots-data.js + css/map.css, and patches the Tilda
form scripts so they load their helpers from ./js instead of static.tildacdn.com.
Idempotent for the JS patches; expects a fresh mirror_tilda.py output for index.html.
"""
import os, re, subprocess, sys
ROOT = os.path.expanduser('~/dev/malinovka-park2')
os.chdir(ROOT)
h = open('index.html', encoding='utf-8').read()


def pop_script(marker, h, required=True):
    for m in re.finditer(r'<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>\s*', h, re.S):
        if marker in m.group(1):
            return h[:m.start()] + h[m.end():], m.group(1)
    if required:
        sys.exit('marker not found: ' + marker)
    return h, ''


# 1. analytics -> js/analytics.js (only runs on the production domain)
h, roistat = pop_script('roistatProjectId', h)
h, metrika = pop_script('mc.yandex.ru/metrika/tag.js', h)
h, roitimer = pop_script('RoiTildaTimer', h)
for junk in ('Инициализация кода счетчика', 'Используем метод push', 'tildastatscript'):
    h, _ = pop_script(junk, h, required=False)
h, _ = pop_script('window.dataLayer=window.dataLayer||[];', h, required=False)
h = re.sub(r'<script src="js/tilda-fallback[^>]*></script>\s*', '', h)
h = re.sub(r'<link rel="dns-prefetch"[^>]*>\s*', '', h)
for f in ('js/tilda-fallback-1.0.min.js', 'js/tilda-stat-1.0.min.js'):
    if os.path.exists(f):
        os.remove(f)
open('js/analytics.js', 'w', encoding='utf-8').write("""/* Аналитика: работает только на боевом домене, чтобы локальная разработка не накручивала статистику. */
(function () {
  var prod = ['malinovka-park2.ru', 'www.malinovka-park2.ru'];
  if (prod.indexOf(location.hostname) === -1) return;

  window.dataLayer = window.dataLayer || [];

  /* Roistat */
%s

  /* Яндекс.Метрика */
%s

  /* Roistat: подмешивает roistat_url / roistat_fields_roistat во все формы */
%s
})();
""" % (roistat.strip(), metrika.strip(), roitimer.strip()))

# 2. map: inline scripts/style -> js/map.js, js/plots-data.js, css/map.css
for marker in ("getElementById('genplan');if(!svg)", "const tooltip=document.getElementById('tooltip')",
               "tooltipButton.addEventListener('click'"):
    h, _ = pop_script(marker, h)
h2 = re.sub(r'<!-- nominify begin -->\s*<style>\s*svg path \{.*?</style>\s*<!-- nominify end -->\s*', '', h, count=1, flags=re.S)
assert h2 != h, 'map <style> not found'
h = h2
h = re.sub(r'(<link rel="stylesheet" href="css/fonts-tildasans\.css"[^>]*/>)',
           r'\1\n<link rel="stylesheet" href="css/map.css" type="text/css" media="all" />', h, count=1)
assert 'css/map.css' in h
h = h.replace('</body>', '<script src="js/plots-data.js" defer></script>\n<script src="js/map.js" defer></script>\n'
                          '<script src="js/analytics.js" defer></script>\n</body>', 1)

# 3. custom properties with url(): resolved relative to the CSS file that uses them
h = re.sub(r"(--[a-z-]+:url\(')img/", r"\1../img/", h)
open('index.html', 'w', encoding='utf-8').write(h)


# 4. Tilda form scripts: load helpers locally
def patch(path, old, new, minimum=1):
    s = open(path, encoding='utf-8').read()
    n = s.count(old)
    if n == 0:
        print('  (already patched)', path)
        return
    assert n >= minimum, (path, old, n)
    open(path, 'w', encoding='utf-8').write(s.replace(old, new))
    print('  patched', path, n)

patch('js/tilda-forms-1.0.min.js', '"https://static.tildacdn."+t_forms__getRootZone()+"/js/', '"js/', 3)
patch('js/tilda-zero-forms-1.0.min.js', 'd="https://static.tildacdn."+t_zeroForms__getRootZone();', 'd=".";')
patch('js/tilda-phone-mask-1.1.min.js',
      "https://static.tildacdn.\"+t_form_phonemask_getRootZone()+'/lib/flags/flags7.png", "img/flags7.png\"+'")
for url, dst in (('https://static.tildacdn.com/lib/flags/flags7.png', 'img/flags7.png'),
                 ('https://static.tildacdn.com/js/tilda-forms-custommask-1.0.min.js', 'js/tilda-forms-custommask-1.0.min.js')):
    if not os.path.exists(dst):
        subprocess.run(['curl', '-sL', '--compressed', '-o', dst, url], check=True)
for f in ('js/tilda-forms-1.0.min.js', 'js/tilda-zero-forms-1.0.min.js', 'js/tilda-phone-mask-1.1.min.js',
          'js/analytics.js', 'js/map.js', 'js/plots-data.js'):
    subprocess.run(['node', '--check', f], check=True)
left = sorted(set(re.findall(r'https?://[a-z0-9.\-]*tildacdn[^\s"\'<>)]*', h)))
print('remaining tildacdn refs in index.html:', left)
