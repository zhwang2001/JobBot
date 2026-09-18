Scrapling (Parse a page, anti-bot guard)
|---> Fetch job description page
    |---> If can parse
        |----> If first time parsing html structure
            |----> Parses the page, 
            |----> provide to llm
            |----> Remembers the relevant page fields
        |----> else
            |----> Parses the page, 
            |----> Automatically remembers the relevant page fields
    |----> If can't parse, because of blocking
        |----> Skip
|---> If 1 click apply available on job board
    |----> generate_documents()
    |----> form_auto_fill()
|--->Else
    |---> Fetch Company form on company website
        |---> If can parse
            |----> If first time parsing html structure
                |----> Parses the page, 
                |----> provide to llm
                |----> Remembers the relevant page fields
                    |----> generate_documents()
                    |----> form_auto_fill()
            |----> else
                |----> Parses the page, 
                |----> Automatically remembers the relevant page fields
                    |----> generate_documents()
                    |---->form_auto_fill()
        |---> If can't parse, because of blocking
            |----> Skip

def form_auto_fill():
    ```
    Parse fields, check for fields, fill it in and press buttons
    Click apply
    ```

def generate_documents():
    ```
    Do we need resume and/or cover letter?
    use job description and master json file to generate the doc(s)
    ```

save applied jobs urls

## Scrapling setup

Python 3.12 is installed in `.venv`; `requirements.txt` pins Scrapling 0.4.15
with its MCP and browser dependencies. To recreate the environment:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/scrapling install
.venv/bin/python -m patchright install chromium
```

Both browser installation commands are needed: the installed Playwright and
Patchright versions use different Chromium builds.

The `scrapling` MCP server is registered in the local Codex configuration using
this project's absolute `.venv/bin/scrapling-mcp` path. Restart the MCP server
in Codex settings (or restart Codex) if its tools do not appear in an existing
session. Reproduce registration from this project with:

```sh
codex mcp add scrapling -- "$PWD/.venv/bin/scrapling-mcp"
.venv/bin/python scripts/check_scrapling_mcp.py
```

The official `scrapling-official` skill, version 0.4.15, is installed at
`/Users/zihaowang/.codex/skills/scrapling-official/SKILL.md`. Its source is
`agent-skill/Scrapling-Skill` in the upstream v0.4.15 release. It includes the
parsing, adaptive extraction, fetcher, spider, and MCP references. Codex can
discover it on the next turn.

## Extract a job description without an LLM

```sh
.venv/bin/python extract_job.py 'https://ca.indeed.com/?r=us&vjk=d3e9cd48aa379808'
```

This adapter currently supports Indeed Canada/US URLs containing `vjk` or `jk`.
It normalizes the job ID to `/viewjob?jk=...`, visits the homepage and then the
job in a persistent Scrapling browser session, and writes the full description,
title, employer, location, and pay/type to `data/indeed-<job-id>.json`.
Use `--headed` to display the browser. It has its own profile under `.scrapling/`.

Initial selector discovery was assisted by Codex. Subsequent execution of this
script makes **no LLM calls**. For each of the two observed Indeed layouts:

1. Validate all extracted fields, then use `auto_save=True` to save their element
   structures in `.scrapling/selectors.db` on the first successful extraction.
2. Try the same selectors on later jobs. If one no longer matches, use
   `adaptive=True` with the saved board/layout/field identifier.
3. Reject missing, ambiguous, low-similarity, or malformed results with exit code
   2 and `status: needs_review`. Preserve the original template for diagnosis.

Adaptive matching is heuristic, not a guarantee for arbitrary redesigns. Blocks,
sign-in pages, and new layouts may still require review. Do not delete
`.scrapling/` if you want to retain learned structures and the browser session.
The latest fetched HTML is retained in `.scrapling/last-response.html` for
diagnosis; generated data and browser state are ignored by Git.

You can also parse a previously captured rendered DOM without network access:

```sh
.venv/bin/python extract_job.py 'https://ca.indeed.com/?r=us&vjk=d3e9cd48aa379808' \
  --html .scrapling/indeed-d3e9cd48aa379808.source.html
.venv/bin/python -m unittest discover -s tests -v
```

Verified September 17, 2026: the full live URL pipeline reused the existing
Scrapling selectors and generated both PDFs in one draft/audit attempt.
Both final PDFs were visually inspected and are one page, with no compensation
labels. All 32 automated tests pass, including the three modes, PDF-only
publication, readable filename handling, repeat-run preservation, and stopping
generation when extraction fails.

The captured-DOM mode trusts the supplied file/URL pairing and records
`source: captured_html`. Live runs verify the response URL and record
`source: live_scrapling_browser`. The extractor does not submit applications.

Verified on September 16, 2026: the supplied job resolved to **Quality Assurance
Analyst, Let's Get Moving, Mississauga, $20/hour, full-time**. A live headless
Scrapling run reused the stored templates with no LLM calls and extracted the
full 1,584-character description. All six tests passed, including simulated
layout changes; the MCP smoke check discovered 13 tools and called
`list_sessions` successfully.

References: [Scrapling](https://github.com/D4Vinci/Scrapling),
[adaptive scraping](https://scrapling.readthedocs.io/en/latest/parsing/adaptive.html),
[official skill](https://scrapling.readthedocs.io/en/latest/ai/agent-skill.html),
[MCP setup](https://developers.openai.com/codex/mcp/).

## Generate a tailored resume and cover letter with Codex

Run the full pipeline from one job URL:

```sh
.venv/bin/python main.py 'https://ca.indeed.com/?r=us&vjk=d3e9cd48aa379808' --mode both
```

Use `--mode resume` or `--mode cover-letter` to request only one document.
Running `main.py` without a URL prompts for one. The command scrapes with the
existing Indeed adapter, saves the job JSON, then calls the document builder.
It stops if extraction fails; it never falls back to stale job data or submits
an application. Only the previously supported Indeed Canada/US URLs are supported.

`--headed` shows the scraping browser; `--state-dir` selects Scrapling's stored
templates/browser profile. `--data-dir` overrides the extraction JSON directory
(default: `OUTPUT_DIR/data`). For a no-network scraping replay:

```sh
.venv/bin/python main.py 'https://ca.indeed.com/?r=us&vjk=d3e9cd48aa379808' \
  --html .scrapling/indeed-d3e9cd48aa379808.source.html --mode both
