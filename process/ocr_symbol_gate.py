# -*- coding: utf-8 -*-
"""
ocr_symbol_gate.py —— OCR 文本层「符号保真闸门」（甲线 · 2026-10-14 立）
配套 skill：scan-to-searchable-pdf §入库衔接第 2 条 / §关键坑 3

用途：入库前对数学 / 物理 / 化学册强制验一层「文本层在用途上站不站得住」，
      补齐旧 census 只验汉字词、从不验符号的口径盲区。

判级（按用途，不按"有没有文本层"）：
  L0 无效   汉字密度 < 20/页 或 学科词命中种类 < 2   → 不得入库为"可检索"
  L1 定位档 汉字足 + 词种 ≥2 + 符号 = 0              → OCR 加层常态，公式须回看原图
  L2 全功能 符号保真 > 0                              → 原生文本层，公式可检索

用法：
  python ocr_symbol_gate.py <目录|文件> [-o out.tsv] [--no-recursive]
"""
import os, re, sys, csv, argparse
import pymupdf

MATH_SYM = "√∫∑∏∈∉⊂⊆∪∩≤≥≠±×÷∞αβγδεθλμπσφωΔΩΦ∠⊥∥≌∽⊙∇∂"

TERMS = {
    "数学": ["函数", "方程", "直线", "平面", "向量", "三角形", "数列", "不等式",
             "导数", "坐标", "集合", "例题", "解析", "证明", "答案"],
    "物理": ["运动", "速度", "加速度", "受力", "能量", "电场", "电路",
             "实验", "质量", "时间", "解析", "答案"],
    "化学": ["反应", "溶液", "物质", "元素", "氧化", "还原", "离子", "平衡", "答案"],
    "生物": ["细胞", "基因", "遗传", "组织", "激素", "受体", "分裂", "光合", "答案"],
    "地理": ["地形", "气候", "区域", "人口", "农业", "工业", "城市", "答案"],
}
TERMS["通用"] = ["定义", "例题", "解析", "答案", "知识点", "方法"]

# 常见词（跨科通用）——乱码 OCR 的照妖镜：随机字流里几乎不成词
# 实测：正常件命中 20–23 个，乱码件 0–1 个，中间 3–13 为灰区（须人工抽读）
COMMON = ["解析", "答案", "因为", "所以", "可以", "这个", "我们", "题目", "如图", "故选",
          "由题意", "证明", "训练", "内容", "方法", "下列", "已知", "关于", "注意", "说明",
          "本题考查", "计算", "判断", "正确", "错误", "选项", "过程", "结果", "学生", "知识"]


def subject_of(path):
    # 文件名优先（2026-10-14 修：只看路径会把「生物体系重构」误判成数学——它落在数学目录，
    # 导致用数学词表去测生物书，词种=0 被误判 L0）
    name = os.path.basename(path)
    for s in ("数学", "物理", "化学", "地理", "生物", "语文", "英语", "历史", "政治"):
        if s in name:
            return s
    p = path.replace("\\", "/")
    for s in ("数学", "物理", "化学", "地理", "生物", "语文", "英语", "历史", "政治"):
        if "/" + s + "/" in p:
            return s
    return "其他"


def grade_one(path):
    subj = subject_of(path)
    words = TERMS.get(subj, []) + TERMS["通用"]
    d = pymupdf.open(path)
    try:
        n = d.page_count
        start = max(0, int(n * 0.15))
        rng = list(range(start, min(start + 20, n)))
        txt = "".join(d[i].get_text() for i in rng)
    finally:
        d.close()
    npp = max(1, len(rng))
    han = len(re.findall(r"[\u4e00-\u9fff]", txt))
    sym = sum(txt.count(c) for c in MATH_SYM)
    han_pp = han / npp
    kinds = sum(1 for w in words if txt.count(w) > 0)
    hits = sum(txt.count(w) for w in words)
    degen = sum(txt.count(c) for c in ("一", "十", "王")) / npp
    # 中文可读性判据（2026-10-14 加）：乱码 OCR 能堆满汉字却不成词，
    # 实测正常件「的」密度 28–58/千字，乱码件 0.2–1.0/千字，差一个数量级
    de_1k = (txt.count("的") / han * 1000) if han else 0.0
    cw = sum(1 for w in COMMON if w in txt)

    if han_pp < 20:
        lv, note = "L0", "文本层空/极稀（OCR 未出字或只出页码）"
    elif cw <= 2 and de_1k < 12:
        # 两个条件须同时成立：公式密集的短件（如 2 页《数学公式秘籍》）文字可读、
        # 常见词却只有 2 个，只按常见词判会把它们误杀（2026-10-14 实读更正）
        lv, note = "L0", "文本层乱码（堆满汉字却不成词，常见词 %d/30，「的」%.1f/千字）" % (cw, de_1k)
    elif cw < 10 and de_1k < 12:
        lv, note = "L0?", "疑似乱码·须人工抽读（常见词 %d/30，「的」%.1f/千字）" % (cw, de_1k)
    elif sym > 0:
        lv, note = "L2", "原生文本层，公式可检索"
    else:
        lv, note = "L1", "汉字可检索，公式须回看原图（禁止符号/公式检索）"
    return [path, subj, n, len(rng), round(han_pp, 1), sym, kinds, hits,
            round(degen, 1), lv, note, round(de_1k, 1), cw]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--no-recursive", action="store_true")
    a = ap.parse_args()

    files = []
    if os.path.isfile(a.target):
        files = [a.target]
    else:
        if a.no_recursive:
            for f in os.listdir(a.target):
                if f.lower().endswith(".pdf"):
                    files.append(os.path.join(a.target, f))
        else:
            for root, _, fs in os.walk(a.target):
                for f in fs:
                    if f.lower().endswith(".pdf"):
                        files.append(os.path.join(root, f))
    files.sort()

    fh = open(a.out, "w", newline="", encoding="utf-8") if a.out else sys.stdout
    w = csv.writer(fh, delimiter="\t")
    w.writerow(["path", "subj", "pages", "samp", "han_per_page", "symbol",
                "term_kinds", "term_hits", "degen_per_page", "LEVEL", "note",
                "de_per_1k", "common_words"])
    cnt = {"L0": 0, "L0?": 0, "L1": 0, "L2": 0}
    for p in files:
        try:
            row = grade_one(p)
        except Exception as e:
            w.writerow([p, "", "", "", "", "", "", "", "", "ERR", str(e)[:60]])
            continue
        cnt[row[9]] += 1
        w.writerow(row)
    if a.out:
        fh.close()
        print("WROTE %s | L0=%d L1=%d L2=%d" % (a.out, cnt["L0"], cnt["L1"], cnt["L2"]))
    else:
        sys.stderr.write("L0=%d L1=%d L2=%d\n" % (cnt["L0"], cnt["L1"], cnt["L2"]))


if __name__ == "__main__":
    main()
