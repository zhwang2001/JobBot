"""Structured drafts and evidence checks shared by generation and rendering."""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Claim(StrictModel):
    text: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)


class Role(StrictModel):
    experience_id: str
    bullets: list[Claim] = Field(min_length=3, max_length=5)


class Resume(StrictModel):
    summary: Claim
    skills: list[Claim] = Field(min_length=4, max_length=12)
    experience: list[Role] = Field(min_length=2, max_length=4)
    certification_names: list[str]


class CoverLetter(StrictModel):
    opening: str = Field(min_length=1)
    examples: list[Claim] = Field(min_length=2, max_length=3)
    closing: str = Field(min_length=1)


class Draft(StrictModel):
    resume: Resume | None
    cover_letter: CoverLetter | None


class Review(StrictModel):
    passed: bool
    issues: list[str]


Mode = Literal["resume", "cover-letter", "both"]


def evidence_catalog(profile):
    """Catalog only candidate facts, never restrictions or job requirements."""
    result = {}
    for role in profile["experience"]:
        for fact in role["facts"]:
            if fact.get("safe_to_claim") is True:
                result[fact["id"]] = fact["fact"]
    for key in ("education", "certifications_training", "technical_projects_and_familiarity"):
        for index, value in enumerate(profile.get(key, [])):
            if value.get("safe_to_claim", True) and (key != "education" or value.get("degree")):
                result[f"{key}:{index}"] = json.dumps(value, ensure_ascii=False)

    def walk(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}/{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}/{index}")
        elif value is not None:
            result[path] = f"{path}: {value}"

    walk(profile["skills"], "skills")
    walk(profile.get("availability", {}), "availability")
    return result


def validate_draft(draft, profile, mode):
    if (draft.resume is not None) != (mode in {"resume", "both"}):
        raise ValueError("Resume presence does not match the requested mode.")
    if (draft.cover_letter is not None) != (mode in {"cover-letter", "both"}):
        raise ValueError("Cover-letter presence does not match the requested mode.")
    catalog = evidence_catalog(profile)

    def check_labels(text):
        if re.search(r"\b(?:paid|unpaid)\s+(?:assignment|experience|employment|role|work|research|position|internship)\b", text, re.I):
            raise ValueError("Omit paid/unpaid experience labels; describe the role and work without compensation status.")

    def check(claim, allowed=None):
        check_labels(claim.text)
        refs = set(claim.evidence)
        if not refs <= catalog.keys():
            raise ValueError(f"Unknown evidence: {sorted(refs - catalog.keys())}")
        if allowed is not None and not refs <= allowed:
            raise ValueError("An experience bullet cites facts from a different role.")
        # Prevent invented numeric metrics even if a valid fact ID was supplied.
        source = " ".join(catalog[ref] for ref in refs).replace(",", "")
        numbers = set(re.findall(r"\d+(?:\.\d+)?", source))
        if not set(re.findall(r"\d+(?:\.\d+)?", claim.text.replace(",", ""))) <= numbers:
            raise ValueError("A claim contains a number absent from its cited evidence.")
        if re.search(r"\[(?:insert|name|date|company)|<[^>]+>", claim.text, re.I):
            raise ValueError("A claim contains a placeholder or markup.")

    if draft.resume:
        resume = draft.resume
        check(resume.summary)
        for skill in resume.skills:
            check(skill)
        roles = {role["id"]: role for role in profile["experience"]}
        ids = [role.experience_id for role in resume.experience]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate experience entries.")
        for role in resume.experience:
            if role.experience_id not in roles:
                raise ValueError(f"Unknown experience: {role.experience_id}")
            allowed = {fact["id"] for fact in roles[role.experience_id]["facts"] if fact.get("safe_to_claim") is True}
            for bullet in role.bullets:
                check(bullet, allowed)
        certs = {cert["name"] for cert in profile.get("certifications_training", []) if cert.get("safe_to_claim") is True}
        if not set(resume.certification_names) <= certs:
            raise ValueError("Unknown or unverified certification.")
    if draft.cover_letter:
        letter = draft.cover_letter
        paragraphs = [letter.opening, *(example.text for example in letter.examples), letter.closing]
        for paragraph in paragraphs:
            check_labels(paragraph)
        if any("\n" in paragraph or "\r" in paragraph for paragraph in paragraphs):
            raise ValueError("Each cover-letter field must be one paragraph; do not embed extra paragraphs in opening or closing.")
        if len(letter.opening.split()) > 65 or len(letter.closing.split()) > 55:
            raise ValueError("Keep the cover-letter opening to at most 65 words and closing to at most 55; concrete examples belong only in examples.")
        if sum(len(paragraph.split()) for paragraph in paragraphs) > 280:
            raise ValueError("Cover-letter body exceeds 280 words; tighten to 210-260 words for the one-page layout.")
        for example in draft.cover_letter.examples:
            check(example)
    return draft
