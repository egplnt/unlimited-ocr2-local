# Unlimited-OCR-2 本地部署管线（unlimited-ocr2-local）

把扫描 / 拍照页变成**可检索 PDF**（文字层可搜索、含 LaTeX 公式层）的本地管线。
引擎是百度开源的 **Unlimited OCR 2**（3B 多模态 OCR，MIT 协议），本仓库在其之上补齐了「渲染 → 推理 → 清洗 → 重建 → 验收」一套可复用的脚本。

> 这不是传统 OCR（先检测再识别），也不是 llama.cpp 那类纯文本推理引擎：
> 它用**视觉语言模型（VLM）**把图片切成视觉 token，直接"生成"出带坐标、带层级的文本。
> 想了解 runtime 原理（模型结构 / 为什么选 VLM / ROCm 坑 / 护栏链 / 迁移别的卡），
> 见下方「延伸阅读」。

## 硬件门槛

- 实测 **16 GB 显存为舒适区**（模型 bf16 约 6.3 GB + 推理峰值）。
- **N 卡（CUDA）/ A 卡（ROCm）均可**；A 卡 + ROCm + Windows 需额外 shim（见下）。

## 目录结构

```
unlimited-ocr2-local/            (MIT)
├── README.md  LICENSE  requirements.txt
├── infer/
│   ├── uocr_infer.py            # 全量推理（VLM 生成文本 + 退化度）
│   └── rocm_dist_shim.py       # ⚠️ 可选：仅 A 卡 + ROCm + Windows 需要
├── process/
│   ├── uocr_clean_text.py       # 清洗 5 种结构标记
│   ├── uocr_textlayer_pdf.py    # 重建可检索 PDF（reportlab 文字层）
│   ├── ocr_symbol_gate.py       # 符号闸门（判级 L0/L1/L2 辅助）
│   └── rebuild_pdf.py           # 批量重建（读 manifest）
├── batch/
│   ├── render_pages.py          # PDF → 图片
│   ├── build_manifest.py        # 从筛选表按档位挑件
│   └── verify_results.py        # 新旧逐件硬对比验收
└── examples/
    └── quickstart.md            # 最小可跑示例
```

## 安装栈

```bash
pip install -r requirements.txt
# 拉取模型权重（MIT，约 6.3 GB）到任意目录，例如 ./models/Unlimited-OCR-2
#   ModelScope / HuggingFace 搜索 "Unlimited OCR 2"
```

**关键版本对齐**（实测兼容）：`torch>=2.9.1` + `transformers>=4.52.1`。
- N 卡：装 CUDA 版 torch（`+cu12x`）。
- A 卡 ROCm：装 ROCm 版 torch（`+rocm7.2.1`）；**Linux 大概率无需 shim**，Windows 才需要。

## 五个坑（部署时最常卡住的地方）

1. **A 卡 + ROCm + Windows 必须先装 shim**：ROCm Windows 构建未编译 `torch.distributed`，
   `uocr_infer.py` 在 `import torch` 前自动 `try/except` 导入 `rocm_dist_shim` 绕过；失败自动跳过（N 卡忽略）。
2. **关掉 Triton**：模型目录 `config.json` 设 `dash_use_triton=false`，否则易崩。
3. **防退化护栏参数勿改**：`no_repeat_ngram_size=8 / ngram_window=100 / max_new_tokens=8000`
   是实测定死的，改了会出现重复灌水（退化）。
4. **OSD 方向校正可选**：拍照歪斜页用 `--osd`，需本机另装 Tesseract-OCR（脚本 `--tesseract` 指路径，缺失则跳过）。
5. **权重只从官方源拉**：推理用 `trust_remote_code=True` 加载模型自带代码，请从 ModelScope / HuggingFace 官方源获取。

## 快速开始

```bash
# 1. 渲染（PDF 输入时；纯图片目录可跳过）
python batch/render_pages.py --list your_list.tsv --work work

# 2. 推理（核心）
python infer/uocr_infer.py --images work/01/imgs --out-dir work/01 --model ./models/Unlimited-OCR-2

# 3. 清洗标记
python process/uocr_clean_text.py --input work/01/single_results_full.json --output work/01/clean.txt

# 4. 重建可检索 PDF
python process/uocr_textlayer_pdf.py --json work/01/single_results_full.json --images work/01/imgs --output work/01/out.pdf
```

完整四步 + 批量 + 续跑示例见 [`examples/quickstart.md`](examples/quickstart.md)。

## 续跑 / 退化重跑

- **续跑**：推理崩溃后直接重跑同一条 `uocr_infer.py` 命令，已完成页自动跳过。
- **重跑指定页**：`--redo 91`（如某页退化）；`--redo-all` 重跑全部（自动备份旧结果）。

## 安全说明

推理使用 `trust_remote_code=True` 加载模型自带代码。请**仅从官方 ModelScope / HuggingFace 源**
拉取权重，不要使用来路不明的权重目录。模型权重遵循百度 Unlimited OCR 2 的 MIT 协议，
本仓库不含权重文件。

## 延伸阅读

- **Runtime 说明（五层讲透）**：[docs/ocr2_runtime.html](docs/ocr2_runtime.html) —— 模型结构 / 为什么选 VLM 而非传统 OCR / ROCm 坑 / 护栏链设计 / 迁移别的卡，图文版。
- 知乎长文《RX9060XT 跑通文心 OCR2》：记录本管线在消费级 A 卡 + Windows + ROCm 上的
  完整踩坑与配置细节（runtime 原理、五个坑的展开版）。

## License

代码：MIT（见 `LICENSE`）。模型权重：百度 Unlimited OCR 2（MIT）。
