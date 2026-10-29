import os
import sys
import time
import subprocess
import threading
from datetime import datetime
from database import (
    get_next_pending_task,
    update_task_status,
    init_db
)
from packager import create_result_zip

_worker_thread = None
_stop_event = threading.Event()

def run_conversion_task(task: dict):
    task_id = task["id"]
    filename = task["filename"]
    pdf_path = task["pdf_path"]
    output_dir = task["output_dir"]
    log_path = task["log_path"]
    device = task.get("device", "cpu").lower()
    tier = task.get("tier", "standard").lower()
    pages = task.get("pages", "all")
    ocr_mode = task.get("ocr_mode", "auto")

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    update_task_status(task_id, "processing", progress_msg=f"Initializing MinerU ({device.upper()}, tier: {tier})...")

    # Use parse_runner.py to execute local_parse directly with full unhandled stack traces
    base_stem = os.path.splitext(filename)[0]
    zip_dest = os.path.join(output_dir, f"{base_stem}_parsed.zip")
    runner_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parse_runner.py")

    cmd = [
        sys.executable,
        runner_script,
        pdf_path,
        tier,
        ocr_mode,
        pages,
        zip_dest
    ]

    # Environment configuration for CPU vs GPU, full tracebacks, and UTF-8 encoding
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONFAULTHANDLER"] = "1"
    env["MINERU_DEBUG"] = "1"
    env["MAGIC_PDF_DEBUG"] = "1"
    
    if device == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = ""
    else:
        # If user selected GPU, ensure CUDA is enabled
        if "CUDA_VISIBLE_DEVICES" in env and env["CUDA_VISIBLE_DEVICES"] == "":
            del env["CUDA_VISIBLE_DEVICES"]

    timestamp_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
        log_header = (
            f"[{timestamp_start}] Starting MinerU 4.0.12 Parse Task #{task_id}\n"
            f"File: {filename}\n"
            f"Device: {device.upper()} | Tier: {tier} | Pages: {pages} | OCR Mode: {ocr_mode}\n"
            f"Command: {' '.join(cmd)}\n"
            f"{'=' * 60}\n\n"
        )
        log_file.write(log_header)
        log_file.flush()
        # Also print to console/terminal running streamlit
        print(log_header, flush=True)

        try:
            update_task_status(task_id, "processing", progress_msg="Loading models & parsing layout...")

            # Execute mineru-kit process with explicit UTF-8 encoding and error replacement for Windows terminals
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                universal_newlines=True
            )

            last_msg_update = time.time()
            for line in process.stdout:
                # Write to per-task log file
                log_file.write(line)
                log_file.flush()
                # Print to terminal/console so user sees it in their terminal
                print(f"[Task #{task_id}] {line}", end="", flush=True)

                clean_line = line.strip()

                # Update live progress messages based on mineru log signals
                if clean_line and (time.time() - last_msg_update > 2.0):
                    if "loading model" in clean_line.lower() or "downloading" in clean_line.lower():
                        update_task_status(task_id, "processing", progress_msg="Loading model weights...")
                        last_msg_update = time.time()
                    elif "parsing" in clean_line.lower() or "page" in clean_line.lower():
                        display_note = clean_line[:80] + ("..." if len(clean_line) > 80 else "")
                        update_task_status(task_id, "processing", progress_msg=display_note)
                        last_msg_update = time.time()
                    elif "vlm" in clean_line.lower():
                        update_task_status(task_id, "processing", progress_msg="VLM layout inference...")
                        last_msg_update = time.time()

            process.wait()

            if process.returncode == 0:
                success_msg = f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MinerU conversion succeeded.\nVerifying output bundle...\n"
                log_file.write(success_msg)
                log_file.flush()
                print(f"[Task #{task_id}] {success_msg}", flush=True)

                # Also extract markdown to folder for immediate preview if needed
                zip_path = zip_dest if (os.path.exists(zip_dest) and os.path.getsize(zip_dest) > 0) else None
                if not zip_path:
                    zip_path = create_result_zip(output_dir, task_id, filename)

                # Post-processing: Correct heading hierarchy, mask PII, and strip images
                try:
                    from postprocess import postprocess_zip_markdown
                    post_res = postprocess_zip_markdown(zip_path, output_dir)
                    if isinstance(post_res, tuple):
                        if len(post_res) == 3:
                            merges, pii_count, images_removed = post_res
                        elif len(post_res) == 2:
                            merges, pii_count, images_removed = post_res[0], post_res[1], 0
                        else:
                            merges, pii_count, images_removed = post_res[0], 0, 0
                    else:
                        merges, pii_count, images_removed = post_res, 0, 0

                    post_msg = (
                        f"[Post-Process] Heading hierarchy normalized ({merges} heading pairs merged).\n"
                        f"[Post-Process] PII Masking applied ({pii_count} entities masked with stars *).\n"
                        f"[Post-Process] Image stripping applied ({images_removed} image tags removed; only markdown in archive).\n"
                    )
                    log_file.write(post_msg)
                    log_file.flush()
                    print(f"[Task #{task_id}] {post_msg}", flush=True)
                except Exception as p_err:
                    log_file.write(f"[Post-Process Notice] Post-processing note: {p_err}\n")
                    log_file.flush()

                log_file.write(f"Archive ready: {zip_path}\n")
                log_file.flush()
                print(f"[Task #{task_id}] Archive ready: {zip_path}\n", flush=True)

                update_task_status(task_id, "completed", progress_msg="Completed successfully", zip_path=zip_path)
            else:
                err_msg = f"MinerU process exited with code {process.returncode}"
                log_file.write(f"\n[ERROR] {err_msg}\n")
                log_file.flush()
                print(f"[Task #{task_id}] [ERROR] {err_msg}\n", flush=True)
                update_task_status(task_id, "failed", progress_msg="Failed", error_message=err_msg)

        except Exception as e:
            err_msg = f"Exception during execution: {str(e)}"
            log_file.write(f"\n[FATAL ERROR] {err_msg}\n")
            log_file.flush()
            print(f"[Task #{task_id}] [FATAL ERROR] {err_msg}\n", flush=True)
            update_task_status(task_id, "failed", progress_msg="Error encountered", error_message=err_msg)

def worker_loop():
    init_db()
    while not _stop_event.is_set():
        task = get_next_pending_task()
        if task:
            try:
                run_conversion_task(task)
            except Exception as e:
                print(f"[Worker Exception] {e}", flush=True)
                update_task_status(task["id"], "failed", error_message=str(e))
        else:
            time.sleep(2)

def start_worker():
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _stop_event.clear()
        _worker_thread = threading.Thread(target=worker_loop, daemon=True, name="MinerUWorker")
        _worker_thread.start()
