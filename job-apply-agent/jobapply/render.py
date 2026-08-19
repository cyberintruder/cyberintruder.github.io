"""Turn markdown resumes into real PDF attachments.

Rendered PDFs are cached by content hash under `data/generated/`, so editing a
markdown resume automatically produces a fresh PDF and nothing stale is ever
attached to an application.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from . import settings as settings_mod

log = logging.getLogger(__name__)

CSS = """
@page { size: A4; margin: 15mm 16mm; }
body { font-family: "DejaVu Sans", "Helvetica Neue", Arial, sans-serif;
       font-size: 10pt; line-height: 1.45; color: #16181d; }
h1 { font-size: 20pt; margin: 0 0 2pt; letter-spacing: -0.3pt; }
h1 + p { margin: 0 0 6pt; font-size: 11pt; font-weight: 600; color: #2f6f4f; }
h2 { font-size: 11.5pt; margin: 14pt 0 5pt; padding-bottom: 2pt;
     border-bottom: 0.6pt solid #c9cdd4; text-transform: uppercase;
     letter-spacing: 0.6pt; }
h3 { font-size: 10.5pt; margin: 9pt 0 1pt; }
h3 + p em, p em { color: #5b6472; font-size: 9pt; }
ul { margin: 4pt 0 4pt 0; padding-left: 14pt; }
li { margin-bottom: 2.5pt; }
a { color: #2f6f4f; text-decoration: none; }
hr { border: none; border-top: 0.6pt solid #c9cdd4; margin: 8pt 0; }
blockquote { display: none; }  /* template warnings never reach the PDF */
strong { color: #0f1115; }
"""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def markdown_to_html(md_text: str) -> str:
    from markdown_it import MarkdownIt

    body = MarkdownIt("commonmark", {"html": False}).enable("table").render(md_text)
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"


def render_markdown_pdf(md_path: Path, out_dir: Path | None = None, force: bool = False) -> Path:
    """Render a markdown resume to PDF. Returns the cached path when unchanged."""
    st = settings_mod.load()
    out_dir = Path(out_dir or st.generated_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{md_path.stem}-{_digest(md_path)}.pdf"
    if out.exists() and not force:
        return out

    html = markdown_to_html(md_path.read_text(encoding="utf-8"))
    try:
        from weasyprint import HTML  # imported lazily: heavy native deps

        HTML(string=html, base_url=str(md_path.parent)).write_pdf(str(out))
    except Exception as exc:  # noqa: BLE001
        # WeasyPrint needs system libraries (pango/cairo). Rather than fail an
        # application, fall back to attaching the HTML — still a readable
        # document — and make the reason visible in the logs and the UI.
        log.warning("PDF rendering unavailable (%s); falling back to HTML", exc)
        fallback = out.with_suffix(".html")
        fallback.write_text(html, encoding="utf-8")
        return fallback
    return out


def ensure_attachable(path: Path) -> Path:
    """Return a path safe to attach to an email or upload to an ATS.

    `.md` sources are rendered; `.pdf`/`.docx` pass through untouched.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"resume file not found: {path}")
    if path.suffix.lower() == ".md":
        return render_markdown_pdf(path)
    return path


def copy_to_generated(path: Path, name: str) -> Path:
    st = settings_mod.load()
    dest = st.generated_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return dest
