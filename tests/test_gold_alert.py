from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gold_alert as bot

HTML = (Path(__file__).parent / "fixtures/current-layout.html").read_text(encoding="utf-8")


class ExtractionTests(unittest.TestCase):
    def test_current_layout_ignores_ticker_history_other_purities_and_weights(self):
        self.assertEqual(bot.extract_price(HTML), D("13535"))

    def test_reordered_columns(self):
        html = HTML.replace("<th>24K</th><th>22K</th>", "<th>22K</th><th>24K</th>")
        self.assertEqual(bot.extract_price(html), D("14766"))

    def test_block_page_and_wrong_city_rejected(self):
        for html in ("<html>Cloudflare: blocked</html>", HTML.replace("Hyderabad", "Mumbai")):
            with self.subTest(html=html[:50]), self.assertRaises(ValueError):
                bot.extract_price(html)

    def test_ambiguous_missing_and_malformed_prices_rejected(self):
        for html in (HTML + HTML, HTML.replace("<th>22K</th>", "<th>Unknown</th>"),
                     HTML.replace("₹13,535</span>", "N/A</span>"),
                     HTML.replace("<td>1</td>", "<td>2</td>")):
            with self.subTest(html=html[:50]), self.assertRaises(ValueError):
                bot.extract_price(html)


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.json"
        self.sent = []

    def check(self, price, threshold="13000", context="chat-A"):
        return bot.run(D(price), D(threshold), self.path, context, self.sent.append)

    def test_strict_boundary_first_run_and_duplicates_across_state_reload(self):
        self.assertFalse(self.check("13000"))
        self.assertFalse(self.check("13001"))
        self.assertTrue(self.check("12999.99"))
        self.assertFalse(self.check("12999.99"))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(bot.load_state(self.path)["last_price"], "12999.99")

    def test_initial_below_and_changes_below(self):
        self.assertTrue(self.check("12900"))
        self.assertTrue(self.check("12800"))
        self.assertTrue(self.check("12900"))
        self.assertEqual(len(self.sent), 3)

    def test_rearms_after_equal_or_above(self):
        self.check("12900")
        self.assertFalse(self.check("13000"))
        self.assertTrue(self.check("12900"))

    def test_threshold_or_recipient_change(self):
        self.check("12900")
        # The CLI hashes the threshold and recipient into context.
        self.assertTrue(self.check("12900", "14000", "new-threshold"))
        self.assertTrue(self.check("12900", "14000", "new-recipient"))

    def test_send_failure_preserves_previous_state_then_retries_next_run(self):
        self.check("14000")
        before = self.path.read_bytes()
        def fail(_):
            raise RuntimeError("Telegram unavailable")
        with self.assertRaises(RuntimeError):
            bot.run(D("12900"), D("13000"), self.path, "chat-A", fail)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.check("12900"))

    def test_corrupt_state_does_not_send(self):
        self.path.write_text('{"version": 8}')
        with self.assertRaises(ValueError):
            self.check("12900")
        self.assertEqual(self.sent, [])

    def test_bad_thresholds(self):
        for value in ("NaN", "Infinity", "0", "-1", "oops"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bot.money(value)

    def test_telegram_requires_success_without_exposing_token(self):
        class Reply:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return b'{"ok":false,"description":"failure"}'
        with patch.object(bot, "urlopen", return_value=Reply()):
            with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                bot.send_telegram("secret-token", "123", "test")


if __name__ == "__main__":
    unittest.main()
