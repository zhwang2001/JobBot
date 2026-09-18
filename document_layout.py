"""ATS-safe Word output, PDF conversion, and a strict one-page layout gate."""

from datetime import date
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from pypdf import PdfReader


def find_soffice(explicit=None):
    configured = explicit or os.environ.get("JOBBOT_SOFFICE")
    if configured:
        candidate = shutil.which(configured)
        if candidate:
            return candidate
        raise ValueError("JOBBOT_SOFFICE/--soffice does not point to an executable.")
    # Prefer the Codex bundle when present; otherwise use a user's CLI install.
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice"
    if bundled.is_file():
        return str(bundled)
    candidate = shutil.which("soffice")
    if candidate:
        return candidate
    raise ValueError("LibreOffice is required for one-page verification and PDF output. Set JOBBOT_SOFFICE or --soffice to its executable.")


def setup_document(kind):
    document = Document()
    # Some bundled Word templates put a blue border under Title. Remove it
    # explicitly rather than relying on font colour to clear inherited rules.
    for border in list(document.styles.element.iter(qn("w:pBdr"))):
        border.getparent().remove(border)
    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(0.6 if kind == "resume" else 0.75)
    section.left_margin = section.right_margin = Inches(0.7)
    body_size = 10.5 if kind == "resume" else 11.5
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "List Bullet"):
        style = document.styles[name]
        style.font.name = "Arial"
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.size = Pt(body_size)
        style.paragraph_format.space_before = Pt(0)
        style.paragraph_format.space_after = Pt(4 if kind == "resume" else 10)
        style.paragraph_format.line_spacing = 1.1 if kind == "resume" else 1.3
    title = document.styles["Title"]
    title.font.size = Pt(20)
    title.font.bold = True
    title.paragraph_format.space_after = Pt(3)
    heading = document.styles["Heading 1"]
    heading.font.size = Pt(11)
    heading.font.bold = True
    heading.paragraph_format.space_before = Pt(9)
    heading.paragraph_format.space_after = Pt(4)
    heading.paragraph_format.keep_with_next = True
    if kind == "resume":
        add_bottom_border(heading.element.get_or_add_pPr())
    bullet = document.styles["List Bullet"].paragraph_format
    bullet.space_after = Pt(3)
    bullet.left_indent = Inches(0.14)
    bullet.first_line_indent = Inches(-0.14)
    document.core_properties.author = ""
    document.core_properties.title = ""
    return document


def add_bottom_border(properties):
    """Light-gray paragraph rules, not images/tables, matching the reference."""
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in (("val", "single"), ("sz", "4"), ("space", "4"), ("color", "D9D9D9")):
        bottom.set(qn(f"w:{key}"), value)
    borders.append(bottom)
    properties.append(borders)


def add_header(document, profile):
    contact = profile["contact"]
    name = document.add_paragraph(contact["name"], "Title")
    add_bottom_border(name._p.get_or_add_pPr())
    # Contact information stays in the document body for ATS reading order.
    line = " | ".join(contact[key] for key in ("phone", "email") if contact.get(key))
    paragraph = document.add_paragraph(line)
    paragraph.paragraph_format.space_after = Pt(2)
    if contact.get("linkedin"):
        document.add_paragraph(contact["linkedin"])


def dates(role):
    # Unknown dates stay absent, never guessed or replaced with placeholders.
    if not role.get("start") or not role.get("end"):
        return ""
    return f"{role['start']} - {role['end']}"


