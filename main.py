"""Run URL -> Scrapling job JSON -> tailored application PDFs. Never submits applications."""

import argparse
import json
from pathlib import Path
import sys

from extract_job import ROOT, scrape_job
from generate_documents import add_generation_options, generate_documents, generation_options


def run_pipeline(url, *, state_dir=ROOT / ".scrapling", data_dir=None,
                 headed=False, html_path=None, **document_options):
    """Extraction must succeed before any LLM generation starts."""
    output_root = Path(document_options.get("output_dir", ROOT))
    print("Extracting the job description with Scrapling...", file=sys.stderr)
    job_path, job = scrape_job(url, state_dir=state_dir,
                               output_dir=data_dir if data_dir is not None else output_root / "data",
                               headed=headed, html_path=html_path)
    print(f"Extracted: {job['title']} - {job['company']}", file=sys.stderr)
    documents = generate_documents(job_path, **document_options)
    return {"status": "ok", "job": str(job_path.resolve()), "title": job["title"],
            "company": job["company"], "extraction": job["extraction"], **documents}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", help="Indeed or LinkedIn job URL; prompts if omitted")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".scrapling")
    parser.add_argument("--data-dir", type=Path, help="Job JSON directory (default: OUTPUT_DIR/data)")
    parser.add_argument(
        "--headed", action="store_true",
        help="Show the dedicated browser (required for first-time LinkedIn sign-in)",
    )
    parser.add_argument("--html", type=Path, help="Use a captured rendered DOM instead of fetching (testing/offline)")
    add_generation_options(parser)
    args = parser.parse_args(argv)
    try:
        url = args.url
        if not url:
            print("Job URL: ", end="", file=sys.stderr, flush=True)
            url = input().strip()
        result = run_pipeline(url, state_dir=args.state_dir, data_dir=args.data_dir,
                              headed=args.headed, html_path=args.html, **generation_options(args))
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled"}), file=sys.stderr)
        return 130
    except Exception as error:
        # The CLI boundary includes browser/transport errors. A failure never
        # falls back to stale saved job data or submits an application.
        print(json.dumps({"status": "needs_review", "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
