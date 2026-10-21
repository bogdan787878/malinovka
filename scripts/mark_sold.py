#!/usr/bin/env python3
"""Пометить участки проданными: статус в js/plots-data.js + красная заливка в img/genplan.png.

    python3 scripts/mark_sold.py 44            # один участок
    python3 scripts/mark_sold.py 44 52 --preview /tmp/preview.png
    python3 scripts/mark_sold.py --free 15 --name "Участок 6,19 сотки"   # вернуть участок в продажу (зелёный)

Красная заливка нарисована в самой картинке схемы, поэтому одного статуса мало.
Заливка воспроизводит существующие красные участки: 0.27 * трава + 0.73 * (255, 45, 3),
белые цифры сохраняются, линии границ у края участка красятся целиком.
Режим --free обратный: красная заливка заменяется травой. Исходной травы под красным нет (заливка «запечена»
с блочными артефактами), поэтому трава синтезируется: средний цвет и зерно берутся из чистых участков соседних
свободных участков. Границей служат серые линии между участками (красная область участка = связная компонента,
разрезанная этими линиями), поэтому красные соседи не затрагиваются, а цифры и линии границ сохраняются.
Нужны: pillow, numpy, scipy, opencv-python, svgpathtools.
"""
import argparse
import os
import re
import sys

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from svgpathtools import parse_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PNG = os.path.join(ROOT, 'img', 'genplan.png')
PLOTS = os.path.join(ROOT, 'js', 'plots-data.js')
INDEX = os.path.join(ROOT, 'index.html')

# PNG 1680x858 показан под SVG 1626x832: 1625x830 со сдвигом на 1 px по X (см. index.html)
SX, SY = 1680 / 1625.0, 858 / 830.0
KEEP = 0.27
COLOR = np.array([255., 45., 3.])
RED_PIXEL = lambda a: (a[..., 0] > 170) & (a[..., 1] < 120) & (a[..., 2] < 100)


def load_polygons():
    html = open(INDEX, encoding='utf-8').read()
    svg = re.search(r'<svg id="genplan".*?</svg>', html, re.S).group(0)
    polys = {}
    for m in re.finditer(r'<path[^>]*?d="([^"]+)"[^>]*?class="(\d+)"', svg):
        pts = [((s.start.real - 1) * SX, s.start.imag * SY) for s in parse_path(m.group(1))]
        polys[m.group(2)] = np.array(pts, np.float32)
    return polys


def fill_mask(poly, shape, erode=0):
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillPoly(m, [np.round(poly).astype(np.int32)], 255)
    if erode:
        m = cv2.erode(m, np.ones((erode * 2 + 1,) * 2, np.uint8))
    return m


def soft_coverage(poly, shape, grow=1.0, ss=4):
    """антиалиасное покрытие полигона [0..1], расширенное на grow px (как у существующих красных)"""
    h, w = shape[:2]
    big = np.zeros((h * ss, w * ss), np.uint8)
    cv2.fillPoly(big, [np.round(poly * ss).astype(np.int32)], 255)
    k = int(round(grow * ss)) * 2 + 1
    big = cv2.dilate(big, np.ones((k, k), np.uint8))
    return cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.


def grass_like(a):
    """трава на схеме: R~108 G~130 B~12; отсекает дорогу, крышу КПП, живую изгородь"""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (r < 150) & (g > r + 8) & (g - b > 55)


def lot_region(rgb, poly_mask):
    """реальная область участка: трава внутри контура (контур в SVG местами шире видимой границы).
    Возвращает (область для заливки вместе с линиями границ, внутренность без границ для сохранения цифр)"""
    grass = (grass_like(rgb) & (poly_mask > 0)).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(grass, connectivity=4)
    keep = np.zeros_like(grass)
    biggest = stats[1:, cv2.CC_STAT_AREA].max()
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 0.05 * biggest:
            keep[lab == i] = 1
    closed = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))   # закрываем дыры от цифр
    region = cv2.dilate(closed, np.ones((5, 5), np.uint8))                          # +2 px: линии границ
    interior = cv2.erode(closed, np.ones((9, 9), np.uint8))                         # отступ 4 px от границы
    return region.astype(np.float32), interior.astype(np.float32)


def paint(rgb, cov, interior):
    """внутри участка (interior) белые цифры сохраняются, у границы линии красятся целиком"""
    s = (np.clip((rgb[..., 2] - 25.) / (255. - 25.), 0, 1) * interior)[..., None]
    grass = np.clip(np.where(s < 0.97, (rgb - s * 255.) / np.maximum(1 - s, 0.03), rgb), 0, 255)
    painted = (1 - s) * (KEEP * grass + (1 - KEEP) * COLOR) + s * 255.
    c = cov[..., None]
    return rgb * (1 - c) + painted * c


