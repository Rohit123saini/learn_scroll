"""ppt/doc -> PDF cache logic for live "Present" (no DB, no LibreOffice needed: SimpleTestCase + mocks)."""
import io
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from tuitionclass import office_convert
from tuitionclass.office_convert import OfficeConvertError, ensure_presentable_pdf


def _pdf_bytes(pages=2):
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class _FakeField:
    def __init__(self, data):
        self._data = data

    def __bool__(self):
        return True

    def open(self, mode="rb"):
        return io.BytesIO(self._data)


def _material(cached=None, source_name=""):
    return SimpleNamespace(
        pk=11, file=SimpleNamespace(name="slides/a.pptx", size=1000),
        presentable_pdf=_FakeField(cached) if cached else None, presentable_source_name=source_name,
    )


class EnsurePresentablePdfTests(SimpleTestCase):
    def test_cached_pdf_for_same_source_is_reused_without_libreoffice(self):
        m = _material(cached=_pdf_bytes(3), source_name="slides/a.pptx")
        with mock.patch.object(office_convert, "_run_soffice") as run:
            self.assertEqual(ensure_presentable_pdf(m), 3)
        run.assert_not_called()

    def test_replaced_source_file_is_not_served_from_old_cache(self):
        m = _material(cached=_pdf_bytes(3), source_name="slides/OLD.pptx")
        with mock.patch.object(office_convert, "_office_bin", return_value=None):
            with self.assertRaises(OfficeConvertError):
                ensure_presentable_pdf(m)

    def test_missing_libreoffice_gives_upload_as_pdf_message(self):
        with mock.patch.object(office_convert, "_office_bin", return_value=None):
            with self.assertRaises(OfficeConvertError) as ctx:
                ensure_presentable_pdf(_material())
        self.assertIn("PDF", str(ctx.exception))

    def test_material_without_file_is_refused(self):
        m = _material()
        m.file = None
        with self.assertRaises(OfficeConvertError):
            ensure_presentable_pdf(m)

    def test_oversized_source_is_refused_before_converting(self):
        m = _material()
        m.file.size = office_convert.MAX_SOURCE_BYTES + 1
        with mock.patch.object(office_convert, "_office_bin", return_value="soffice"), \
                mock.patch.object(office_convert, "_run_soffice") as run:
            with self.assertRaises(OfficeConvertError):
                ensure_presentable_pdf(m)
        run.assert_not_called()
