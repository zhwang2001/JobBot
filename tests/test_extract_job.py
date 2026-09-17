import tempfile
import unittest

from extract_job import ExtractionError, canonical_url, extract


URL = "https://ca.indeed.com/?r=us&vjk=d3e9cd48aa379808"
DESCRIPTION = "Evaluate quality, document findings, and work with the team to improve service. " * 5
HTML = f"""<html><body><main>
<h1 data-testid="jobsearch-JobInfoHeader-title">QA Analyst</h1>
<div data-testid="inlineHeader-companyName">Example Employer</div>
<div data-testid="inlineHeader-companyLocation">Toronto, ON</div>
<div id="salaryInfoAndJobType">$20 an hour - Full-time</div>
<section class="description-section"><h2>Full job description</h2>
<div id="jobDescriptionText" class="jobsearch-JobComponent-description"><p>{DESCRIPTION}</p></div>
</section></main></body></html>"""


class ExtractionTests(unittest.TestCase):
    def test_canonicalization(self):
        self.assertEqual(canonical_url(URL)[0], "https://ca.indeed.com/viewjob?jk=d3e9cd48aa379808")
        for url in ("https://ca.indeed.com.evil.test/?jk=d3e9cd48aa379808", "https://ca.indeed.com/", "file:///tmp/job.html"):
            with self.assertRaises(ExtractionError):
                canonical_url(url)

    def test_persistent_learning_and_changed_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            first = extract(HTML, URL, directory)
            self.assertIn("description", first["extraction"]["learned"])
            second = extract(HTML, URL, directory)
            self.assertEqual(second["extraction"]["learned"], [])
            changed = HTML.replace('id="jobDescriptionText"', 'id="revised-description"')
            third = extract(changed, URL, directory)
            self.assertEqual(third["description"], first["description"])
            self.assertEqual(third["extraction"]["methods"]["description"], "adaptive")

    def test_another_job_uses_same_board_template(self):
        with tempfile.TemporaryDirectory() as directory:
            extract(HTML, URL, directory)
            other = HTML.replace("QA Analyst", "QA Lead").replace(DESCRIPTION, DESCRIPTION + "Lead the team.")
            result = extract(other, URL.replace("d3e9cd48aa379808", "0123456789abcdef"), directory)
            self.assertEqual(result["title"], "QA Lead")
            self.assertEqual(result["extraction"]["learned"], [])

    def test_sign_in_does_not_overwrite_template(self):
        with tempfile.TemporaryDirectory() as directory:
            extract(HTML, URL, directory)
            with self.assertRaises(ExtractionError):
                extract("<html><title>Sign in</title><body>Create an account or sign in.</body></html>", URL, directory)
            self.assertEqual(extract(HTML, URL, directory)["extraction"]["learned"], [])

    def test_layout_variants_have_independent_templates(self):
        react = f'''<div data-testid="viewjob-main-content">
        <div data-testid="desktop-job-header">
        <h5 data-testid="vj-job-title">QA Analyst</h5>
        <div data-testid="company-info-metadata"><div>
        <div><a href="/cmp/example">Example Employer</a></div><div>Toronto, ON</div>
        </div></div><div><div aria-label="Pay and type">$20 an hour - Full-time</div></div>
        </div><section><h4>Full job description</h4>
        <div class="simple-job-description-html"><p>{DESCRIPTION}</p></div></section></div>'''
        with tempfile.TemporaryDirectory() as directory:
            extract(HTML, URL, directory)
            first = extract(react, URL, directory)
            self.assertEqual(first["extraction"]["layout"], "react")
            self.assertEqual(len(first["extraction"]["learned"]), 5)
            self.assertEqual(first["location"], "Toronto, ON")
            self.assertEqual(extract(HTML, URL, directory)["extraction"]["learned"], [])
            changed = react.replace('class="simple-job-description-html"', 'class="revised-description"')
            adapted = extract(changed, URL, directory)
            self.assertEqual(adapted["description"], first["description"])
            self.assertEqual(adapted["extraction"]["methods"]["description"], "adaptive")

    def test_bad_first_run_does_not_train(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExtractionError):
                extract(HTML.replace(DESCRIPTION, "Missing"), URL, directory)
            self.assertEqual(len(extract(HTML, URL, directory)["extraction"]["learned"]), 5)


if __name__ == "__main__":
    unittest.main()
