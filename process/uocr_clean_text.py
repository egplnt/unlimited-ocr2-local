# -*- coding: utf-8 -*-
"""
uocr_clean_text.py —— 清洗文心 Unlimited OCR 2 输出中的结构标记

背景
----
Unlimited OCR 2 的原始输出混有版面结构标记，共三种形态：

  1) 完整标记（有价值，必须保留其后文本）：
       <BLOCK>title<LEVEL1>[219, 123, 560, 147]<FORK>4.2 变加速直线运动的运动学问题
       <BLOCK>text[110, 610, 935, 713]<FORK>3. 电车的起点站到终点站的距离是 6.0 千米
     → 剥掉 "<BLOCK>type<LEVELn>[坐标]<FORK>" 前缀，保留后面的真实正文。

  2) 损坏标记（模型识别失败的碎片，无 <FORK>，内容是零星垃圾）：
       <BLOCK>other[111, 371, 862, 507]wezig
       <BLOCK>other[209, 444, 714, 544]сив        ← 西里尔字母
       <BLOCK>image_caption[M]                     ← 单字符
     → 整段剔除（实盘核验：这类碎片无一含完整中文语义）。

  3) 页标记 <PAGE> → 剔除。

实盘依据（2026-10-28，《质点运动的秘密》拍照版 229 页）
-----------------------------------------------------
  full_text.txt 共 117 行含标记：完整标记 16 个、垃圾碎片 101 个。
  碎片内容经逐条核验为 wezig / сив / riture / M / C / [注] / 宇智 等零星字符，
  剔除无信息损失。

用法
----
  python uocr_clean_text.py --input full_text.txt --output clean.txt
  python uocr_clean_text.py --input full_text.txt --output clean.md --keep-headings
  python uocr_clean_text.py --input single_results_full.json --output clean.txt   # 直接吃 json

参数
----
  --input          输入：清洗前的 .txt 全文，或 single_results_full.json
  --output         输出干净文件
  --keep-headings  把 title 类标记按 LEVEL 转成 markdown 标题（#/##/###/####）
  --mark-images    把 image / image_caption 位置标为 [图]（默认关闭）
"""
import argparse
import json
import os
import re
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 完整标记：<BLOCK>type<LEVELn?>[坐标]<FORK>正文
RE_FULL = re.compile(r'<BLOCK>([a-z_]+)(?:<LEVEL(\d+)>)?\[[^\]]*\]<FORK>(.*)$')
# 损坏标记：<BLOCK>type<LEVELn?>[坐标]垃圾   （后面没有 <FORK>）
RE_BROKEN = re.compile(r'<BLOCK>([a-z_]+)(?:<LEVEL(\d+)>)?\[[^\]]*\](.*)$')
RE_PAGE = re.compile(r'<PAGE>')
RE_ANY_TAG = re.compile(r'<BLOCK>|<FORK>|<LEVEL\d+>|<PAGE>|<MERGE>')

# 裸「类型名+坐标」标记（2026-10-28 实修第二处）：
#   形态如  image_caption [749, 701, 817, 718]例3答图
#           table[140, 572, 885, 884]<table>...
#   特点：类型名与 '[' 之间**可能有空格**，且前面未必有 <BLOCK>
#   （<BLOCK> 可能已被剥掉）。不剥会污染检索。
_TYPES = (r'(?:header|title|subtitle|text|other|image|image_caption|page_number|'
          r'table|figure|caption|formula|equation|math|inline|block|paragraph|'
          r'list|list_item|footnote|chart|diagram|toc|code|reference)')
# 类型名与 '[' 之间还可能夹标点（2026-10-29 P0 验收发现的**第 5 种形态**）：
#   实测  image_caption,[600, 385, 628, 400](C)   ← 逗号
#         text.[103, 598, 291, 614]C.            ← 句点
#         text：[297, 683, 413, 714]D.           ← 中文冒号
_PUNCT = r'[,.:：;、]?'
RE_BARE_COORD = re.compile(r'\b' + _TYPES + _PUNCT + r'\s*\[[0-9,\s]*\]')
# 未闭合坐标（模型输出畸形，坐标被中文截断，无右括号）：
#   实测  text[718, 610, 圆锥摆模型…   /   other[141, 806, 851, many 2. 轻杆…
#   负向预查保证只吃"后面没有 ]"的，避免误伤正文里正常的 [1, 2] 区间
RE_BARE_COORD_OPEN = re.compile(
    r'\b' + _TYPES + _PUNCT + r'\s*\[[0-9,\s]*(?![0-9,\s]*\])')

HAN = re.compile(r'[\u4e00-\u9fff]')


