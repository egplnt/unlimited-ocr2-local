# -*- coding: utf-8 -*-
"""
uocr_textlayer_pdf.py —— 用 Unlimited OCR 2 的逐页文本给拍照页重建可检索 PDF

与既有 ocr_to_searchable_pdf.py 的区别
--------------------------------------
  既有脚本：图片 → Tesseract OCR → 带文字层 PDF（OCR 与合成一体）
  本脚本  ：图片 + 已有高质量逐页文本（文心 OCR 2 输出）→ 带文字层 PDF（不再跑 OCR）

  适用场景：已经用 Unlimited OCR 2 跑完全书、拿到 single_results_full.json，
            要把这些文本挂回原图，产出一个「看得见原图 + 搜得到文字」的 PDF。

实盘依据（2026-10-28《质点运动的秘密》拍照版 229 页）
--------------------------------------------------
  文心 OCR 2  vs  旧 Tesseract 版全文（同书）：
      汉字  53,777 → 96,346（+79%）
      LaTeX 公式标记  0 → 8,809（从无到有）
  → 公式密集的理科教辅，用旧文字层等于检索不到任何公式，必须重建。

文字层做法
----------
  用 PDF 文本渲染模式 3（Tr 3 = 既不填充也不描边，对视觉不可见，但可被
  提取/检索/复制）。这是 OCRmyPDF 等工具的标准做法。

用法
----
  python uocr_textlayer_pdf.py \
      --json   <推理结果目录>/single_results_full.json \
      --images "<拍照页图片目录>" \
      --output "<输出 PDF 路径>"

参数
----
  --json       single_results_full.json（每页含 img / text 字段）
  --images     拍照页图片所在目录（json 的 img 字段相对此目录）
  --output     输出 PDF
  --font       中文字体 ttf（默认自动探测系统 CJK 字体，可用 --font 覆盖）
  --dpi        页面尺寸换算 dpi（默认 200；仅影响页面物理大小，不影响文字层）
  --max-width  图片嵌入前最大宽度（默认 1600；手机直出图必压，见下）
  --jpeg-quality  嵌入 JPEG 质量（默认 80）
  --max-pages  只做前 N 页（调试用，0=全部）

为什么必须压缩（实盘教训 2026-10-28）
------------------------------------
  《质点运动的秘密》拍照版 229 张为手机直出：4096×3072、均 15.5 MB、共 3.5 GB。
  不压缩直接嵌入 → 5 页就 107 MB，全本约 4.9 GB，产物不可用。
  按 --max-width 1600 / --jpeg-quality 80 压缩后，单页约 0.4 MB，全本约 90 MB。
  压缩在内存中完成（BytesIO），不落临时文件、不删任何文件。
"""
import argparse
import json
import os
import re
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uocr_clean_text import clean_text, RE_ANY_TAG  # noqa: E402

DEFAULT_FONT = None

def find_cjk_font():
    """跨平台探测系统中文字体；找不到返回 None。"""
    import glob
    candidates = [
        r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc',
        r'C:\Windows\Fonts\simsun.ttc',
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    ]
    for f in candidates:
        if os.path.exists(f):
            return f
    for pat in (r'C:\Windows\Fonts\*simhei*', r'C:\Windows\Fonts\*msyh*',
                '/usr/share/fonts/**/wqy*', '/usr/share/fonts/**/NotoSansCJK*'):
        hits = glob.glob(pat, recursive=True)
        if hits:
            return hits[0]
    return None

HAN = re.compile(r'[\u4e00-\u9fff]')


def safe_text(text, cmap):
    """把字体不支持的字符换成空格，避免 reportlab 渲染报错/丢字。"""
    out = []
    for ch in text:
        if ch in ('\n', '\t', ' '):
            out.append(' ')
        elif ord(ch) in cmap:
            out.append(ch)
        else:
            out.append(' ')
    return ''.join(out)


