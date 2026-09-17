from io import BytesIO
import unittest
from unittest.mock import Mock, patch
from zipfile import ZIP_DEFLATED, ZipFile

import scanner
from stock_universe import (
    KIS_MASTER_URL_BASE,
    KOSDAQ_FIELD_WIDTHS,
    KOSPI_FIELD_WIDTHS,
    STOCK_UNIVERSE_SOURCE,
    get_stock_universe,
)
from test_daily_batch import scan_cache


LAST_MODIFIED = "Thu, 17 Sep 2026 08:10:03 GMT"


def master_line(
    code: str,
    name: str,
    market_cap: int,
    widths: tuple[int, ...],
    security_group: str = "ST",
) -> str:
    fields = ["0" * width for width in widths]
    fields[0] = security_group.ljust(widths[0])
    fields[-5] = str(market_cap).zfill(widths[-5])
    fixed = "".join(value[-width:] for value, width in zip(fields, widths))
    return f"{code:<9}{('KR' + code):<12}{name}{fixed}"


def archive_response(
    market: str,
    rows: list[str],
    last_modified: str = LAST_MODIFIED,
) -> Mock:
    data = BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as zipped:
        zipped.writestr(f"{market}_code.mst", "\n".join(rows).encode("cp949"))
    result = Mock()
    result.raise_for_status.return_value = None
    result.content = data.getvalue()
    result.headers = {"Last-Modified": last_modified}
    return result


def market_rows(
    prefix: str,
    count: int,
    code_offset: int,
    widths: tuple[int, ...],
) -> list[str]:
    return [
        master_line(
            f"{code_offset + index:06d}",
            f"{prefix}{index}",
            1_000_000 - index,
            widths,
        )
        for index in range(1, count + 1)
    ]


def complete_responses() -> list[Mock]:
    kospi = market_rows("Kospi", 220, 0, KOSPI_FIELD_WIDTHS)
    kospi.append(master_line("900001", "KospiFund", 9_999_999, KOSPI_FIELD_WIDTHS, "EF"))
    kosdaq = market_rows("Kosdaq", 170, 300_000, KOSDAQ_FIELD_WIDTHS)
    return [archive_response("kospi", kospi), archive_response("kosdaq", kosdaq)]


class KisUniverseTests(unittest.TestCase):
    def test_collects_exact_market_sizes_from_official_kis_masters(self):
        get = Mock(side_effect=complete_responses())

        universe = get_stock_universe(
            "token",
            "app-key",
            "app-secret",
            "20260917",
            request_get=get,
        )

        self.assertEqual((len(universe.kospi), len(universe.kosdaq)), (200, 150))
        self.assertEqual(len(universe.all_symbols), 350)
        self.assertNotIn("KospiFund", universe.kospi)
        self.assertEqual(universe.metadata["as_of"], "20260917")
        self.assertEqual(universe.metadata["source"], STOCK_UNIVERSE_SOURCE)
        self.assertEqual(
            universe.metadata["master_dates"],
            {"kospi": "20260917", "kosdaq": "20260917"},
        )
        self.assertEqual(get.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in get.call_args_list],
            [
                f"{KIS_MASTER_URL_BASE}/kospi_code.mst.zip",
                f"{KIS_MASTER_URL_BASE}/kosdaq_code.mst.zip",
            ],
        )
        for call in get.call_args_list:
            self.assertEqual(call.kwargs, {"timeout": 30})

    def test_market_cap_sort_is_independent_of_master_row_order(self):
        kospi = market_rows("Kospi", 200, 0, KOSPI_FIELD_WIDTHS)
        kospi.reverse()
        kosdaq = market_rows("Kosdaq", 150, 300_000, KOSDAQ_FIELD_WIDTHS)
        get = Mock(
            side_effect=[
                archive_response("kospi", kospi),
                archive_response("kosdaq", kosdaq),
            ]
        )

        universe = get_stock_universe(
            "token", "key", "secret", "20260917", request_get=get
        )

        self.assertEqual(list(universe.kospi)[:2], ["Kospi1", "Kospi2"])

    def test_stale_master_is_rejected(self):
        stale = "Wed, 16 Sep 2026 08:10:03 GMT"
        get = Mock(
            return_value=archive_response(
                "kospi",
                market_rows("Kospi", 200, 0, KOSPI_FIELD_WIDTHS),
                stale,
            )
        )
        with self.assertRaisesRegex(RuntimeError, r"목표일 자료가 아닙니다.*20260916/20260917"):
            get_stock_universe(
                "token", "key", "secret", "20260917", request_get=get
            )

    def test_incomplete_master_is_rejected_without_a_fallback_universe(self):
        get = Mock(
            return_value=archive_response(
                "kospi", market_rows("Kospi", 30, 0, KOSPI_FIELD_WIDTHS)
            )
        )
        with self.assertRaisesRegex(RuntimeError, "유효 주식이 부족합니다: 30/200"):
            get_stock_universe(
                "token", "key", "secret", "20260917", request_get=get
            )
        get.assert_called_once()

    def test_corrupt_or_invalid_master_is_rejected(self):
        cases = {
            "bad zip": b"not-a-zip",
            "missing member": None,
            "short row": "too-short",
            "bad code": master_line("BAD", "BadCode", 10, KOSPI_FIELD_WIDTHS),
        }
        for label, value in cases.items():
            with self.subTest(case=label):
                result = Mock()
                result.raise_for_status.return_value = None
                result.headers = {"Last-Modified": LAST_MODIFIED}
                if value is None:
                    data = BytesIO()
                    with ZipFile(data, "w") as zipped:
                        zipped.writestr("other.mst", b"data")
                    result.content = data.getvalue()
                elif isinstance(value, str):
                    result = archive_response("kospi", [value])
                else:
                    result.content = value
                with self.assertRaises((ValueError, RuntimeError)):
                    get_stock_universe(
                        "token",
                        "key",
                        "secret",
                        "20260917",
                        request_get=Mock(return_value=result),
                    )

    def test_search_lists_come_from_the_kis_scan_cache(self):
        payload = scan_cache()
        with patch.object(scanner, "load_scan_cache", return_value=payload):
            kospi, kosdaq, all_symbols = scanner.get_stock_lists()
        self.assertEqual((len(kospi), len(kosdaq), len(all_symbols)), (200, 150, 200))
        self.assertEqual(kospi, payload["markets"]["kospi200"]["symbols"])

    def test_old_universe_date_never_counts_as_complete(self):
        payload = scan_cache()
        payload["universe"] = {"as_of": "20260827", "age_sessions": 1}
        self.assertFalse(scanner.cache_has_target_date(payload, "20260828"))
        self.assertFalse(scanner.cache_has_target_date(payload, "20260828", allow_partial=True))


if __name__ == "__main__":
    unittest.main()
