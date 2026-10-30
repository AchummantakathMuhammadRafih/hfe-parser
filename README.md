# MinerU 4.0.12 PDF Converter (Streamlit App)

An asynchronous document conversion web application built with **Streamlit**, powered by **MinerU 4.0.12**, and backed by an **SQLite** queue for state management.

Converts complex PDFs into high-quality **Markdown** and structured **JSON**, bundled with extracted images in a downloadable `.zip` file.

---

## Features

- **MinerU 4.0.12 Engine Integration**:
  - Utilizes `mineru-kit parse` under the hood.
  - Configurable parsing tiers: `standard`, `flash`, `basic`, and `advanced`.
  - Configurable OCR mode: `auto`, `txt`, `ocr`.
  - Support for custom page ranges or full document (`--pages all`).
- **CPU & GPU Acceleration**:
  - Switch between **CPU** and **GPU** from the sidebar.
  - Automatically isolates CPU execution via `CUDA_VISIBLE_DEVICES` or binds to detected CUDA GPUs.
- **Asynchronous Processing Queue**:
  - Multi-file PDF upload support.
  - Background daemon worker thread processes documents sequentially without blocking the UI.
  - SQLite database (`tasks.db`) with Write-Ahead Logging (WAL) for safe, concurrent state management.
- **Real-Time Model & Parsing Logs**:
  - Inspect model loading, downloads, layout detection, and OCR progress live via an expandable log viewer.
- **One-Click Downloads & File Management**:
  - Download all results (`.md`, `.json`, figures/images) packaged in a clean `.zip` archive.
  - Per-row delete functionality to remove tasks from SQLite and clean up physical files on disk.

---

## Project Structure

```text
hfe-parser/
├── app.py              # Streamlit dashboard and user interface
├── worker.py           # Background task consumer executing MinerU 4.0.12 CLI
├── database.py         # Thread-safe SQLite database manager (WAL mode)
├── packager.py         # ZIP packager for Markdown, JSON, and image assets
├── requirements.txt    # Python dependencies
├── README.md           # Documentation
└── data/               # Automatically created runtime data directories
    ├── uploads/        # Staged PDF files
    ├── outputs/        # Extracted Markdown, JSON, and assets
    └── logs/           # Real-time execution logs per document
```

---

## Getting Started

### 1. Prerequisites
- Python 3.12
- Virtual environment (recommended)

### 2. Installation (Local / CPU)

1. Activate your virtual environment (if not already activated):
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   python -m spacy download en_core_web_sm
   ```

---

## Remote GPU Setup & Acceleration (NVIDIA CUDA)

To achieve maximum throughput (15–30+ pages/sec) on a remote GPU VM (e.g., RTX 3060/3090/4090, A10G, T4, L4):

### 1. System Libraries (Ubuntu/Debian)
Install essential rendering and media libraries required by PDF layout engines:
```bash
sudo apt update && sudo apt install -y ffmpeg libsm6 libxext6 libgl1
```

### 2. Install PyTorch with CUDA
Install PyTorch compiled for your VM's CUDA version (e.g., CUDA 12.4):
```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

Verify GPU detection:
```bash
python3 -c "import torch; print('CUDA Available:', torch.cuda.is_available(), '| GPU:', torch.cuda.get_device_name(0))"
```

### 3. Install Requirements & NLP Model
```bash
pip install -r requirements.txt
python3 -m spacy download en_core_web_sm
```

### 4. Enable GPU VLM Engine (`lmdeploy` or `vllm`)
> [!IMPORTANT]
> By default, MinerU falls back to `llama-cpp-engine` on the CPU (~1 page/sec) if an accelerated VLM engine is not installed. To unlock full GPU Tensor Core acceleration for layout detection, install **`lmdeploy`** (recommended) or **`vllm`**:
```bash
pip install lmdeploy
```

Verify that MinerU selects the GPU VLM backend:
```bash
python3 -c "from mineru.model.vlm.selector import resolve_vlm_engine; print('Selected VLM Engine:', resolve_vlm_engine())"
```
*Expected output: `Selected VLM Engine: lmdeploy`*

### 5. Tune Page Batch Size & GPU Throughput (Optional)
MinerU processes document pages in chunks/windows (default is 64). On GPUs with 16GB–24GB+ VRAM, increase the page batch window size to maximize GPU saturation and throughput:
```bash
export MINERU_PROCESSING_WINDOW_SIZE=256
```
*(On Windows PowerShell, use: `$env:MINERU_PROCESSING_WINDOW_SIZE="256"`)*

---

## Running the Application

### Local Machine:
```powershell
streamlit run app.py
```

### Remote GPU VM (Accessible via Browser):
```bash
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```
*(Or use SSH tunneling from your local machine: `ssh -N -L 8501:localhost:8501 user@<vm-ip>` and browse to `http://localhost:8501`)*.

---

## Configuration & Usage

1. **Select Device & Parameters (Sidebar)**:
   - **Device**: Select **CPU** or **GPU**.
   - **Tier**:
     - `standard`: Balanced multimodal layout analysis (default).
     - `flash`: Fast native text extraction & lightweight preview.
     - `basic`: Small ONNX/Torch models for OCR and tables without full VLM.
     - `advanced`: High inference depth for demanding layouts.
   - **Pages**: Choose `all` to parse the full document or `Custom Range` (e.g., `1-5,8`).
2. **Upload Files**:
   - Drag and drop one or multiple PDFs and click **Add to Queue**.
3. **Monitor Progress**:
   - Check status badges: `⏳ Pending`, `⚙️ Processing`, `✅ Completed`, `❌ Failed`.
   - Click **📜 Log** on any row to observe real-time MinerU output (model downloading, loading, inference steps).
4. **Download & Clean**:
   - When a task reaches `✅ Completed`, click **📥 Download ZIP** to get the generated Markdown and JSON.
   - Click **🗑️** to delete the task and clean up associated files.
