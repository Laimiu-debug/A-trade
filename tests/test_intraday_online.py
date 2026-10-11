import datetime as dt
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from service import intraday_online as online
from service.intraday import MissingIntraday


OBSERVED = dt.datetime(2026, 10, 11, tzinfo=dt.timezone.utc)


def payload(rows=None, **changes):
    data = {"code": "600900", "market": 1, "name": "长江电力",
            "trends": rows if rows is not None else [
                "2026-10-09 09:30,28.98,28.98,28.98,28.98,3301,9566298.00,28.980",
                "2026-10-09 15:00,28.97,28.99,28.99,28.97,11676,33847275.00,29.143"]}
    data.update(changes)
    return json.dumps({"rc": 0, "data": data}, ensure_ascii=False).encode()


class OnlineIntradayTests(unittest.TestCase):
    def config(self, symbol="sh600900", date="2026-10-09", observed=OBSERVED):
        return online.request_config(symbol, date, observed)

    def test_real_shape_units_date_and_average(self):
        data = online.parse_response(payload(), self.config())
        self.assertEqual(data["symbol"], "sh600900")
        self.assertEqual(data["name"], "长江电力")
        self.assertEqual(data["provider"], "eastmoney")
        self.assertEqual(data["volumeUnit"], "手")
        self.assertEqual(data["amountUnit"], "元")
        self.assertEqual(data["rows"][-1]["close"], 28.99)
        self.assertEqual(data["rows"][-1]["average"], 29.143)
        self.assertEqual(data["rows"][-1]["volume"], 11676)
        self.assertEqual(data["asOf"], "2026-10-09T15:00:00+08:00")
        self.assertFalse(data["cached"])
        self.assertEqual(data["availableDates"], ["2026-10-09"])

    def test_missing_date_keeps_actual_available_dates(self):
        with self.assertRaises(MissingIntraday) as error:
            online.parse_response(payload(), self.config(date="2026-09-30"))
        self.assertEqual(error.exception.available_dates, ["2026-10-09"])
        with self.assertRaises(MissingIntraday) as empty:
            online.parse_response(payload([]), self.config())
        self.assertEqual(empty.exception.available_dates, [])

    def test_older_close_only_rows_preserve_missing_ohlc_and_never_invent_bars(self):
        data = online.parse_response(payload([
            "2026-10-09 09:31,0,28.98,0,0,-,--,0"
        ]), self.config())
        row = data["rows"][0]
        self.assertEqual(row["close"], 28.98)
        for key in ("open", "high", "low", "volume", "amount", "average"):
            self.assertIsNone(row[key])
        self.assertTrue(any("缺失" in warning for warning in data["warnings"]))

    def test_completed_minute_policy_excludes_current_and_future(self):
        observed = dt.datetime(2026, 10, 9, 9, 32, 30, tzinfo=online.SHANGHAI)
        records = [f"2026-10-09 09:{n},10,10,10,10,100,100000,10" for n in (31, 32, 33)]
        data = online.parse_response(payload(records), self.config(observed=observed))
        self.assertEqual([row["time"] for row in data["rows"]], ["09:31"])
        self.assertTrue(any("尚未结束" in warning for warning in data["warnings"]))

    def test_explicit_exchange_identity_and_request_validation(self):
        self.assertEqual(self.config("sh000001")["secid"], "1.000001")
        self.assertEqual(self.config("sz000001")["secid"], "0.000001")
        for symbol in ("000001", "bj920001", "sh510300", "sh000999", "../../file"):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                self.config(symbol)
        for date in ("2026-10-12", "2026-02-30", "20261009", 20261009):
            with self.subTest(date=date), self.assertRaises(ValueError):
                self.config(date=date)
        with self.assertRaises(ValueError):
            self.config(observed=dt.datetime(2026, 10, 11))

    def test_explicit_latest_uses_actual_response_date_not_current_calendar_date(self):
        records = ["2026-10-08 15:00,10,10,10,10,100,100000,10",
                   "2026-10-09 09:31,11,11,11,11,100,110000,11"]
        with patch.object(online, "fetch_bytes", return_value=payload(records)):
            data = online.read_online_intraday("sh600900", observed_at=OBSERVED)
        self.assertEqual(data["date"], "2026-10-09")
        self.assertEqual(data["rows"][0]["close"], 11)
        self.assertEqual(data["availableDates"], ["2026-10-08", "2026-10-09"])

    def test_invalid_source_identity_status_shape_and_numbers_are_rejected(self):
        values = [payload(code="600000"), payload(market=0), payload(market=True),
                  payload(trends="not rows"), b"[]", b'{"rc":1,"data":{}}',
                  b'{"rc":0,"data":null}', b"invalid json"]
        for value in values:
            with self.subTest(value=value[:60]), self.assertRaisesRegex(ValueError, "格式"):
                online.parse_response(value, self.config())
        for row in ("2026-10-09 09:31,10,0,10,10,100,100000,10",
                    "2026-10-09 09:31,10,11,10,9,100,100000,10",
                    "2026-10-09 09:31,10,10,10,10,-1,100000,10",
                    "2026-10-09 09:31,10,10,10,10,100,NaN,10",
                    "2026-10-09 25:31,10,10,10,10,100,100000,10",
                    "2026-10-09 09:31,10,10,10,10,100,1e30,10"):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, "格式"):
                online.parse_response(payload([row]), self.config())

    def test_duplicate_and_response_bounds_rejected(self):
        row = "2026-10-09 09:31,10,10,10,10,100,100000,10"
        for rows in ([row, row], [row] * 1601):
            with self.assertRaises(ValueError):
                online.parse_response(payload(rows), self.config())
        with self.assertRaisesRegex(ValueError, "512 KiB"):
            online.parse_response(b" " * (online.MAX_BYTES + 1), self.config())

    def test_fetch_is_fixed_identity_bounded_and_network_errors_sanitized(self):
        class Response(io.BytesIO):
            status = 200
            headers = {}

            def geturl(self):
                return self.request.full_url

        seen = []

        def opener(request, timeout):
            seen.append((request.full_url, timeout))
            response = Response(payload())
            response.request = request
            return response

        with patch.object(online, "urlopen", side_effect=opener):
            data = online.read_online_intraday("sh600900", "2026-10-09", observed_at=OBSERVED)
        self.assertEqual(data["rows"][-1]["close"], 28.99)
        self.assertEqual(seen[0][1], 5)
        self.assertTrue(seen[0][0].startswith(online.URL + "?"))
        self.assertIn("secid=1.600900", seen[0][0])
        self.assertIn("ndays=5", seen[0][0])
        with patch.object(online, "urlopen", side_effect=URLError("private connection details")):
            with self.assertRaisesRegex(ValueError, "连接失败") as error:
                online.fetch_bytes("1.600900")
        self.assertNotIn("private", str(error.exception))
        with self.assertRaises(ValueError):
            online.fetch_bytes("https://other-host/")

    def test_network_stream_limits_redirect_and_deadline(self):
        class Response(io.BytesIO):
            status = 200
            headers = {}

            def geturl(self):
                return self.request.full_url

        def opener(body=b"{}", headers=None):
            def run(request, timeout):
                response = Response(body)
                response.request = request
                response.headers = headers or {}
                return response
            return run

        with patch.object(online, "urlopen", side_effect=opener(b" " * (online.MAX_BYTES + 1))):
            with self.assertRaisesRegex(ValueError, "512 KiB"):
                online.fetch_bytes("1.600900")
        with patch.object(online, "urlopen", side_effect=opener(headers={"Content-Length": "900000"})):
            with self.assertRaisesRegex(ValueError, "超过上限"):
                online.fetch_bytes("1.600900")
        with patch.object(online, "urlopen", side_effect=opener()):
            with patch.object(online.time, "monotonic", side_effect=[0, 16]):
                with self.assertRaisesRegex(ValueError, "超时"):
                    online.fetch_bytes("1.600900")
        with self.assertRaisesRegex(ValueError, "重定向"):
            online._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other-host/")


if __name__ == "__main__":
    unittest.main()
