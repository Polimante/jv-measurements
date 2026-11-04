"""NPLC depends on the power line: 16.7 ms at 60 Hz, 20 ms at 50 Hz.

The instrument integrates correctly either way, but the scan rate the software
calculates, displays and saves must use the frequency the instrument reports.
At NPLC 10 the 60 Hz assumption on a 50 Hz grid overstates the rate by ~13 %.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import openpyxl
import pytest

from app.gui.data_viewer import load_jv_file
from app.gui.input_validation import validate_scan_rate
from app.instrument.tsp_transport import TSPTransport
from app.io.exporter import save_data
from app.simulation.fake_keithley import FakeKeithley
from app.utils.scan_timing import (
    DEFAULT_LINE_HZ,
    format_scan_times,
    measured_scan_rate_mv_s,
    nplc_duration_ms,
    read_line_frequency,
    scan_rate_mv_s,
)


class TestArithmetic:
    @pytest.mark.parametrize("nplc, hz, expected", [
        (1, 60, 16.667), (1, 50, 20.0), (10, 50, 200.0), (0.01, 50, 0.2),
    ])
    def test_nplc_duration(self, nplc, hz, expected):
        assert nplc_duration_ms(nplc, hz) == pytest.approx(expected, abs=1e-3)

    def test_none_means_the_default_grid(self):
        assert DEFAULT_LINE_HZ == 60.0
        assert nplc_duration_ms(1, None) == nplc_duration_ms(1, 60)

    def test_scan_rate_at_each_frequency(self):
        # 25 mV, delay 33.3 ms, NPLC 10, 50 ms overhead.
        assert scan_rate_mv_s(25, 33.3, 10, 60) == pytest.approx(100.0, abs=0.1)
        assert scan_rate_mv_s(25, 33.3, 10, 50) == pytest.approx(88.2, abs=0.1)

    def test_scan_rate_not_computable(self):
        assert scan_rate_mv_s(0, 10, 1, 60) is None
        assert scan_rate_mv_s(25, 0, 0, 60, overhead_ms=0) is None

    def test_validator_clamps_according_to_the_frequency(self):
        # 250 ms per point: NPLC 11 leaves 16.7 ms at 60 Hz but nothing at 50 Hz.
        assert validate_scan_rate(100.0, 25.0, 11.0, line_hz=60).warning is None
        res = validate_scan_rate(100.0, 25.0, 11.0, line_hz=50)
        assert res.warning.code == "scan_rate_clamped"


class TestReadLineFrequency:
    @pytest.mark.parametrize("hz", [50.0, 60.0])
    def test_scpi_instrument(self, hz):
        assert read_line_frequency(FakeKeithley(line_hz=hz)) == hz

    @pytest.mark.parametrize("reply, expected", [
        ("5.00000e+01\n", 50.0),
        ("6.00000e+01\n", 60.0),
        ("50", 50.0),
        ("nil\n", None),      # firmware without the attribute
        ("0,0,0,0,0", None),
        ("400", None),
        ("", None),
    ])
    def test_reply_parsing(self, reply, expected):
        inst = MagicMock()
        inst.query.return_value = reply
        assert read_line_frequency(inst) == expected

    def test_a_2600_is_asked_in_tsp(self):
        raw = MagicMock()
        raw.query.return_value = "5.00000e+01\n"
        assert read_line_frequency(TSPTransport(raw, channel="A")) == 50.0
        raw.query.assert_called_once_with("print(localnode.linefreq)")

    def test_no_answer_returns_none_and_restores_the_timeout(self):
        raw = MagicMock()
        raw.timeout = 10000
        seen = []

        def query(_cmd):
            seen.append(raw.timeout)
            raise TimeoutError("VI_ERROR_TMO")

        raw.query.side_effect = query
        assert read_line_frequency(TSPTransport(raw, channel="A")) is None
        assert seen == [3000]          # shortened on the real session
        assert raw.timeout == 10000


def _save(folder, **kw):
    v = np.linspace(-0.1, 1.0, 12)
    j = 18 - 18 * np.clip(v / 0.8, 0, None) ** 6
    return save_data(
        str(folder), "Cell", v, j, v[::-1], j[::-1], 100.0,
        0.8, 18, 70, 10.08, 5, 1e4, 0.8, 18, 70, 10.08, 5, 1e4, 0.0,
        0.06, 33.3, 10.0, False, False, 0, 0, "IDN", **kw)


class TestSavedFile:
    def test_column_af_round_trip(self, tmp_path):
        fp = _save(tmp_path, line_hz=50.0)
        ws = openpyxl.load_workbook(fp)["MeasurementData"]
        assert ws.cell(1, 32).value == "Line Frequency (Hz)"
        assert ws.cell(2, 32).value == 50
        assert load_jv_file(fp)["line_hz"] == 50

    def test_unread_frequency_leaves_the_cell_empty(self, tmp_path):
        fp = _save(tmp_path)
        assert openpyxl.load_workbook(fp)["MeasurementData"].cell(2, 32).value is None
        assert load_jv_file(fp)["line_hz"] is None

    def test_fallback_scan_rate_uses_the_frequency(self, tmp_path):
        # No explicit scan_rate: the exporter computes it itself.
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        at_60 = load_jv_file(_save(tmp_path / "a"))
        at_50 = load_jv_file(_save(tmp_path / "b", line_hz=50.0))
        assert at_50["scan_rate"] < at_60["scan_rate"]


class TestMeasuredTime:
    def test_measured_rate_from_duration(self):
        # 47 points 25 mV apart, each taking 250 ms: a true 100 mV/s.
        v = np.linspace(-0.1, 1.05, 47)
        assert measured_scan_rate_mv_s(v, 47 * 0.25) == pytest.approx(100.0)

    def test_measured_rate_ignores_scan_direction(self):
        v = np.linspace(1.05, -0.1, 47)
        assert measured_scan_rate_mv_s(v, 47 * 0.25) == pytest.approx(100.0)

    @pytest.mark.parametrize("v, seconds", [([0.0], 1.0), ([], 1.0), ([0.0, 1.0], 0.0)])
    def test_measured_rate_not_computable(self, v, seconds):
        assert measured_scan_rate_mv_s(v, seconds) is None

    def test_summary_text(self):
        text = format_scan_times({"fwd_s": 12.34, "fwd_rate": 96.81, "rev_s": 12.5,
                                  "rev_rate": None, "total_s": 27.04})
        assert text == "FWD 12.3 s (96.8 mV/s)  |  REV 12.5 s  |  total 27.0 s"

    def test_columns_round_trip(self, tmp_path):
        fp = _save(tmp_path, timing={"fwd_s": 12.0, "rev_s": 13.0, "total_s": 30.0,
                                     "fwd_rate": 97.0, "rev_rate": 96.0})
        ws = openpyxl.load_workbook(fp)["MeasurementData"]
        assert [ws.cell(1, c).value for c in range(33, 38)] == [
            "Forward Scan Time (s)", "Reverse Scan Time (s)",
            "Total Measurement Time (s)",
            "Measured Forward Scan Rate (mV/s)", "Measured Reverse Scan Rate (mV/s)"]
        meta = load_jv_file(fp)
        assert (meta["fwd_time_s"], meta["rev_time_s"], meta["total_time_s"]) == (12, 13, 30)
        assert (meta["fwd_rate_meas"], meta["rev_rate_meas"]) == (97, 96)

    def test_no_timing_leaves_the_cells_empty(self, tmp_path):
        assert load_jv_file(_save(tmp_path))["fwd_time_s"] is None