def wrap_by_width(line, max_chars):
    """按估算宽度切行（中文按 1 字宽，ASCII 按 0.55 字宽）。"""
    if not line:
        return ['']
    out, cur, w = [], '', 0.0
    for ch in line:
        cw = 0.55 if ord(ch) < 128 else 1.0
        if w + cw > max_chars:
            out.append(cur)
            cur, w = '', 0.0
        cur += ch
        w += cw
    if cur:
        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser(description='OCR 逐页文本 → 可检索 PDF 文字层')
    ap.add_argument('--json', required=True, help='single_results_full.json')
    ap.add_argument('--images', required=True, help='拍照页图片目录')
    ap.add_argument('--output', required=True, help='输出 PDF')
    ap.add_argument('--font', default=DEFAULT_FONT, help='中文字体 ttf（默认自动探测系统 CJK 字体，可用 --font 覆盖）')
    ap.add_argument('--dpi', type=int, default=200, help='页面尺寸换算 dpi')
    ap.add_argument('--max-width', type=int, default=1600, help='嵌入前最大宽度')
    ap.add_argument('--jpeg-quality', type=int, default=80, help='嵌入 JPEG 质量')
    ap.add_argument('--max-pages', type=int, default=0, help='只做前 N 页（0=全部）')
    args = ap.parse_args()

    import io
    from PIL import Image
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_path = args.font or find_cjk_font()
    if not font_path or not os.path.exists(font_path):
        print('!! 找不到中文字体，请用 --font 指定一个含中文的 ttf/ttc 路径')
        return 2
    pdfmetrics.registerFont(TTFont('CJK', font_path))
    cmap = pdfmetrics.getFont('CJK').face.charToGlyph

    data = json.load(open(args.json, encoding='utf-8'))
    pages = sorted(int(k) for k in data.keys() if k.isdigit())
    if args.max_pages:
        pages = pages[:args.max_pages]

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    c = canvas.Canvas(args.output)
    c.setTitle(os.path.splitext(os.path.basename(args.output))[0])

    n_img_missing = 0
    n_text_empty = 0
    total_han = 0

    for i, pg in enumerate(pages, 1):
        rec = data[str(pg)]
        text = rec.get('text', '') if isinstance(rec, dict) else str(rec)
        img_name = rec.get('img', '') if isinstance(rec, dict) else ''
        img_path = os.path.join(args.images, img_name) if img_name else ''

        # 清洗结构标记（保留真实正文与章节标题）
        text = clean_text(text)
        text = RE_ANY_TAG.sub('', text)

        if not os.path.exists(img_path):
            n_img_missing += 1
            print('  !! 缺图 页%d %s' % (pg, img_name))
            continue

        im = Image.open(img_path).convert('RGB')
        iw0, ih0 = im.size
        # 压缩：手机直出图必须缩尺寸，否则 PDF 体积失控（见文件头实盘教训）
        sc = min(1.0, args.max_width / float(iw0))
        iw, ih = int(iw0 * sc), int(ih0 * sc)
        if sc < 1.0:
            im = im.resize((iw, ih), Image.LANCZOS)
        pw, ph = iw * 72.0 / args.dpi, ih * 72.0 / args.dpi
        c.setPageSize((pw, ph))

        # 1) 画原图（可见）—— 经内存压缩后嵌入，不落临时文件
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=args.jpeg_quality, optimize=True)
        buf.seek(0)
        c.drawImage(ImageReader(buf), 0, 0, width=pw, height=ph)

        # 2) 画不可见文字层（Tr 3）
        if text.strip():
            lines = [l for l in text.split('\n')]
            flat = []
            fs = 2.0
            max_chars = max(20, int(pw / (fs * 0.55)))
            for l in lines:
                flat.extend(wrap_by_width(l.rstrip(), max_chars))
            flat = [l for l in flat if l.strip()]

            if flat:
                lh = fs * 1.25
                # 行数过多则自动缩小字号，保证落在页内
                if len(flat) * lh > ph - 4:
                    fs = max(0.5, (ph - 4) / max(1, len(flat)) / 1.25)
                    lh = fs * 1.25
                # 走 TextObject：Canvas 在 reportlab 5.x 无 setTextRenderMode
                t = c.beginText(2, ph - 2 - lh)
                t.setFont('CJK', fs)
                t.setLeading(lh)
                t.setTextRenderMode(3)          # 关键：不可见但可被提取/检索
                for l in flat:
                    t.textLine(safe_text(l, cmap))
                c.drawText(t)
                total_han += len(HAN.findall(text))
        else:
            n_text_empty += 1

        c.showPage()
        if i % 25 == 0 or i == len(pages):
            print('  合成进度 %d/%d' % (i, len(pages)), flush=True)

    c.save()

    size_mb = os.path.getsize(args.output) / 1048576
    print('输出：%s  (%.2f MB)' % (os.path.abspath(args.output), size_mb))
    print('页数：%d   缺图：%d   空文本页：%d' % (len(pages), n_img_missing, n_text_empty))
    print('文字层汉字总数：%d' % total_han)
    return 0


if __name__ == '__main__':
    sys.exit(main())
