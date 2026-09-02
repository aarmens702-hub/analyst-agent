"""Report.to_html — the shareable single-file report (arc spec R3): the
notebook card as one standalone, self-contained HTML document an analyst can
send to someone who will never install anything."""

import crivo


def test_to_html_writes_one_self_contained_document(tmp_path):
    report = crivo.diagnose(crivo.load_example(), name="example")
    assert report.findings, "the example frame must trip the detector"

    out = report.to_html(tmp_path / "nested" / "quality.html")
    assert out == tmp_path / "nested" / "quality.html"
    text = out.read_text()

    assert text.lower().startswith("<!doctype html")
    assert "example" in text
    assert "sentinel" in text  # a planted finding made it into the document
    assert "<script" not in text.lower()
    assert "http://" not in text and "https://" not in text  # no external assets
