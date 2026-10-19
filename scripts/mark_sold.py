#!/usr/bin/env python3
"""Пометить участки проданными: статус в js/plots-data.js + красная заливка в img/genplan.png.

    python3 scripts/mark_sold.py 44            # один участок
    python3 scripts/mark_sold.py 44 52 --preview /tmp/preview.png

Красная заливка нарисована в самой картинке схемы, поэтому одного статуса мало.
Заливка воспроизводит существующие красные участки: 0.27 * трава + 0.73 * (255, 45, 3),
белые цифры сохраняются, линии границ у края участка красятся целиком.
Нужны: pillow, numpy, opencv-python, svgpathtools.
"""
import argparse
import os
import re
import sys

import cv2
import numpy as np
from PIL import Image
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


def set_status(plot):
    text = open(PLOTS, encoding='utf-8').read()
    pat = re.compile(r"(\n\s*%s:\s*\{ name: '[^']*',\s*status: ')([^']*)(' \})" % re.escape(plot))
    m = pat.search(text)
    if not m:
        sys.exit('в plots-data.js нет участка %s' % plot)
    if m.group(2) == 'забронирован':
        return False
    open(PLOTS, 'w', encoding='utf-8').write(pat.sub(r"\1забронирован\3", text, count=1))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plots', nargs='+', help='номера участков (class у <path> в SVG)')
    ap.add_argument('--preview', help='сохранить сравнение «было / стало» в этот PNG')
    args = ap.parse_args()

    polys = load_polygons()
    unknown = [p for p in args.plots if p not in polys]
    if unknown:
        sys.exit('нет таких участков на схеме: %s' % ', '.join(unknown))

    im = np.array(Image.open(PNG).convert('RGBA'))
    rgb = im[..., :3].astype(np.float32)
    alpha = im[..., 3]
    cov = np.zeros(rgb.shape[:2], np.float32)
    interior = np.zeros_like(cov)
    todo = []
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
        if args.preview:
            tiles = []
            for p in todo:
                x0, y0 = (polys[p].min(0) - 60).clip(0).astype(int)
                x1, y1 = (polys[p].max(0) + 60).astype(int)
                b, a = rgb.astype(np.uint8)[y0:y1, x0:x1], res[y0:y1, x0:x1, :3]
                tiles.append(np.hstack([b, np.full((b.shape[0], 6, 3), 255, np.uint8), a]))
            w = max(t.shape[1] for t in tiles)
            sheet = np.vstack([np.pad(t, ((0, 8), (0, w - t.shape[1]), (0, 0)), constant_values=255) for t in tiles])
            Image.fromarray(cv2.resize(sheet, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)).save(args.preview)

    for p in args.plots:
        changed = set_status(p)
        print('участок %s: статус %s, картинка %s' % (
            p, 'изменён на «забронирован»' if changed else 'уже был «забронирован»',
            'перекрашена' if p in todo else 'не менялась'))


if __name__ == '__main__':
    main()