```

Replay still invokes Codex for document generation. The callable `run_pipeline()`
in `main.py` returns the job JSON, extraction information, PDFs, and audit path.

The builder uses your **signed-in Codex CLI**, not the OpenAI API. Its default
is `gpt-5.6-terra` with medium reasoning: a current balanced model suitable for
evidence-sensitive tailoring. Calls consume your Codex plan's usage allowance;
they are not free/unlimited. You can override the model with `--model` or
`JOBBOT_CODEX_MODEL`. See [Codex models](https://learn.chatgpt.com/docs/models)
and [non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode).

Prerequisites: install `requirements.txt`, have a current `codex` CLI on PATH,
and run `codex login` if `codex login status` is not authenticated. LibreOffice
is required for PDF conversion and page-count verification. The builder detects
Codex's bundled LibreOffice or `soffice` on PATH; alternatively set
`JOBBOT_SOFFICE` or pass `--soffice /path/to/soffice`.

```sh
.venv/bin/python -m pip install -r requirements.txt
codex login status

# Both documents (default)
.venv/bin/python generate_documents.py data/indeed-d3e9cd48aa379808.json --mode both

# Resume only / cover letter only
.venv/bin/python generate_documents.py data/indeed-d3e9cd48aa379808.json --mode resume
.venv/bin/python generate_documents.py data/indeed-d3e9cd48aa379808.json --mode cover-letter
```

Each requested document is published as a **searchable PDF only**. For example:

```text
applications/
  Quality Assurance Analyst - Let's Get Moving/
    James Zihao Wang - Resume.pdf
    James Zihao Wang - Cover Letter.pdf
```

Repeated runs use a new folder ending in `(2)`, `(3)`, etc., retaining the same
readable PDF filenames. Job IDs and run IDs stay in internal JSON records, not
recruiter-facing filenames. DOCX files exist only temporarily during conversion;
they are not published. Exports made by earlier builder versions are untouched.
`--output-dir` changes the output root. `data/` remains extraction JSON only.
The callable `generate_documents()` in `generate_documents.py` returns output
paths for integration with the application workflow. Nothing is submitted.

The authoritative source is `james_wang_master_profile_v1.json`; use `--profile`
to select another compatible profile. Every run includes its ATS, wording,
truthfulness, and layout instructions. The visual-baseline DOCX files named in
the profile were not supplied, so the renderer implements the written layout
specifications, not an exact reproduction of those files. The updated resume
uses uppercase section headings and thin light-gray border lines like the supplied
image. Compensation-status labels have been removed from the master profile and
application text, while academic coursework and research programs stay identifiable.

The pipeline:

1. Ask Codex for a structured draft, treating job-posting text as untrusted data.
2. Validate cited evidence, numeric metrics, role ownership, credentials, and mode.
3. Use a separate Codex call to audit factual claims against the profile.
4. Render single-column documents with body contact information, no tables,
   and the profile's font/spacing specifications. Verify each PDF is exactly one
   page and has extractable text. Shorten wording on overflow, not typography.
5. Publish only passing drafts. Save evidence, audit, input hashes, requested
   model and reported token usage in `.jobbot/runs/<run-id>.json`.

A draft that passes on the first attempt uses two Codex calls; repairs can use up to six at the
default three-attempt limit. `--max-attempts 1` limits it to one draft/audit;
`--timeout` sets the per-call timeout in seconds. Exhausted checks return exit
code 2 and `needs_review`, without publishing application documents. A rejected
draft and its feedback are retained only in `.jobbot/runs/*-needs-review.json`
for diagnosis. AI factual review
is not a guarantee: **read the final documents before applying**, particularly
where the job asks for qualifications missing from the profile.

Codex receives the job description and profile facts/rules. Contact details are
omitted from prompts and rendered locally. Calls use temporary working folders,
read-only sandboxing, disabled shell/web tools, and ephemeral sessions, ignoring
user config so project MCPs and custom plugins are not loaded. Generated files
and evidence logs contain personal information and are Git-ignored. Ephemeral
local execution does not imply a provider-side data-retention guarantee.

```sh
.venv/bin/python -m unittest discover -s tests -v
```
