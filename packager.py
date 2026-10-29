import os
import zipfile
from typing import Optional

def create_result_zip(output_dir: str, task_id: int, base_filename: str) -> Optional[str]:
    """
    Finds markdown (.md), json (.json), and any extracted images in output_dir,
    and packages them into a clean .zip file.
    """
    if not os.path.exists(output_dir):
        return None

    zip_filename = f"{os.path.splitext(base_filename)[0]}_parsed.zip"
    zip_path = os.path.join(output_dir, zip_filename)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(output_dir):
            for file in files:
                file_path = os.path.join(root, file)
                # Don't include the zip file itself if re-running
                if os.path.abspath(file_path) == os.path.abspath(zip_path):
                    continue
                # Calculate relative path inside zip
                rel_path = os.path.relpath(file_path, output_dir)
                zipf.write(file_path, arcname=rel_path)

    if os.path.exists(zip_path) and os.path.getsize(zip_path) > 0:
        return zip_path
    return None
