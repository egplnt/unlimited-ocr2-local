# -*- coding: utf-8 -*-
"""P0 验收：新旧逐件硬对比（汉字 / LaTeX / 数学符号 / 坐标残留 / 判级）。

判级口径（对齐 skill 的 ocr_symbol_gate.py）：
  L0a 空     汉字密度 < 20/页
  L1 定位档  可读 + 符号 = 0
  L2 全功能  符号保真 > 0
"""
import json
import os
import re
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

MF = 'work/manifest.json'
HAN = re.compile(r'[\u4e00-\u9fff]')
LAT = re.compile(r'\\frac|\\sqrt|\\mathrm|\\times|\\sin|\\cos|\\cdot|_\{|\^\{')
SYM = re.compile(r'[≥≤∈∫⊥∥≠≈±∑∏√∂∆]|\\[a-z]{2,}')
# 真污染＝「类型名 + 坐标」；数学区间（如 $[10,100]$、[25,35)）是真内容，不算污染
# 2026-10-29 教训：旧判据 \[[0-9]{2,4}, ?[0-9] 会把数学区间误判成坐标，统计虚高
_TYPES = (r'(?:header|title|subtitle|text|other|image|image_caption|page_number|'
          r'table|figure|caption|formula|equation|math|inline|block|paragraph|'
          r'list|list_item|footnote|chart|diagram|toc|code|reference)')
COORD = re.compile(r'\b' + _TYPES + r'[,.:：;、]?\s*\[[0-9,\s]*\]')
TAG = re.compile(r'<BLOCK>|<FORK>|<PAGE>|<LEVEL')


def probe(path):
    from pypdf import PdfReader
    rd = PdfReader(path)
    n = len(rd.pages)
    han = lat = sym = coord = tag = 0
    for p in rd.pages:
        t = p.extract_text() or ''
        han += len(HAN.findall(t))
        lat += len(LAT.findall(t))
        sym += len(SYM.findall(t))
        coord += len(COORD.findall(t))
        tag += len(TAG.findall(t))
    if n == 0:
        return dict(n=0, han=0, lat=0, sym=0, coord=0, tag=0, lvl='-')
    dens = han / n
    if dens < 20:
        lvl = 'L0a空'
    elif sym > 0:
        lvl = 'L2'
    else:
        lvl = 'L1'
    return dict(n=n, han=han, lat=lat, sym=sym, coord=coord, tag=tag, lvl=lvl)


def main():
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument('--manifest', default=MF)
    _a = _ap.parse_args()
    globals()['MF'] = _a.manifest
    mf = json.load(open(MF, encoding='utf-8'))
    print('%-46s %6s %6s %8s %8s %6s %6s' %
          ('文件名', '页数', '判级', '旧汉字', '新汉字', 'LaTeX', '坐标残留'))
    print('-' * 96)
    tot = dict(old_han=0, new_han=0, lat=0, coord=0, tag=0)
    up = 0
    for it in mf:
        try:
            o = probe(it['src'])
        except Exception:
            o = dict(n=0, han=0, lat=0, sym=0, coord=0, tag=0, lvl='?')
        try:
            nw = probe(it['out_pdf'])
        except Exception as e:
            print('!! 读新件失败 %s (%s)' % (os.path.basename(it['out_pdf'])[:40], str(e)[:40]))
            continue
        name = os.path.basename(it['out_pdf']).replace('_可检索_文心OCR2.pdf', '')
        print('%-46s %6d %6s %8d %8d %6d %6d' %
              (name[:46], nw['n'], nw['lvl'], o['han'], nw['han'],
               nw['lat'], nw['coord']))
        tot['old_han'] += o['han']
        tot['new_han'] += nw['han']
        tot['lat'] += nw['lat']
        tot['coord'] += nw['coord']
        tot['tag'] += nw['tag']
        if o['sym'] == 0 and nw['sym'] > 0:
            up += 1

    print('-' * 96)
    print('合计：旧汉字 %d → 新汉字 %d（%+.0f%%）｜LaTeX %d｜坐标残留 %d｜标签残留 %d'
          % (tot['old_han'], tot['new_han'],
             100.0 * (tot['new_han'] - tot['old_han']) / max(1, tot['old_han']),
             tot['lat'], tot['coord'], tot['tag']))
    print('判级提升（旧符号0 → 新符号>0）：%d 件' % up)


if __name__ == '__main__':
    main()
