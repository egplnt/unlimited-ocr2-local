# -*- coding: utf-8 -*-
"""
uocr_infer.py —— 文心 Unlimited OCR 2 全量推理（路线 C 第 1 步，泛化版）

输入：拍照页图片目录
输出：single_results_full.json（逐页文本 + 退化度）→ 供
      uocr_clean_text.py（清洗）→ uocr_textlayer_pdf.py（重建可检索 PDF）

与最初那版 uocr_full.py 的区别
------------------------------
  原版把图目录 / 方向台账 / 页码映射 / 模型路径全部硬编码为《质点运动的秘密》专用，
  换一本书就跑不通。本版全部参数化：
    - 图目录、模型、输出目录均为命令行参数
    - 页码映射文件缺失时，回退为「按文件名排序」
    - 方向台账缺失时，回退为「可选 OSD 实时检测」或「不旋转」

运行环境（本机实测）
--------------------
  Python: 任意含 torch / transformers 的 venv（N 卡 CUDA 版或 A 卡 ROCm 版均可）
  GPU   : RX 9060 XT 16GB，ROCm 7.2.1，torch 2.9.1+rocm7.2.1，transformers 4.52.1
  模型  : 自行从 ModelScope / HuggingFace 拉取的 Unlimited-OCR-2（3B / bf16，约 6.3 GB）
  配置  : config.json 设 dash_use_triton=false
  ⚠️ 必须先装 rocm_dist_shim（绕开 DTensor/distributed 分支），
     且必须在 import transformers **之前** —— 与本脚本顶部顺序保持一致。
  速度  : 46.6 s/页（229 页 ≈ 178 分钟）

用法
----
  python uocr_infer.py \
      --images "<图目录>" --out-dir "<结果目录>"

  # 续跑（崩溃后直接重跑同一条命令，已完成的页会跳过）
  # 重跑指定页（如退化页）
  ... --redo 91
  # 开启 OSD 方向校正（拍照件歪斜时；需本机 tesseract）
  ... --osd

产物
----
  <out-dir>/single_results_full.json   逐页 {text, sec, rot, img, degeneracy, error}
  <out-dir>/full_text.txt              拼接全文（未清洗，清洗交给下一步）
"""
import argparse
import csv
import json
import os
import re
import sys
import time

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ---- 关键：必须在 import torch / transformers 之前装 shim ----
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import rocm_dist_shim
    rocm_dist_shim.install()
except Exception as _e:
    print('shim skip: %s' % _e)

from PIL import Image  # noqa: E402

IMG_EXT = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp'}
DEFAULT_MODEL = None   # 必须显式通过 --model 指定模型目录
TESSERACT = None       # 可选：OSD 方向校正用的 tesseract 路径，缺失则跳过 OSD

# 防退化护栏（2026-10-28 实测定，缺了必崩，勿改）
GUARD = dict(no_repeat_ngram_size=8, ngram_window=100, max_new_tokens=8000)
DEGEN_THRESHOLD = 0.5


def degeneracy(text, n=5):
    """最常见 n-gram 字符占比 —— 越高越像重复灌水。"""
    s = re.sub(r'<[^>]+>', '', text or '')
    s = re.sub(r'\s+', '', s)
    if len(s) < n * 2:
        return 0.0
    from collections import Counter
    c = Counter(s[i:i + n] for i in range(len(s) - n + 1))
    return c.most_common(1)[0][1] / float(len(s) - n + 1)


def list_images(d):
    out = []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.path.splitext(name)[1].lower() in IMG_EXT:
            out.append(p)
    return out


def load_ledger(tsv):
    """方向台账（可选）：idx → rot（tesseract OSD 的顺时针角）。"""
    if not tsv or not os.path.exists(tsv):
        return {}
    with open(tsv, encoding='utf-8') as f:
        return {int(r['idx']): r for r in csv.DictReader(f, delimiter='\t')}


def load_map(mp):
    """页码映射（可选）：idx → file。缺失时回退文件名排序。"""
    if not mp or not os.path.exists(mp):
        return {}
    d = json.load(open(mp, encoding='utf-8'))
    return {r['idx']: r['file'] for r in d.get('rows', [])}


def osd_angle(img, tmpdir, tesseract):
    """实时 OSD 检测（台账缺失时的回退）。返回 tesseract 顺时针角，失败返回 -1。"""
    import subprocess
    if not tesseract or not os.path.exists(tesseract):
        return -1
    small = os.path.join(tmpdir, '_osd_small.png')
    base = os.path.join(tmpdir, '_osd')
    try:
        im = img.copy()
        im.thumbnail((1024, 1024))
        im.convert('L').save(small)
        subprocess.run([tesseract, small, base, '-l', 'osd', '--psm', '0'],
                       capture_output=True, timeout=120)
        osdfile = base + '.osd'
        if not os.path.exists(osdfile):
            return -1
        m = re.search(r'Rotate:\s*(\d+)',
                      open(osdfile, encoding='utf-8', errors='replace').read())
        return int(m.group(1)) if m else -1
    except Exception:
        return -1


def pil_deg_for(idx, ledger, im, tmpdir, use_osd, tesseract):
    """返回 (PIL 逆时针角, OSD 角, 说明)。"""
    r = ledger.get(idx, {})
    try:
        trot = int(r.get('rot', -1))
    except Exception:
        trot = -1
    if trot == -1 and use_osd:
        trot = osd_angle(im, tmpdir, tesseract)
    if trot == -1:
        return (90 if im.width > im.height else 0), trot, 'OSD失败→宽高比启发'
    return (360 - trot) % 360, trot, ''