RED_LIKE = np.array([218., 69., 8.], np.float32)       # типичный цвет красной заливки
PREFERRED_DONORS = ['21', '23', '24', '26', '27', '28', '30', '36', '39', '41', '42']


def free_plots():
    text = open(PLOTS, encoding='utf-8').read()
    return {n for n, st in re.findall(r"\n\s*(\d+):\s*\{[^}]*status: '([^']*)'", text) if st == 'свободен'}


def synth_grass(rgb, polys, target, donors):
    """трава для участка target: средний цвет и зерно из чистых пикселей свободных участков-доноров"""
    h, w = rgb.shape[:2]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    dm = np.zeros((h, w), np.uint8)
    for n in donors:
        dm |= (fill_mask(polys[n], rgb.shape, erode=4) > 0).astype(np.uint8)
    clean = cv2.erode(((b < 100) & (r < 160) & (g > r) & (dm > 0)).astype(np.uint8), np.ones((5, 5), np.uint8))
    if clean.sum() < 2000:
        sys.exit('мало чистой травы у свободных участков для синтеза')
    mean = rgb[clean > 0].mean(0)

    def lowpass(a, m, sig):
        mm = m.astype(np.float32)
        return cv2.GaussianBlur(a * mm[..., None], (0, 0), sig) / np.maximum(cv2.GaussianBlur(mm, (0, 0), sig)[..., None], 1e-3)

    hp = rgb - lowpass(rgb, clean, 3.0)                       # «зерно» без цвета
    p = 20
    cand = np.argwhere(cv2.erode(clean, np.ones((p, p), np.uint8), anchor=(0, 0)) > 0)
    win = np.outer(np.hanning(p + 2)[1:-1], np.hanning(p + 2)[1:-1]).astype(np.float32)[..., None]
    rng = np.random.default_rng(int(target))
    acc = np.zeros((h, w, 3), np.float32)
    wsum = np.zeros((h, w, 1), np.float32)
    ys, xs = np.where(fill_mask(polys[target], rgb.shape) > 0)
    for y in range(ys.min() - p - 6, ys.max() + p + 6, p // 2):
        for x in range(xs.min() - p - 6, xs.max() + p + 6, p // 2):
            cy, cx = cand[rng.integers(len(cand))]
            y0, x0, y1, x1 = max(y, 0), max(x, 0), min(y + p, h), min(x + p, w)
            if y1 <= y0 or x1 <= x0:
                continue
            oy, ox = y0 - y, x0 - x
            acc[y0:y1, x0:x1] += (hp[cy:cy + p, cx:cx + p] * win)[oy:oy + y1 - y0, ox:ox + x1 - x0]
            wsum[y0:y1, x0:x1] += (win ** 2)[oy:oy + y1 - y0, ox:ox + x1 - x0]
    return np.clip(mean + acc / np.sqrt(np.maximum(wsum, 1e-3)), 0, 255)


def unpaint(rgb, polys, target, donors):
    """красный участок -> трава. Возвращает новое изображение или None, если участок не красный"""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    m_t = fill_mask(polys[target], rgb.shape) > 0
    red = ((r - g) > 120) & (b < 90)
    inside = cv2.erode(m_t.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    if (inside & red).sum() < 0.5 * inside.sum():
        return None
    n, lab = cv2.connectedComponents(red.astype(np.uint8), connectivity=4)
    cnt = np.bincount(lab[inside & red], minlength=n)
    cnt[0] = 0
    comp = lab == int(cnt.argmax())               # красная область именно этого участка (серые линии её ограничивают)
    full = ndi.binary_fill_holes(comp)            # + цифры и их антиалиасинг
    holes = full & ~comp
    others = red & ~comp
    d_c = cv2.distanceTransform((~full).astype(np.uint8), cv2.DIST_L2, 3)
    d_o = cv2.distanceTransform((~others).astype(np.uint8), cv2.DIST_L2, 3)
    fringe = (~full) & (d_c <= 2.5) & (d_c < d_o)  # антиалиасная кайма красного
    grass = synth_grass(rgb, polys, target, donors)
    out = rgb.copy()
    out[comp] = grass[comp]
    white = np.clip((b - 25.) / 230., 0, 1)[..., None]
    out[holes] = ((1 - white) * grass + white * 255.)[holes]
    rho = np.clip(((r - g) - 20) / 133., 0, 1)[..., None]   # красная доля пикселя каймы: убираем её, остальное оставляем
    exact = rgb + rho * (grass - RED_LIKE)
    k = np.clip((rho - 0.85) / 0.15, 0, 1)
    out[fringe] = ((1 - k) * exact + k * grass)[fringe]
    return out


def set_plot(plot, status, name=None):
    text = open(PLOTS, encoding='utf-8').read()
    pat = re.compile(r"(\n\s*%s:\s*\{ name: ')([^']*)(',\s*status: ')([^']*)(' \})" % re.escape(plot))
    m = pat.search(text)
    if not m:
        sys.exit('в plots-data.js нет участка %s' % plot)
    new_name = m.group(2) if name is None else name
    if new_name == m.group(2) and m.group(4) == status:
        return False
    width = max(len(n) for n in re.findall(r"name: '([^']*)'", text))
    rep = "%s%s',%s status: '%s%s" % (m.group(1), new_name, ' ' * (width - len(new_name)), status, m.group(5))
    open(PLOTS, 'w', encoding='utf-8').write(text[:m.start()] + rep + text[m.end():])
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plots', nargs='+', help='номера участков (class у <path> в SVG)')
    ap.add_argument('--free', action='store_true', help='вернуть участки в продажу (зелёные) вместо «проданы»')
    ap.add_argument('--name', help='текст в тултипе для --free, напр. "Участок 6,19 сотки"')
    ap.add_argument('--preview', help='сохранить сравнение «было / стало» в этот PNG')
    args = ap.parse_args()
    if args.name and (not args.free or len(args.plots) != 1):
        sys.exit('--name нужен только с --free и одним участком')

    polys = load_polygons()
    unknown = [p for p in args.plots if p not in polys]
    if unknown:
        sys.exit('нет таких участков на схеме: %s' % ', '.join(unknown))

    im = np.array(Image.open(PNG).convert('RGBA'))
    rgb = im[..., :3].astype(np.float32)
    alpha = im[..., 3]
    todo = []

    if args.free:
        out = rgb.copy()
        donors = [d for d in PREFERRED_DONORS if d in free_plots()] or sorted(free_plots() - set(args.plots))
        for p in args.plots:
            res_img = unpaint(out, polys, p, donors)
            if res_img is None:
                print('участок %s не красный в картинке, перекраска пропущена' % p)
                continue
            out = res_img
            todo.append(p)
        if todo:
            res = np.dstack([np.clip(np.round(out), 0, 255).astype(np.uint8), alpha])
            Image.fromarray(res).save(PNG, optimize=True)
        for p in args.plots:
            changed = set_plot(p, 'свободен', args.name)
            print('участок %s: %s, картинка %s' % (p, 'статус «свободен»' + (', название «%s»' % args.name if args.name else '')
                                                    if changed else 'статус уже «свободен»', 'перекрашена в зелёный' if p in todo else 'не менялась'))
    else:
        cov = np.zeros(rgb.shape[:2], np.float32)
        interior = np.zeros_like(cov)
        for p in args.plots:
            m = fill_mask(polys[p], rgb.shape)
            inside = rgb[m > 0]
            if RED_PIXEL(inside).mean() > 0.5:
                print('участок %s уже красный в картинке, перекраска пропущена' % p)
                continue
            region, inner = lot_region(rgb, m)
            # антиалиасинг края: размытая область, не шире контура участка (+1 px)
            soft = np.minimum(cv2.GaussianBlur(region, (0, 0), 0.7), soft_coverage(polys[p], rgb.shape))
            cov = np.maximum(cov, soft)
            interior = np.maximum(interior, cv2.GaussianBlur(inner, (0, 0), 1.0))
            todo.append(p)
        if todo:
            out = paint(rgb, cov, interior)
            res = np.dstack([np.clip(np.round(out), 0, 255).astype(np.uint8), alpha])
            Image.fromarray(res).save(PNG, optimize=True)
        for p in args.plots:
            changed = set_plot(p, 'забронирован')
            print('участок %s: статус %s, картинка %s' % (
                p, 'изменён на «забронирован»' if changed else 'уже был «забронирован»',
                'перекрашена' if p in todo else 'не менялась'))

    if args.preview and todo:
        tiles = []
        for p in todo:
            x0, y0 = (polys[p].min(0) - 60).clip(0).astype(int)
            x1, y1 = (polys[p].max(0) + 60).astype(int)
            b, a = rgb.astype(np.uint8)[y0:y1, x0:x1], res[y0:y1, x0:x1, :3]
            tiles.append(np.hstack([b, np.full((b.shape[0], 6, 3), 255, np.uint8), a]))
        w = max(t.shape[1] for t in tiles)
        sheet = np.vstack([np.pad(t, ((0, 8), (0, w - t.shape[1]), (0, 0)), constant_values=255) for t in tiles])
        Image.fromarray(cv2.resize(sheet, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)).save(args.preview)


if __name__ == '__main__':
    main()
