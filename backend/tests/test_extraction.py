"""Extraction: the stage that turns bytes into something searchable.

The interesting cases are not the happy ones. They are the document that
parses successfully and yields nothing, and the document whose declared type
is a lie.
"""
import pytest

from factories import docx_bytes, html_bytes, pdf_bytes
from indexer.managers import extraction


# ---------------------------------------------------------------- sniffing

class TestSniff:
    @pytest.mark.parametrize("raw,expected", [
        (pdf_bytes(),                     "pdf"),
        (docx_bytes(),                    "zip"),
        (html_bytes(),                    "html"),
        (b"<html><body>hi</body></html>", "html"),
        (b"plain text",                   "text"),
        (b"{\\rtf1\\ansi hello}",         "rtf"),
        (b"\x00\x01\x02\xff\xfe binary",  "binary"),
    ])
    def test_recognises_by_magic_bytes(self, raw, expected):
        assert extraction.sniff(raw) == expected

    def test_a_mislabelled_pdf_is_still_a_pdf(self):
        """The client says text/plain; the bytes say otherwise.

        Trusting Content-Type here would put binary in the search index.
        sniff() never sees the declared type, which is the point.
        """
        assert extraction.sniff(pdf_bytes()) == "pdf"

    def test_leading_whitespace_does_not_hide_html(self):
        assert extraction.sniff(b"\n\n   <!DOCTYPE html><html>") == "html"

    def test_utf8_is_text_not_binary(self):
        assert extraction.sniff("naïve café 文書".encode()) == "text"


# --------------------------------------------------------------------- pdf

class TestPdf:
    def test_extracts_text_and_counts_pages(self):
        text, pages = extraction.extract(pdf_bytes(pages=3))
        assert "Refund policy" in text
        assert pages == 3

    def test_a_scan_raises_needs_ocr_rather_than_indexing_nothing(self):
        """pypdf does not error on a scan — it returns "". Without the
        MIN_PDF_TEXT guard we would index an empty document that can never
        be found, and nobody would ever notice."""
        with pytest.raises(extraction.NeedsOCR):
            extraction.extract(pdf_bytes(text=None))

    def test_a_nearly_empty_pdf_counts_as_a_scan(self):
        with pytest.raises(extraction.NeedsOCR):
            extraction.extract(pdf_bytes(text="x"))

    def test_just_over_the_threshold_is_accepted(self):
        text, _ = extraction.extract(pdf_bytes(text="y" * 60))
        assert len(text) >= extraction.MIN_PDF_TEXT

    def test_truncated_pdf_is_permanent_not_transient(self):
        """Classified UnsupportedType so it skips the retry ladder. Retrying
        a corrupt file three times reaches the same answer, minutes later,
        with everything behind it delayed."""
        with pytest.raises(extraction.UnsupportedType):
            extraction.extract(pdf_bytes()[:120])


# -------------------------------------------------------------------- docx

class TestDocx:
    def test_extracts_paragraphs(self):
        text, _ = extraction.extract(docx_bytes(), filename="report.docx")
        assert "Quarterly report" in text
        assert "Revenue grew by 12 percent." in text

    def test_extracts_table_cells(self):
        """Tables are where the numbers live in most business documents;
        paragraph-only extraction silently drops them."""
        raw = docx_bytes(paragraphs=("Summary",),
                         table_rows=(("Region", "Revenue"), ("EMEA", "4.2M")))
        text, _ = extraction.extract(raw, filename="q3.docx")
        assert "EMEA" in text and "4.2M" in text

    def test_blank_paragraphs_are_dropped(self):
        text, _ = extraction.extract(docx_bytes(paragraphs=("a", "", "   ", "b")),
                                     filename="x.docx")
        assert text == "a\nb"


# -------------------------------------------------------------------- html

class TestHtml:
    def test_script_and_style_are_not_indexed(self):
        """Otherwise a search for a CSS colour or a JS identifier matches
        every page on the site."""
        text, _ = extraction.extract(html_bytes())
        assert "Visible text" in text
        assert "secret" not in text
        assert "color:red" not in text

    def test_tags_are_stripped(self):
        text, _ = extraction.extract(
            html_bytes(body="<div><h1>Title</h1><p>Body <b>bold</b></p></div>",
                       head="", script=""))
        assert "<" not in text
        assert "Title" in text and "bold" in text


# ------------------------------------------------------------------- plain

class TestPlainText:
    def test_passes_through(self):
        text, pages = extraction.extract(b"just some text")
        assert (text, pages) == ("just some text", 0)

    def test_undecodable_bytes_are_unsupported(self):
        with pytest.raises(extraction.UnsupportedType):
            extraction.extract(b"\x00\x01\x02\xff\xfe\xfd" * 100)

    def test_invalid_utf8_late_in_a_text_file_does_not_crash(self):
        """sniff() only inspects the first 4 KB, so a file can pass the sniff
        and still contain a bad byte further in. errors="replace" keeps that
        a partial result rather than a dead-lettered message."""
        raw = b"a" * 5000 + b"\xff\xfe"
        text, _ = extraction.extract(raw)
        assert text.startswith("aaaa")
