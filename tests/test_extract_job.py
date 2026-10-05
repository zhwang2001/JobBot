import tempfile
import unittest

from extract_job import ExtractionError, canonical_url, extract, scrape_job


URL = "https://ca.indeed.com/?r=us&vjk=d3e9cd48aa379808"
LINKEDIN_URL = (
    "https://www.linkedin.com/jobs/search-results/?currentJobId=4466238218&"
    "keywords=research%20assistant&start=25"
)
DESCRIPTION = "Evaluate quality, document findings, and work with the team to improve service. " * 5
HTML = f"""<html><body><main>
<h1 data-testid="jobsearch-JobInfoHeader-title">QA Analyst</h1>
<div data-testid="inlineHeader-companyName">Example Employer</div>
<div data-testid="inlineHeader-companyLocation">Toronto, ON</div>
<div id="salaryInfoAndJobType">$20 an hour - Full-time</div>
<section class="description-section"><h2>Full job description</h2>
<div id="jobDescriptionText" class="jobsearch-JobComponent-description"><p>{DESCRIPTION}</p></div>
</section></main></body></html>"""
LINKEDIN_HTML = f"""<html><head><title>Research Assistant | LinkedIn</title></head><body>
<main>
<h1 class="job-details-jobs-unified-top-card__job-title">Research Assistant</h1>
<div class="job-details-jobs-unified-top-card__company-name"><a>Example Laboratory</a></div>
<div class="job-details-jobs-unified-top-card__tertiary-description-container">
  <span class="tvm__text--low-emphasis">Toronto, Ontario, Canada</span>
</div>
<div class="job-details-preferences-and-skills__pill">Full-time</div>
<article id="job-details"><p>{DESCRIPTION}</p></article>
</main></body></html>"""
LINKEDIN_REACT_HTML = f"""<html><head><title>Research Assistant | LinkedIn</title></head><body>
<main><section>
  <div class="job-header">
    <div class="company-row"><div><a><div aria-label="Company, Example Laboratory.">
      <p>Example Laboratory</p>
    </div></a></div></div>
    <div>Research Assistant</div>
    <div></div>
    <p>Toronto, ON &middot; 3 weeks ago &middot; 18 applicants</p>
  </div>
  <div class="employment-details"><a>On-site</a><a>Full-time</a></div>
  <div class="description-card">
    <div><h2>About the job</h2></div>
    <p>{DESCRIPTION}</p>
  </div>
</section></main></body></html>"""


class ExtractionTests(unittest.TestCase):
    def test_canonicalization(self):
        self.assertEqual(canonical_url(URL)[0], "https://ca.indeed.com/viewjob?jk=d3e9cd48aa379808")
        self.assertEqual(
            canonical_url(LINKEDIN_URL),
            ("https://www.linkedin.com/jobs/view/4466238218/", "4466238218"),
        )
        self.assertEqual(
            canonical_url("https://www.linkedin.com/jobs/view/research-assistant-at-example-4466238218/")[0],
            "https://www.linkedin.com/jobs/view/4466238218/",
        )
        for url in (
            "https://ca.indeed.com.evil.test/?jk=d3e9cd48aa379808",
            "https://www.linkedin.com.evil.test/jobs/view/4466238218/",
            "https://www.linkedin.com/jobs/search-results/?keywords=research",
            "https://ca.indeed.com/", "file:///tmp/job.html",
        ):
            with self.assertRaises(ExtractionError):
                canonical_url(url)

    def test_linkedin_persistent_learning_and_changed_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            first = extract(LINKEDIN_HTML, LINKEDIN_URL, directory)
            self.assertEqual(first["job_id"], "4466238218")
            self.assertEqual(first["canonical_url"], "https://www.linkedin.com/jobs/view/4466238218/")
            self.assertEqual(first["company"], "Example Laboratory")
            self.assertEqual(first["location"], "Toronto, Ontario, Canada")
            self.assertEqual(first["pay_and_type"], "Full-time")
            self.assertEqual(first["extraction"]["board"], "linkedin")
            self.assertIn("description", first["extraction"]["learned"])
            self.assertEqual(extract(LINKEDIN_HTML, LINKEDIN_URL, directory)["extraction"]["learned"], [])
            changed = LINKEDIN_HTML.replace('id="job-details"', 'id="revised-job-details"')
            adapted = extract(changed, LINKEDIN_URL, directory)
            self.assertEqual(adapted["description"], first["description"])
            self.assertEqual(adapted["extraction"]["methods"]["description"], "adaptive")
            changed_details = changed.replace(
                "job-details-preferences-and-skills__pill", "revised-employment-details"
            )
            adapted_details = extract(changed_details, LINKEDIN_URL, directory)
            self.assertEqual(adapted_details["pay_and_type"], "Full-time")
            self.assertEqual(adapted_details["extraction"]["methods"]["pay_and_type"], "adaptive")

    def test_current_linkedin_react_layout_uses_semantic_anchors(self):
        with tempfile.TemporaryDirectory() as directory:
            result = extract(LINKEDIN_REACT_HTML, LINKEDIN_URL, directory)
            self.assertEqual(result["title"], "Research Assistant")
            self.assertEqual(result["company"], "Example Laboratory")
            self.assertEqual(result["location"], "Toronto, ON")
            self.assertEqual(result["pay_and_type"], "On-site Full-time")
            self.assertEqual(result["description"], DESCRIPTION.strip())
            self.assertEqual(result["extraction"]["learned"], [
                "title", "company", "location", "description", "pay_and_type",
            ])

    def test_linkedin_login_page_never_trains(self):
        login = """<html><head><title>Sign in | LinkedIn</title></head><body>
        <form action="/uas/login-submit"><input name="session_key"></form></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ExtractionError, "--headed"):
                extract(login, LINKEDIN_URL, directory)
            self.assertEqual(len(extract(LINKEDIN_HTML, LINKEDIN_URL, directory)["extraction"]["learned"]), 5)

    def test_linkedin_optional_employment_details_can_be_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            result = extract(
                LINKEDIN_HTML.replace(
                    '<div class="job-details-preferences-and-skills__pill">Full-time</div>', ""
                ),
                LINKEDIN_URL,
                directory,
            )
            self.assertEqual(result["pay_and_type"], "")
            self.assertEqual(result["extraction"]["methods"]["pay_and_type"], "missing_optional")

    def test_linkedin_captured_html_uses_board_specific_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            html = f"{directory}/linkedin.html"
            with open(html, "w", encoding="utf-8") as stream:
                stream.write(LINKEDIN_HTML)
            path, result = scrape_job(
                LINKEDIN_URL, state_dir=f"{directory}/state",
                output_dir=f"{directory}/data", html_path=html,
            )
            self.assertEqual(path.name, "linkedin-4466238218.json")
            self.assertEqual(result["extraction"]["source"], "captured_html")

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
