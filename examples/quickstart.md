# 快速开始（最小可跑示例）

下面用**你自己的任意一张扫描/拍照页**（或一页 PDF）跑通全链路，不依赖任何外部样例文件。

## 0. 准备

```bash
pip install -r requirements.txt

# 拉取模型权重（MIT，约 6.3 GB），放到任意目录，例如 ./models/Unlimited-OCR-2
#   ModelScope / HuggingFace 搜索 "Unlimited OCR 2"
```

> A 卡 + ROCm + Windows 用户：**先** `pip install` 后无需手动装 shim，
> `uocr_infer.py` 会在加载 torch 前自动 `try/except` 导入 `rocm_dist_shim`，
> 失败则跳过（N 卡 / Linux 用户忽略即可）。
> ⚠️ 安全：权重请仅从官方 ModelScope / HuggingFace 源拉取（推理用 `trust_remote_code=True`）。

## 1. 若输入是 PDF —— 先渲染成图片

```bash
# 准备一个清单 tsv（两列：src 原PDF路径, out_pdf 输出路径），或直接用图片目录跳过本步
python batch/render_pages.py --list your_list.tsv --work work
# 产物：work/<nn>/imgs/pg0001.jpg ... + work/manifest.json
```

## 2. 推理（核心）

```bash
# 图片目录模式（最常用）：
python infer/uocr_infer.py \
    --images work/01/imgs \
    --out-dir work/01 \
    --model ./models/Unlimited-OCR-2

# 续跑（崩溃后直接重跑同一条命令，已完成页自动跳过）
# 重跑某页（如退化页）：... --redo 91
# 开启 OSD 方向校正（拍照歪斜件，需本机 tesseract）：... --osd --tesseract /path/to/tesseract
```

产物：`work/01/single_results_full.json`（逐页文本 + 退化度）+ `full_text.txt`

## 3. 清洗结构标记（可选但推荐）

```bash
python process/uocr_clean_text.py \
    --input work/01/single_results_full.json \
    --output work/01/clean.txt
```

## 4. 重建可检索 PDF（文字层可搜索 + 公式层）

```bash
python process/uocr_textlayer_pdf.py \
    --json work/01/single_results_full.json \
    --images work/01/imgs \
    --output work/01/out.pdf
# 中文字体自动探测系统字体；找不到时用 --font 指定一个含中文的 ttf/ttc
```

## 5. 批量（多件）

```bash
# 渲染整批 → manifest
python batch/render_pages.py --list your_list.tsv --work work
# 逐件推理（自己写个循环，或参考 manifest 结构）
# 批量重建 + 验收
python process/rebuild_pdf.py --manifest work/manifest.json
python batch/verify_results.py --manifest work/manifest.json
```

完成。生成的 `out.pdf` 文字层可被 Ctrl+F 搜索，含 LaTeX 公式。
