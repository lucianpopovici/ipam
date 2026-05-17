"""
Stage 4: Convert rendered HTML to a PDF using WeasyPrint.

WeasyPrint is optional — if not installed, PDF generation raises ImportError
with a clear message pointing to the required system dependencies.
"""
import os

PRINT_CSS_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'var', 'ipam', 'template-sets', 'default', 'print.css'
)


def html_to_pdf(html_string, base_url=None):
    """
    Convert an HTML string to PDF bytes using WeasyPrint.

    base_url is used to resolve relative asset paths (images, CSS).
    """
    try:
        from weasyprint import HTML, CSS
    except ImportError as exc:
        raise ImportError(
            "WeasyPrint is required for PDF generation. "
            "Install it with: pip install weasyprint\n"
            "System deps: libcairo2 libpango-1.0-0 libpangoft2-1.0-0 libgdk-pixbuf-2.0-0"
        ) from exc

    stylesheets = []
    if os.path.isfile(PRINT_CSS_PATH):
        stylesheets.append(CSS(filename=PRINT_CSS_PATH))

    pdf_bytes = HTML(
        string=html_string,
        base_url=base_url or '/',
    ).write_pdf(stylesheets=stylesheets or None)

    return pdf_bytes