def main():
    ap = argparse.ArgumentParser(description='Unlimited OCR 2 全量推理')
    ap.add_argument('--images', required=True, help='拍照页图片目录')
    ap.add_argument('--out-dir', required=True, help='结果输出目录')
    ap.add_argument('--model', required=True, help='模型目录（Unlimited-OCR-2 权重路径）')
    ap.add_argument('--ledger', default='', help='方向检测台账 tsv（可选）')
    ap.add_argument('--map', default='', help='页码映射 json（可选，缺失按文件名排序）')
    ap.add_argument('--osd', action='store_true', help='台账缺失时用实时 OSD 检测方向')
    ap.add_argument('--tesseract', default=TESSERACT, help='tesseract 可执行文件路径（--osd 时用，缺失则跳过 OSD）')
    ap.add_argument('--redo', default='', help='强制重跑这些页（逗号分隔页码）')
    ap.add_argument('--redo-all', action='store_true', help='重跑全部（改前自动备份）')
    args = ap.parse_args()

    root = os.path.abspath(args.images)
    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    full_json = os.path.join(out_dir, 'single_results_full.json')

    done = {}
    if os.path.exists(full_json):
        done = json.load(open(full_json, encoding='utf-8'))
        print('续跑：已存在 %d 页结果' % len(done), flush=True)

    redo = set(int(x) for x in args.redo.split(',') if x.strip())
    if args.redo_all:
        redo |= set(int(k) for k in done)
    if redo:
        import shutil
        bak = full_json.replace('.json', '.bak_%s.json' % time.strftime('%H%M%S'))
        shutil.copy2(full_json, bak)
        print('重跑页：%s（改前备份 %s）' % (sorted(redo), bak), flush=True)
        for p_ in redo:
            done.pop(str(p_), None)

    # ---- 图片 ↔ 页号 ----
    mp_path = args.map or os.path.join(root, '_页码映射*.json')
    import glob as _g
    cands = _g.glob(mp_path)
    idx2f = load_map(cands[0] if cands else '')
    if not idx2f:
        files = [os.path.basename(p) for p in list_images(root)]
        idx2f = {i: f for i, f in enumerate(files)}
        print('未找到页码映射 → 按文件名排序，共 %d 张' % len(idx2f), flush=True)

    led = load_ledger(args.ledger)

    import torch
    from transformers import AutoModel, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    mdl = AutoModel.from_pretrained(args.model, trust_remote_code=True,
                                    use_safetensors=True,
                                    torch_dtype=torch.bfloat16).eval().cuda()

    tmpdir = out_dir
    t_total = 0.0
    n_err = n_degen = 0
    idxs = sorted(idx2f.keys())

    for idx in idxs:
        pg = idx + 1
        if str(pg) in done:
            continue
        src = os.path.join(root, idx2f[idx])
        if not os.path.exists(src):
            print('缺文件 idx=%d %s' % (idx, idx2f[idx]), flush=True)
            done[str(pg)] = {'text': '', 'error': 'missing file', 'img': idx2f[idx]}
            continue
        im = Image.open(src).convert('RGB')
        pdeg, trot, flag = pil_deg_for(idx, led, im, tmpdir, args.osd, args.tesseract)
        if pdeg:
            im = im.rotate(pdeg, expand=True)
        t0 = time.time()
        try:
            text = mdl.infer(tok, image_file=im, save_results=False, **GUARD)
            if not isinstance(text, str):
                text = str(text)
            err = ''
        except Exception as _e:
            text, err = '', str(_e)[:200]
            n_err += 1
        dt = time.time() - t0
        t_total += dt
        deg = degeneracy(text)
        if deg > DEGEN_THRESHOLD:
            n_degen += 1
        done[str(pg)] = {'text': text, 'sec': round(dt, 1), 'pil_deg': pdeg,
                         'rot': trot, 'img': idx2f[idx], 'flag': flag,
                         'error': err, 'degeneracy': round(deg, 3)}
        json.dump(done, open(full_json, 'w', encoding='utf-8'),
                  ensure_ascii=False)
        print('p%-4d idx=%-4d OSD=%-4s PIL=%-4s %6.1fs 累计%d页 %6.1fmin err=%d%s'
              % (pg, idx, trot, pdeg, dt, len(done), t_total / 60.0, n_err,
                 '  ⚠DEGEN' if deg > DEGEN_THRESHOLD else ''), flush=True)

    # ---- 拼接全文（未清洗，清洗交给 uocr_clean_text.py）----
    with open(os.path.join(out_dir, 'full_text.txt'), 'w', encoding='utf-8') as fh:
        for k in sorted(done, key=lambda x: int(x)):
            fh.write('===== 第 %s 页 =====\n' % k)
            fh.write((done[k].get('text') or '') + '\n\n')

    print('全量完成 %d 页，总耗时 %.1f 分钟，均 %.1fs/页，错误页 %d，退化页 %d'
          % (len(done), t_total / 60.0, t_total / max(1, len(done)), n_err, n_degen))
    deg_pages = [k for k, v in done.items()
                 if float(v.get('degeneracy') or 0) > DEGEN_THRESHOLD]
    if deg_pages:
        print('退化页（用 --redo 重跑）：%s' % deg_pages)
    print('结果：%s' % full_json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
