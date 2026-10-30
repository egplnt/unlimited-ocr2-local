# -*- coding: utf-8 -*-
"""P0 批次 · 第 3 段：逐件重建可检索 PDF（复用 skill 的 uocr_textlayer_pdf.py）。

输入：work/manifest.json（第 1 段渲染产出）+ 各工作目录的
      single_results_full.json（第 2 段推理产出）
输出：原目录下 原名_可检索_文心OCR2.pdf（旧件不删不覆盖）
"""
import json
import os
import subprocess
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

MF = 'work/manifest.json'
PY = sys.executable
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uocr_textlayer_pdf.py')


def main():
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument('--manifest', default=MF)
    _a = _ap.parse_args()
    globals()['MF'] = _a.manifest
    mf = json.load(open(MF, encoding='utf-8'))
    ok, fail = [], []
    for item in mf:
        jp = item['json']
        if not os.path.exists(jp):
            print('!! 缺推理结果，跳过：%s' % item['src'][-50:])
            fail.append((item, 'no_json'))
            continue
        cmd = [PY, SCRIPT, '--json', jp,
               '--images', item['imdir'], '--output', item['out_pdf']]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        if r.returncode == 0 and os.path.exists(item['out_pdf']):
            size = os.path.getsize(item['out_pdf']) / 1048576
            print('✅ %-52s %7.1f MB' % (os.path.basename(item['out_pdf'])[:52], size))
            ok.append(item)
        else:
            print('❌ %s\n   %s' % (os.path.basename(item['out_pdf'])[:52],
                                    (r.stdout or '')[-200:] or (r.stderr or '')[-200:]))
            fail.append((item, 'rebuild_fail'))

    print()
    print('重建成功 %d / %d，失败 %d' % (len(ok), len(mf), len(fail)))
    for item, why in fail:
        print('   [%s] %s' % (why, item['src'][-70:]))
    return 0 if not fail else 1


if __name__ == '__main__':
    sys.exit(main())
