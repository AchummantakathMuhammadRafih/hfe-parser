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
- Python 3.10 to 3.13
- Virtual environment (recommended)

### 2. Installation

1. Activate your virtual environment (if not already activated):
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

*(Optional for NVIDIA GPU acceleration)*:
Install the PyTorch GPU build compatible with your CUDA version from [pytorch.org](https://pytorch.org/).

---

## Running the Application

Launch the Streamlit app:
```powershell
streamlit run app.py
```

Once started, open your browser and navigate to:
```
http://localhost:8501
```

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
