import os
import sys
import traceback
from pathlib import Path

def main():
    if len(sys.argv) < 6:
        print("Usage: parse_runner.py <pdf_path> <tier> <ocr_mode> <pages> <zip_dest>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1]).resolve()
    tier = sys.argv[2]
    ocr_mode = sys.argv[3]
    pages = sys.argv[4] if sys.argv[4].strip() != "all" else ""
    zip_dest = Path(sys.argv[5]).resolve()

    try:
        # Hotfix: MinerU 4.0.12 has a bug on Linux/Python 3.12 where native._owned_span_texts
        # calls owner.prepare_span_texts(..., private_ranges) and fails with:
        # TypeError: argument 'private_ranges': 'int' object is not an instance of 'tuple'.
        # By patching _owned_span_texts to catch TypeError and return None, MinerU automatically
        # executes its standard Python text span assignment fallback without crashing.
        try:
            from mineru.backend.analysis.pdf.text import native as mineru_native
            orig_owned_span_texts = mineru_native._owned_span_texts
            def safe_owned_span_texts(*args, **kwargs):
                try:
                    return orig_owned_span_texts(*args, **kwargs)
                except TypeError:
                    return None
            mineru_native._owned_span_texts = safe_owned_span_texts
        except Exception:
            pass

        from mineru.parser import parse as local_parse
        from mineru.kit.common import save_parse_result
        from mineru.types import validate_tier

        path_tier = validate_tier(tier) if tier else "standard"
        print(f"[Runner] Parsing {pdf_path} (tier={path_tier}, ocr_mode={ocr_mode}, pages='{pages}')...", flush=True)

        result = local_parse(
            pdf_path,
            tier=path_tier,
            ocr_mode=ocr_mode,
            image_analysis=True,
            page_range=pages,
        )

        zip_dest.parent.mkdir(parents=True, exist_ok=True)
        save_parse_result(result, zip_dest, "zip")

        # Custom Markdown Generation Stage:
        # Replaces the default markdown in the zip bundle with custom markdown generated
        # from middle_json and PDF font-size based heading detection.
        try:
            from custom_markdown import render_custom_markdown
            import zipfile

            print(f"[Runner] Generating custom markdown using JSON and PDF font-size heading levels...", flush=True)
            custom_md = render_custom_markdown(result.middle_json, str(pdf_path))

            if custom_md and os.path.exists(zip_dest):
                temp_zip = str(zip_dest) + ".custom.tmp"
                with zipfile.ZipFile(zip_dest, "r") as zin:
                    with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zout:
                        for item in zin.infolist():
                            if item.filename.lower() == "markdown.md":
                                zout.writestr(item, custom_md.encode("utf-8"))
                            else:
                                zout.writestr(item, zin.read(item.filename))
                if os.path.exists(temp_zip):
                    os.replace(temp_zip, zip_dest)
                print(f"[Runner] Successfully updated zip bundle with custom markdown.", flush=True)
        except Exception as md_err:
            print(f"[Runner Warning] Custom markdown generation fallback: {md_err}", flush=True)
            traceback.print_exc()

        print(f"[Runner] Saved result to {zip_dest}", flush=True)

    except Exception:
        print("\n" + "=" * 30 + " COMPLETE STACK TRACE " + "=" * 30, file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        print("=" * 82 + "\n", file=sys.stderr, flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
