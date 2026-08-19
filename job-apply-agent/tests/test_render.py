"""Markdown resumes must become attachable files."""

from __future__ import annotations

from pathlib import Path

from jobapply.render import ensure_attachable, markdown_to_html, render_markdown_pdf

SAMPLE = """# Jane Doe

**Cloud Security Engineer**

> Template warning that must not reach the document.

## Experience

### Acme — Engineer
*2021 – Present*

- Did the thing.
"""


def test_markdown_becomes_html():
    html = markdown_to_html(SAMPLE)
    assert "<h1>Jane Doe</h1>" in html
    assert "<li>Did the thing.</li>" in html
    assert "blockquote { display: none" in html  # warnings hidden in print


def test_render_produces_a_file_and_caches_it(tmp_path):
    src = tmp_path / "resume.md"
    src.write_text(SAMPLE, encoding="utf-8")
    out = render_markdown_pdf(src, out_dir=tmp_path / "out")
    assert out.exists() and out.stat().st_size > 0
    assert render_markdown_pdf(src, out_dir=tmp_path / "out") == out  # cached


def test_editing_the_source_produces_a_new_file(tmp_path):
    src = tmp_path / "resume.md"
    src.write_text(SAMPLE, encoding="utf-8")
    first = render_markdown_pdf(src, out_dir=tmp_path / "out")
    src.write_text(SAMPLE + "\n- And another thing.\n", encoding="utf-8")
    second = render_markdown_pdf(src, out_dir=tmp_path / "out")
    assert first != second


def test_pdf_and_docx_pass_through_untouched(tmp_path):
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    assert ensure_attachable(pdf) == pdf


def test_missing_file_raises(tmp_path):
    try:
        ensure_attachable(Path(tmp_path / "nope.md"))
    except FileNotFoundError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")
