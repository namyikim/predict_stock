# -*- coding: utf-8 -*-
"""R09 0단계 — 선행지수 판본 보관.

OECD 선행지수는 나중에 값이 바뀐다. 최신본 한 벌만 덮어쓰면 '그때 보이던 값'이 남지 않아
나중에 아무리 조심해도 개정을 미리 아는 백테스트밖에 할 수 없다. 그래서 오늘부터 쌓는다.
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import macro_utils as mu  # noqa: E402


def series(values, start="2026-01-01"):
    index = pd.date_range(start, periods=len(values), freq="MS")
    return pd.DataFrame({"month": index, "value": values})


class AppendTests(unittest.TestCase):
    def test_the_first_vintage_creates_the_table(self):
        text = mu.append_cli_vintage("", series([100.1, 100.2, 100.3]), "2026-04-15")
        frame = mu.read_cli_vintages(text)
        self.assertEqual(list(frame.columns), list(mu.CLI_VINTAGE_COLUMNS))
        self.assertEqual(set(frame["vintage_date"]), {"2026-04-15"})
        self.assertEqual(list(frame["month"]), ["2026-01", "2026-02", "2026-03"])

    def test_a_revision_is_kept_as_a_separate_vintage(self):
        """개정이 일어난 달을 덮어쓰면 안 된다. 두 판본이 나란히 남아야 비교할 수 있다."""
        first = mu.append_cli_vintage("", series([100.1, 100.2, 100.3]), "2026-04-15")
        second = mu.append_cli_vintage(first, series([100.1, 100.2, 100.9]), "2026-05-15")
        frame = mu.read_cli_vintages(second)
        self.assertEqual(sorted(set(frame["vintage_date"])), ["2026-04-15", "2026-05-15"])
        march = frame[frame["month"] == "2026-03"].set_index("vintage_date")["value"]
        self.assertAlmostEqual(march["2026-04-15"], 100.3)
        self.assertAlmostEqual(march["2026-05-15"], 100.9)

    def test_the_same_day_is_not_added_twice(self):
        first = mu.append_cli_vintage("", series([100.1, 100.2]), "2026-04-15")
        self.assertIsNone(mu.append_cli_vintage(first, series([100.1, 100.9]), "2026-04-15"))

    def test_an_unchanged_series_does_not_create_a_new_vintage(self):
        """값이 그대로인데 판본만 늘리면 표가 매일 불어난다."""
        first = mu.append_cli_vintage("", series([100.1, 100.2]), "2026-04-15")
        self.assertIsNone(mu.append_cli_vintage(first, series([100.1, 100.2]), "2026-04-16"))

    def test_only_the_tail_is_kept(self):
        """개정은 끝자락에서 일어난다. 오래된 달은 같은 값을 해마다 다시 적을 뿐이다."""
        text = mu.append_cli_vintage("", series([100.0] * 40, start="2023-01-01"), "2026-04-15",
                                     months=6)
        frame = mu.read_cli_vintages(text)
        self.assertEqual(len(frame), 6)
        self.assertEqual(frame["month"].max(), "2026-04")

    def test_a_broken_file_is_treated_as_empty_rather_than_crashing(self):
        """판본 표가 깨졌다고 보고서를 멈추면 안 된다. 새로 시작한다."""
        self.assertTrue(mu.read_cli_vintages("이건 CSV 가 아닙니다").empty)
        self.assertTrue(mu.read_cli_vintages("a,b\n1,2\n").empty)
        text = mu.append_cli_vintage("a,b\n1,2\n", series([100.1]), "2026-04-15")
        self.assertEqual(len(mu.read_cli_vintages(text)), 1)

    def test_an_empty_series_adds_nothing(self):
        self.assertIsNone(mu.append_cli_vintage("", series([]), "2026-04-15"))


class WiringTests(unittest.TestCase):
    def test_the_earnings_job_saves_a_vintage_when_the_index_is_fresh(self):
        source = (ROOT / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        self.assertIn("append_cli_vintage(", source)
        self.assertIn("CLI_VINTAGE_PATH", source)
        # 보관 실패가 보고서를 멈추면 안 된다.
        block = source[source.index("append_cli_vintage("):]
        self.assertIn("except Exception", block[:800])

    def test_the_vintage_file_lives_next_to_the_other_archives(self):
        self.assertTrue(mu.CLI_VINTAGE_PATH.startswith("macro_history/"))
        self.assertTrue(mu.CLI_VINTAGE_PATH.endswith(".csv"))


if __name__ == "__main__":
    unittest.main()
