from sessionbuddy.platform.http import attachment_header


def test_attachment_header_uses_ascii_fallback_and_utf8_filename() -> None:
    header = attachment_header('..東 ü\r\n"slides".pdf')

    header.encode("latin-1")
    assert "\r" not in header and "\n" not in header
    assert 'filename="slides.pdf"' in header
    assert "filename*=UTF-8''..%E6%9D%B1%20%C3%BC--%22slides%22.pdf" in header


def test_attachment_header_has_a_bounded_nonempty_fallback() -> None:
    header = attachment_header("東" * 300)

    assert 'filename="download"' in header
    assert len(header.split('filename="', 1)[1].split('"', 1)[0]) <= 120


def test_attachment_header_reserves_a_distinguishing_ascii_suffix() -> None:
    header = attachment_header(
        f"{'conference-' * 30}review-details-deadbeef.csv",
        ascii_suffix="-review-details-deadbeef.csv",
    )
    fallback = header.split('filename="', 1)[1].split('"', 1)[0]

    assert len(fallback) <= 120
    assert fallback.endswith("-review-details-deadbeef.csv")


def test_attachment_header_bounds_an_untrusted_suffix() -> None:
    header = attachment_header("report.csv", ascii_suffix=f"-{'x' * 300}.csv")
    fallback = header.split('filename="', 1)[1].split('"', 1)[0]

    assert len(fallback) <= 120


def test_attachment_header_removes_paths_controls_and_repeated_dots() -> None:
    header = attachment_header("../folder\\a....b\x00.json")

    assert "%2F" not in header and "%5C" not in header and "%00" not in header
    assert 'filename="folder-a.b-.json"' in header