def _finish(s):
    """统一收尾：剥残留标签 + 裸「类型名+坐标」。
    表格标记 <table>/<tr>/<td> 刻意保留——它标志单元格边界，有结构价值。"""
    s = RE_ANY_TAG.sub('', s)
    s = RE_BARE_COORD.sub('', s)
    return RE_BARE_COORD_OPEN.sub('', s)


def clean_text(raw, keep_headings=False, mark_images=False, stats=None):
    """清洗一段（整页 / 整文件）文本。stats 为可选计数器。"""
    out = []
    for line in raw.split('\n'):
        # 页标记：先无条件剥掉。
        # 坑（2026-10-28 实修）：行首常是 "<PAGE><BLOCK>header[...]<FORK>正文"，
        # 若不先剥掉 <PAGE>，下面的 match() 会因行首不是 <BLOCK> 而失败，
        # 冒泡到"只剥标签"分支，把 "header[坐标]" 留在正文里污染检索。
        if '<PAGE>' in line:
            line = RE_PAGE.sub('', line)
            if stats is not None:
                stats['page_tags'] += 1

        m = RE_FULL.match(line)
        if m:
            btype, level, body = m.group(1), m.group(2), m.group(3)
            if stats is not None:
                stats['full_kept'] += 1
            if keep_headings and btype == 'title' and level:
                body = '#' * min(int(level), 6) + ' ' + body
            if btype == 'image' and mark_images:
                body = '[图] ' + body
            # 行内可能还连排着第二个、第三个 BLOCK（模型常把多块挤在一行），
            # RE_FULL 只吃行首第一个，故对 body 再剥一次（2026-10-28 实修第三处）
            out.append(_finish(body))
            continue

        m = RE_BROKEN.match(line)
        if m:
            body = m.group(3) or ''
            # 碎片：仅在含中文且有一定长度时才保留，否则丢弃
            if HAN.search(body) and len(body.strip()) >= 4:
                if stats is not None:
                    stats['broken_kept'] += 1
                out.append(_finish(body))
            else:
                if stats is not None:
                    stats['broken_dropped'] += 1
            continue

        # 普通行：剥掉任何残留标记 + 裸「类型名+坐标」
        # （表格标记 <table>/<tr>/<td> 刻意保留：它标志单元格边界，有结构价值）
        before = line
        line = _finish(line)
        if line != before and stats is not None:
            stats['stray_tags'] += 1
        out.append(line)

    return '\n'.join(out)


def collapse_blank(text, max_blank=2):
    """把连续空行压到最多 max_blank 行，并去掉行尾空白。"""
    lines = [l.rstrip() for l in text.split('\n')]
    out = []
    blank = 0
    for l in lines:
        if l.strip() == '':
            blank += 1
            if blank > max_blank:
                continue
        else:
            blank = 0
        out.append(l)
    return '\n'.join(out).strip() + '\n'


def load_input(path):
    """输入可以是 .txt 全文，也可以是 single_results_full.json（按页拼）。"""
    if path.lower().endswith('.json'):
        d = json.load(open(path, encoding='utf-8'))
        keys = sorted(int(k) for k in d.keys() if k.isdigit())
        chunks = []
        for k in keys:
            v = d[str(k)]
            t = v.get('text', '') if isinstance(v, dict) else str(v)
            chunks.append('===== 第 %d 页 =====\n' % k + t)
        return '\n\n'.join(chunks)
    return open(path, encoding='utf-8', errors='replace').read()


def main():
    ap = argparse.ArgumentParser(description='清洗 Unlimited OCR 2 的结构标记')
    ap.add_argument('--input', required=True, help='full_text.txt 或 single_results_full.json')
    ap.add_argument('--output', required=True, help='输出干净文件')
    ap.add_argument('--keep-headings', action='store_true',
                    help='title 类标记按 LEVEL 转 markdown 标题')
    ap.add_argument('--mark-images', action='store_true',
                    help='image 位置标 [图]')
    args = ap.parse_args()

    raw = load_input(args.input)
    stats = {'full_kept': 0, 'broken_kept': 0, 'broken_dropped': 0,
             'page_tags': 0, 'stray_tags': 0}

    cleaned = clean_text(raw, args.keep_headings, args.mark_images, stats)
    cleaned = collapse_blank(cleaned)

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(cleaned)

    left = len(RE_ANY_TAG.findall(cleaned))
    print('输入字符数：%d' % len(raw))
    print('输出字符数：%d' % len(cleaned))
    print('完整标记保留：%d 行（真实正文，含章节标题）' % stats['full_kept'])
    print('碎片剔除：%d 行 / 碎片保留（含中文且够长）：%d 行'
          % (stats['broken_dropped'], stats['broken_kept']))
    print('页标记清理：%d  游离标记剥离：%d' % (stats['page_tags'], stats['stray_tags']))
    print('残留标记数：%d' % left)
    print('输出：%s' % os.path.abspath(args.output))
    return 0 if left == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