def write_resume(draft, profile, job, path):
    document = setup_document("resume")
    add_header(document, profile)
    headline = document.add_paragraph(job["title"])
    headline.runs[0].bold = True
    document.add_paragraph("SUMMARY", "Heading 1")
    document.add_paragraph(draft.summary.text)
    document.add_paragraph("SKILLS", "Heading 1")
    document.add_paragraph("; ".join(skill.text for skill in draft.skills))
    document.add_paragraph("RELEVANT EXPERIENCE", "Heading 1")
    roles = {role["id"]: role for role in profile["experience"]}
    for selected in draft.experience:
        role = roles[selected.experience_id]
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(5)
        paragraph.paragraph_format.space_after = Pt(2)
        paragraph.paragraph_format.keep_with_next = True
        paragraph.add_run(f"{role['title']} | {role['organization']}").bold = True
        # Compensation labels are intentionally not displayed. The title still
        # identifies academic laboratory experience; research-program context
        # remains useful without implying a different kind of employment.
        context = role.get("employment_type", "")
        context = "Research Opportunity Program" if "Research Opportunity Program" in context else ""
        metadata = " | ".join(value for value in (context, dates(role)) if value)
        if metadata:
            paragraph = document.add_paragraph(metadata)
            paragraph.paragraph_format.space_after = Pt(3)
            paragraph.paragraph_format.keep_with_next = True
        for bullet in selected.bullets:
            document.add_paragraph(bullet.text, "List Bullet")
    document.add_paragraph("EDUCATION", "Heading 1")
    for education in profile["education"]:
        line = f"{education['degree']} | {education['institution']}"
        if education.get("end_year"):
            line += f" | {education['end_year']}"
        document.add_paragraph(line)
        if education.get("majors"):
            document.add_paragraph("Majors: " + ", ".join(education["majors"]))
    if draft.certification_names:
        document.add_paragraph("TRAINING", "Heading 1")
        document.add_paragraph("; ".join(draft.certification_names))
    document.save(path)


def write_cover_letter(draft, profile, job, path, today=None):
    document = setup_document("cover-letter")
    add_header(document, profile)
    document.add_paragraph((today or date.today()).strftime("%B %d, %Y"))
    document.add_paragraph(f"Hiring Manager\n{job['company']}")
    subject = document.add_paragraph(f"Application for {job['title']}")
    subject.runs[0].bold = True
    document.add_paragraph("Dear Hiring Manager,")
    for text in [draft.opening, *(example.text for example in draft.examples), draft.closing]:
        document.add_paragraph(text)
    document.add_paragraph(f"Sincerely,\n{profile['contact']['name']}")
    document.save(path)


def to_pdf(docx_path, soffice):
    docx_path = Path(docx_path).resolve()
    with tempfile.TemporaryDirectory(prefix="jobbot-office-") as directory:
        profile = Path(directory) / "profile"
        result = subprocess.run(
            [soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless",
             "--convert-to", "pdf:writer_pdf_Export", "--outdir", directory, str(docx_path)],
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "TMPDIR": tempfile.gettempdir()},
        )
        generated = Path(directory) / docx_path.with_suffix(".pdf").name
        if result.returncode or not generated.is_file():
            raise ValueError("LibreOffice PDF conversion failed: " + (result.stderr or result.stdout)[-1000:])
        pdf = docx_path.with_suffix(".pdf")
        shutil.copyfile(generated, pdf)
    reader = PdfReader(pdf)
    if not any(page.extract_text().strip() for page in reader.pages):
        raise ValueError("PDF has no extractable text; unsuitable for ATS submission.")
    return pdf, len(reader.pages)


def render_draft(draft, profile, job, directory, soffice):
    """Render into a staging directory; publish only after every page check passes."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    output, overflow = {}, []
    for kind, content, writer in (
        ("resume", draft.resume, write_resume),
        ("cover-letter", draft.cover_letter, write_cover_letter),
    ):
        if content is None:
            continue
        path = directory / f"{kind}.docx"
        writer(content, profile, job, path)
        pdf, pages = to_pdf(path, soffice)
        if pages != 1:
            overflow.append(f"{kind} is {pages} pages; shorten lower-value content to fit one page without changing fonts or spacing.")
        # DOCX is a temporary conversion intermediate, never a public output.
        output[kind] = {"pdf": pdf}
    return output, overflow
