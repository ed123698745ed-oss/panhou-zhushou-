import copy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import unittest
from fetch_market_data import (
    describe_oi_change,
    parse_foreign_tx_oi,
    parse_mis,
    parse_mis_turnover,
    parse_trading_dates,
    parse_turnover,
    validate_spot,
)

FIXTURES = Path(__file__).parent / 'fixtures'
TARGET = date(2026, 9, 10)

class MarketDataTests(unittest.TestCase):
    def setUp(self):
        self.mis = json.loads((FIXTURES / 'mis.json').read_text())
        self.mis_statis = json.loads((FIXTURES / 'mis-statis.json').read_text())
        self.turnover = json.loads((FIXTURES / 'fmtqik.json').read_text())
        self.taifex = (FIXTURES / 'taifex-latest.html').read_text()

    def test_captured_official_reports(self):
        index, change, percent = parse_mis(self.mis, TARGET)
        self.assertEqual(index, Decimal('46940.49'))
        self.assertEqual(change, Decimal('-242.87'))
        self.assertEqual(f'{percent:.2f}', '-0.51')
        self.assertIsNone(validate_spot(index, change, parse_turnover(self.turnover, TARGET)))
        self.assertEqual(parse_mis_turnover(self.mis_statis, TARGET), Decimal('7139.7228356'))
        self.assertEqual(parse_foreign_tx_oi(self.taifex, TARGET), -83918)

    def test_previous_trading_day_and_oi_change(self):
        self.assertEqual(max(day for day in parse_trading_dates(self.turnover) if day < TARGET),
                         date(2026, 9, 9))
        previous_html = (self.taifex
                         .replace('日期2026/09/10', '日期2026/09/09')
                         .replace('8,264', '8,123', 1)
                         .replace('92,182', '89,189', 1)
                         .replace('-83,918', '-81,066', 1))
        previous_oi = parse_foreign_tx_oi(previous_html, date(2026, 9, 9))
        difference = -83918 - previous_oi
        self.assertEqual(previous_oi, -81066)
        self.assertEqual(difference, -2852)
        self.assertEqual(describe_oi_change(difference), '向淨空方向增加 2,852 口')

    def test_stale_mis_turnover_rejected(self):
        self.mis_statis['detail']['key'] = 'tse_20260909'
        with self.assertRaises(RuntimeError):
            parse_mis_turnover(self.mis_statis, TARGET)

    def test_intraday_mis_turnover_rejected(self):
        self.mis_statis['detail']['%'] = '13:29:59'
        with self.assertRaises(RuntimeError):
            parse_mis_turnover(self.mis_statis, TARGET)

    def test_stale_mis_rejected(self):
        self.mis['msgArray'][0]['d'] = '20260909'
        with self.assertRaises(RuntimeError): parse_mis(self.mis, TARGET)

    def test_intraday_rejected(self):
        self.mis['msgArray'][0]['t'] = '13:29:55'
        with self.assertRaises(RuntimeError): parse_mis(self.mis, TARGET)

    def test_missing_or_invalid_price_rejected(self):
        for price in ['-', 'NaN', 'Infinity', '0']:
            self.mis['msgArray'][0]['z'] = price
            with self.assertRaises((RuntimeError, ValueError)): parse_mis(self.mis, TARGET)

    def test_request_date_does_not_prove_row_date(self):
        self.turnover['data'] = self.turnover['data'][:-1]
        with self.assertRaises(RuntimeError): parse_turnover(self.turnover, TARGET)

    def test_unsorted_rows(self):
        self.turnover['data'].reverse()
        self.assertEqual(parse_turnover(self.turnover, TARGET)[0], Decimal('7507.42811639'))

    def test_duplicate_rows_rejected(self):
        self.turnover['data'].append(copy.deepcopy(self.turnover['data'][-1]))
        with self.assertRaises(RuntimeError): parse_turnover(self.turnover, TARGET)

    def test_stale_futures_rejected(self):
        with self.assertRaises(RuntimeError): parse_foreign_tx_oi(self.taifex.replace('日期2026/09/10', '日期2026/09/09'), TARGET)

    def test_wrong_contract_rejected(self):
        with self.assertRaises(RuntimeError): parse_foreign_tx_oi(self.taifex.replace('臺股期貨', '小型臺指期貨'), TARGET)

    def test_missing_tx_foreign_row_does_not_use_next_product(self):
        with self.assertRaises(RuntimeError): parse_foreign_tx_oi(self.taifex.replace('外資', '未提供', 1), TARGET)

    def test_inconsistent_futures_rejected(self):
        with self.assertRaises(RuntimeError): parse_foreign_tx_oi(self.taifex.replace('-83,918', '-83,917', 1), TARGET)

    def test_spot_mismatch_rejected(self):
        with self.assertRaises(RuntimeError): validate_spot(Decimal('47183.36'), Decimal('77.58'), parse_turnover(self.turnover, TARGET))

    def test_holiday_rejected(self):
        with self.assertRaises(RuntimeError): parse_turnover(self.turnover, date(2026, 9, 6))

if __name__ == '__main__':
    unittest.main()
