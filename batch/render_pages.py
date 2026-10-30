# -*- coding: utf-8 -*-
"""批次 · 第 1 段：把待修 PDF 渲染成图片（为第 2 段 GPU 推理备料）。

用本机 python（需安装 pypdfium2）即可，推理环境与渲染库分离。
输出：<work>/<nn>/imgs/pg0001.jpg  +  manifest.json
"""
import csv
import json
import os
import re
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

LIST = 'input_list.tsv'
WORK = 'work'
DPI = 200


def safe_name(p):
    """由原 PDF 路径生成工作目录名与输出文件名。"""
    d = os.path.dirname(p)
    base = os.path.basename(p)
    new = base.replace('.pdf', '_可检索_文心OCR2.pdf')
    if new == base:                       # 万一后缀不同
        new = os.path.splitext(base)[0] + '_文心OCR2.pdf'
    return d, new


def main():
    import argparse
    ap = argparse.ArgumentParser(description='批次渲染：PDF → 图片（第 1 段）')
    ap.add_argument('--list', default=LIST, help='可跑清单 TSV')
    ap.add_argument('--work', default=WORK, help='工作目录（渲染图 + manifest）')
    ap.add_argument('--dpi', type=int, default=DPI)
    a = ap.parse_args()

    import pypdfium2 as pdfium
    from PIL import Image

    rows = list(csv.DictReader(open(a.list, encoding='utf-8'), delimiter='\t'))
    os.makedirs(a.work, exist_ok=True)
    manifest = []
    total_pages = 0

    for i, r in enumerate(rows, 1):
        src = r['路径']
        if not os.path.exists(src):
            print('!! 缺文件跳过：%s' % src)
            continue
        outdir_pdf, newname = safe_name(src)
        wd = os.path.join(a.work, '%02d' % i)
        imdir = os.path.join(wd, 'imgs')
        os.makedirs(imdir, exist_ok=True)

        doc = pdfium.PdfDocument(src)
        n = len(doc)
        scale = a.dpi / 72.0
        for k in range(n):
            pil = doc[k].render(scale=scale).to_pil().convert('RGB')
            # 限宽 2000，避免超大页拖慢推理
            if pil.width > 2000:
                h = int(pil.height * 2000 / pil.width)
                pil = pil.resize((2000, h), Image.LANCZOS)
            pil.save(os.path.join(imdir, 'pg%04d.jpg' % (k + 1)),
                     'JPEG', quality=88, optimize=True)
        total_pages += n

        manifest.append({
            'idx': i, 'src': src, 'imdir': imdir, 'npages': n,
            'json': os.path.join(wd, 'single_results_full.json'),
            'out_pdf': os.path.join(outdir_pdf, newname),
        })
        print('[%2d/%d] %-58s %4d 页' % (i, len(rows),
                                        os.path.basename(src)[:58], n), flush=True)

    mf = os.path.join(a.work, 'manifest.json')
    json.dump(manifest, open(mf, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print()
    print('渲染完成：%d 个 PDF / %d 页' % (len(manifest), total_pages))
    print('manifest：%s' % mf)
    print('预估推理机时：%.1f h' % (total_pages * 46.6 / 3600))


if __name__ == '__main__':
    main()
