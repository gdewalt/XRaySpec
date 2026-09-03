"""Google Patents PDF-link extraction (DESIGN.md §11.2)."""

from __future__ import annotations

import pytest

from app.fetch.errors import FetchError
from app.fetch.google_patents import extract_pdf_url, patent_page_url

PDF = "https://patentimages.storage.googleapis.com/pdfs/US12262260B2.pdf"


def test_page_url():
    assert patent_page_url("US12262260B2") == "https://patents.google.com/patent/US12262260B2/en"


def test_extract_pdf_url():
    html = f'<html><head><meta name="citation_pdf_url" content="{PDF}"></head></html>'
    assert extract_pdf_url(html) == PDF


def test_extract_pdf_url_reversed_attr_order():
    html = f'<meta content="{PDF}" name="citation_pdf_url">'
    assert extract_pdf_url(html) == PDF


def test_missing_pdf_link():
    with pytest.raises(FetchError) as e:
        extract_pdf_url("<html><head><title>x</title></head></html>")
    assert e.value.code == "no_pdf_link"
