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
