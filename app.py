import os
import shutil
import time
import streamlit as st
from datetime import datetime
import torch

from database import (
    init_db,
    add_task,
    get_all_tasks,
    delete_task,
    get_task
)
from worker import start_worker

# Set Page Config
st.set_page_config(
    page_title="MinerU 4.0.12 PDF Converter",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling for polished dashboard look
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        margin-bottom: 0.3rem;
        color: #1E3A8A;
    }
    .sub-header {
        font-size: 1rem;
        color: #4B5563;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background-color: #F8FAFC;
        border-radius: 8px;
        padding: 12px;
        border: 1px solid #E2E8F0;
    }
    .log-box {
        background-color: #1E293B;
        color: #E2E8F0;
        font-family: 'Courier New', Courier, monospace;
        font-size: 0.85rem;
        padding: 12px;
        border-radius: 6px;
        max-height: 380px;
        overflow-y: auto;
        white-space: pre-wrap;
    }
    .status-badge {
        padding: 4px 10px;
        border-radius: 12px;
        font-weight: 600;
        font-size: 0.8rem;
        display: inline-block;
    }
</style>
""", unsafe_allow_html=True)

# Initialize storage paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "data", "uploads")
OUTPUTS_DIR = os.path.join(BASE_DIR, "data", "outputs")
LOGS_DIR = os.path.join(BASE_DIR, "data", "logs")

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Initialize SQLite database and launch worker
init_db()
start_worker()

# Sidebar: Hardware and MinerU 4.0.12 Configuration
with st.sidebar:
    st.image("https://raw.githubusercontent.com/opendatalab/MinerU/master/docs/images/logo.png", width=160)
    st.markdown("### ⚙️ Engine Parameters")
    
    # Device Configuration
    cuda_available = torch.cuda.is_available()
    device_options = ["CPU", "GPU"]
    default_device_idx = 1 if cuda_available else 0
    
    selected_device = st.selectbox(
        "Compute Device",
        options=device_options,
        index=default_device_idx,
        help="Run on CPU or GPU. GPU uses CUDA acceleration if available."
    )
    if selected_device == "GPU" and not cuda_available:
        st.warning("⚠️ No CUDA GPU detected on system. Will automatically fallback to CPU/DirectML.")

    # MinerU 4.0.12 Tier
    tier_choice = st.selectbox(
        "Parsing Tier (--tier)",
        options=["standard", "flash", "basic", "advanced"],
        index=0,
        help="standard: high-accuracy multimodal; flash: fast native extraction; basic: lightweight ONNX models; advanced: heavy inference."
    )

    # OCR Mode
    ocr_mode = st.selectbox(
        "OCR Mode (--ocr-mode)",
        options=["auto", "txt", "ocr"],
        index=0,
        help="auto: automatically determine; txt: fast native text extraction; ocr: image OCR model."
    )

    # Pages Selection
    page_selection = st.radio(
        "Pages to Parse (--pages)",
        options=["all", "Custom Range"],
        index=0,
        horizontal=True
    )
    custom_pages = "all"
    if page_selection == "Custom Range":
        custom_pages = st.text_input("Enter Page Spec (e.g. 1-5,8 or 1-10)", value="1-5")

    st.markdown("---")
    st.markdown("### 🔄 Queue Settings")
    auto_refresh = st.checkbox("Auto-refresh Queue (3s)", value=True)
    if st.button("Manual Refresh 🔄", use_container_width=True):
        st.rerun()

# Main Header
st.markdown("<div class='main-header'>⚡ MinerU 4.0.12 PDF Converter</div>", unsafe_allow_html=True)
st.markdown("<div class='sub-header'>High-fidelity document parser: PDF to structured Markdown and JSON with asynchronous queueing.</div>", unsafe_allow_html=True)

# Upload Section
with st.expander("📤 Upload New Documents to Queue", expanded=True):
    uploaded_files = st.file_uploader(
        "Select or Drag & Drop PDF files (multiple supported)",
        type=["pdf"],
        accept_multiple_files=True
    )

    col1, col2 = st.columns([4, 1])
    with col2:
        queue_btn = st.button("📥 Add to Queue", type="primary", use_container_width=True, disabled=not uploaded_files)

    if queue_btn and uploaded_files:
        added_count = 0
        for uploaded_file in uploaded_files:
            file_stem = os.path.splitext(uploaded_file.name)[0][:50]
            unique_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            task_subdir = f"{file_stem}_{unique_timestamp}"
            
            # Target file paths
            task_upload_dir = os.path.join(UPLOADS_DIR, task_subdir)
            task_output_dir = os.path.join(OUTPUTS_DIR, task_subdir)
            task_log_path = os.path.join(LOGS_DIR, f"{task_subdir}.log")
            
            os.makedirs(task_upload_dir, exist_ok=True)
            pdf_path = os.path.join(task_upload_dir, uploaded_file.name)
            
            with open(pdf_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            # Add task record to SQLite
            add_task(
                filename=uploaded_file.name,
                pdf_path=pdf_path,
                output_dir=task_output_dir,
                log_path=task_log_path,
                device=selected_device.lower(),
                tier=tier_choice.lower(),
                pages=custom_pages
            )
            added_count += 1

        st.success(f"Successfully queued {added_count} document(s)!")
        time.sleep(0.5)
        st.rerun()

# Queue & Task Dashboard
st.markdown("### 📋 Document Conversion Queue")

tasks = get_all_tasks()

if not tasks:
    st.info("No documents in the queue yet. Upload PDFs above to begin.")
else:
    # Summary KPI metrics
    pending_cnt = sum(1 for t in tasks if t["status"] == "pending")
    processing_cnt = sum(1 for t in tasks if t["status"] == "processing")
    completed_cnt = sum(1 for t in tasks if t["status"] == "completed")
    failed_cnt = sum(1 for t in tasks if t["status"] == "failed")

    mcol1, mcol2, mcol3, mcol4, mcol5 = st.columns(5)
    mcol1.metric("Total Documents", len(tasks))
    mcol2.metric("Pending ⏳", pending_cnt)
    mcol3.metric("Processing ⚙️", processing_cnt)
    mcol4.metric("Completed ✅", completed_cnt)
    mcol5.metric("Failed ❌", failed_cnt)

    st.markdown("---")

    # Render Task Rows
    for task in tasks:
        task_id = task["id"]
        filename = task["filename"]
        status = task["status"]
        progress_msg = task.get("progress_msg", "") or ""
        device = task.get("device", "cpu").upper()
        tier = task.get("tier", "standard")
        log_path = task.get("log_path", "")
        zip_path = task.get("zip_path")
        created_at = task.get("created_at", "")

        # Status badge formatting
        if status == "pending":
            status_html = "<span style='background:#FEF3C7; color:#92400E; padding:4px 8px; border-radius:6px; font-weight:600;'>⏳ Pending</span>"
        elif status == "processing":
            status_html = f"<span style='background:#DBEAFE; color:#1E40AF; padding:4px 8px; border-radius:6px; font-weight:600;'>⚙️ {progress_msg}</span>"
        elif status == "completed":
            status_html = "<span style='background:#D1FAE5; color:#065F46; padding:4px 8px; border-radius:6px; font-weight:600;'>✅ Completed</span>"
        else:
            status_html = f"<span style='background:#FEE2E2; color:#991B1B; padding:4px 8px; border-radius:6px; font-weight:600;'>❌ Failed</span>"

        with st.container():
            rcol1, rcol2, rcol3, rcol4, rcol5 = st.columns([3, 1.2, 2.5, 1.5, 1.8])
            
            with rcol1:
                st.markdown(f"**#{task_id}: {filename}**")
                st.caption(f"Added: {created_at} | Pages: {task.get('pages', 'all')}")
            
            with rcol2:
                st.markdown(f"`{device}` | `{tier}`")

            with rcol3:
                st.markdown(status_html, unsafe_allow_html=True)
                if task.get("error_message"):
                    st.caption(f":red[Error: {task['error_message']}]")

            with rcol4:
                # Download Zip Button if completed
                if status == "completed" and zip_path and os.path.exists(zip_path):
                    with open(zip_path, "rb") as zf:
                        st.download_button(
                            label="📥 Download ZIP",
                            data=zf.read(),
                            file_name=os.path.basename(zip_path),
                            mime="application/zip",
                            key=f"dl_{task_id}",
                            use_container_width=True
                        )
                elif status == "failed":
                    if st.button("🔁 Retry", key=f"retry_{task_id}", use_container_width=True):
                        from database import update_task_status
                        update_task_status(task_id, "pending", progress_msg="Queued for retry", error_message="")
                        st.rerun()
                elif status == "processing":
                    st.progress(50, text="Parsing...")
                elif status == "pending":
                    st.caption("Waiting in queue...")

            with rcol5:
                act_col1, act_col2 = st.columns(2)
                with act_col1:
                    # Toggle Live Log Viewer in modal/expander
                    show_log = st.checkbox("📜 Log", key=f"log_chk_{task_id}")
                with act_col2:
                    if st.button("🗑️", key=f"del_{task_id}", help="Delete task and clean up files"):
                        deleted = delete_task(task_id)
                        if deleted:
                            # Clean up physical files
                            if os.path.exists(deleted.get("pdf_path", "")):
                                shutil.rmtree(os.path.dirname(deleted["pdf_path"]), ignore_errors=True)
                            if os.path.exists(deleted.get("output_dir", "")):
                                shutil.rmtree(deleted["output_dir"], ignore_errors=True)
                            if os.path.exists(deleted.get("log_path", "")):
                                try:
                                    os.remove(deleted["log_path"])
                                except Exception:
                                    pass
                        st.rerun()

            # Display real-time execution log file content if toggled
            if show_log:
                if os.path.exists(log_path):
                    try:
                        with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                            log_content = lf.read()
                        st.markdown(f"<div class='log-box'>{log_content[-6000:] if len(log_content) > 6000 else log_content}</div>", unsafe_allow_html=True)
                    except Exception as err:
                        st.error(f"Could not read log file: {err}")
                else:
                    st.caption("Log file will be created when processing starts.")

            st.divider()

# Auto-refresh loop when pending or processing jobs exist
has_active_jobs = any(t["status"] in ("pending", "processing") for t in tasks)
if auto_refresh and has_active_jobs:
    time.sleep(3)
    st.rerun()
