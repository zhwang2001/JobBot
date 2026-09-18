from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from extract_job import ExtractionError
from main import main, run_pipeline
from test_extract_job import HTML, URL


class PipelineTests(unittest.TestCase):
    @patch("main.generate_documents", return_value={"outputs": {}, "manifest": "review.json"})
    def test_captured_html_flows_into_generator_and_reuses_selectors(self, generate):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            html = root / "job.html"
            html.write_text(HTML)
            first = run_pipeline(URL, state_dir=root / "state", output_dir=root,
                                 html_path=html, mode="cover-letter")
            self.assertEqual(first["status"], "ok")
            job_path = Path(first["job"])
            self.assertEqual(job_path.parent, root / "data")
            job = json.loads(job_path.read_text())
            self.assertEqual(job["title"], "QA Analyst")
            self.assertFalse(job["extraction"]["llm_used"])
            self.assertEqual(job["extraction"]["source"], "captured_html")
            self.assertEqual(len(job["extraction"]["learned"]), 5)
            generate.assert_called_once_with(job_path, output_dir=root, mode="cover-letter")
            second = run_pipeline(URL, state_dir=root / "state", output_dir=root, html_path=html)
            self.assertEqual(second["extraction"]["learned"], [])

    @patch("main.generate_documents")
    @patch("main.scrape_job", side_effect=ExtractionError("Challenge page"))
    def test_failed_scrape_never_calls_codex(self, scrape, generate):
        with self.assertRaisesRegex(ExtractionError, "Challenge page"):
            run_pipeline(URL)
        generate.assert_not_called()

    @patch("main.generate_documents")
    def test_bad_url_never_calls_codex(self, generate):
        with self.assertRaises(ExtractionError):
            run_pipeline("https://evil.example/job")
        generate.assert_not_called()

    @patch("main.run_pipeline", return_value={"status": "ok", "outputs": {}})
    def test_cli_forwards_options_and_emits_json(self, pipeline):
        for mode in ("resume", "cover-letter", "both"):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status = main([URL, "--mode", mode, "--output-dir", "/tmp/jobbot-test", "--headed"])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ok")
            self.assertEqual(pipeline.call_args.kwargs["mode"], mode)
            self.assertEqual(pipeline.call_args.kwargs["output_dir"], Path("/tmp/jobbot-test"))
            self.assertTrue(pipeline.call_args.kwargs["headed"])

    @patch("main.run_pipeline", side_effect=ExtractionError("Blocked"))
    def test_cli_failure_returns_needs_review(self, pipeline):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = main([URL])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stderr.getvalue())["status"], "needs_review")

    @patch("builtins.input", return_value=URL)
    @patch("main.run_pipeline", return_value={"status": "ok"})
    def test_cli_prompts_for_missing_url(self, pipeline, user_input):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main([]), 0)
        self.assertEqual(pipeline.call_args.args, (URL,))


if __name__ == "__main__":
    unittest.main()
