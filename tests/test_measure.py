import measure


def test_parse_short_term_extracts_time_and_value():
    stdout = (
        "frame:0    pts:0       pts_time:0\n"
        "lavfi.r128.S=-120.691\n"
        "frame:1    pts:4410    pts_time:0.1\n"
        "lavfi.r128.S=-35.463\n"
    )
    assert measure.parse_short_term(stdout) == [(0.0, -120.691), (0.1, -35.463)]


def test_parse_short_term_ignores_unrelated_lines():
    stdout = "some noise\nframe:0    pts:0       pts_time:0\nlavfi.r128.M=-9.9\nlavfi.r128.S=-20.0\n"
    assert measure.parse_short_term(stdout) == [(0.0, -20.0)]


def test_parse_short_term_handles_inf():
    stdout = "frame:0    pts:0       pts_time:0\nlavfi.r128.S=-inf\n"
    assert measure.parse_short_term(stdout) == [(0.0, float("-inf"))]


def test_parse_short_term_empty_input_returns_empty_list():
    assert measure.parse_short_term("") == []
