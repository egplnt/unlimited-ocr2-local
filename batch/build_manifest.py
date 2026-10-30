# -*- coding: utf-8 -*-
"""批次准备：从 P1 筛选表按档位挑件，产出可跑清单（供 render_pages.py 用）。

用法：
  python build_manifest.py --bands "A必做,C低收益" --out "run_list.tsv"
"""
import argparse
import csv
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

FILTER = 'filter_table.tsv'


def main():
    ap = argparse.ArgumentParser(description='按档位产出批次清单')
    ap.add_argument('--filter', default=FILTER, help='筛选表路径')
    ap.add_argument('--bands', default='A必做,C低收益', help='档位（逗号分隔）')
    ap.add_argument('--out', required=True, help='输出清单 TSV')
    args = ap.parse_args()

    bands = [b.strip() for b in args.bands.split(',') if b.strip()]
    rows = list(csv.DictReader(open(args.filter, encoding='utf-8'), delimiter='\t'))
    sel = [r for r in rows if r['档位'] in bands]

    out, missing = [], []
    for r in sel:
        p = r['路径']
        if not os.path.exists(p):
            missing.append(p)
            continue
        out.append({
            '路径': p,
            '页数': r['页数'],
            '大小MB': '%.1f' % (os.path.getsize(p) / 1048576),
            '现有汉字': r['汉字每页'],
            '旧状态': r['旧判级'],
        })

    with open(args.out, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['路径', '页数', '大小MB', '现有汉字', '旧状态'],
                           delimiter='\t')
        w.writeheader()
        w.writerows(out)

    pg = sum(int(x['页数'] or 0) for x in out)
    print('档位：%s' % '、'.join(bands))
    print('选中 %d 件 / %d 页（缺文件 %d 件已剔除）' % (len(out), pg, len(missing)))
    print('预估机时：%.1f h（按 P0 实测 69 s/页）' % (pg * 69 / 3600))
    print('清单：%s' % args.out)
    for m in missing[:5]:
        print('  缺：%s' % m[-60:])


if __name__ == '__main__':
    main()
