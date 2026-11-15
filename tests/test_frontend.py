import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SavingsModalFrontendTests(unittest.TestCase):
    def setUp(self):
        source = (ROOT / "static" / "app.js").read_text()
        self.modal_source = source.split("function openSaveModal", 1)[1].split(
            "function openBondModal", 1
        )[0]
        self.styles = (ROOT / "static" / "style.css").read_text()

    def test_savings_amount_inputs_share_the_same_style(self):
        inputs = re.findall(r'<input class="([^"]+)" type="number"', self.modal_source)

        self.assertEqual(2, len(inputs))
        self.assertTrue(all("savings-amt" in classes.split() for classes in inputs))
        self.assertIn(".savings-amt", self.styles)

    def test_enter_dismissal_is_wired_to_savings_number_inputs(self):
        self.assertIn("wireEnterToBlur(card);", self.modal_source)


class MoneyChartFrontendTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "static" / "app.js").read_text()
        self.styles = (ROOT / "static" / "style.css").read_text()
        self.card_source = self.source.split("function kidCard", 1)[1].split(
            "function untilLabel", 1
        )[0]

    def test_graph_offers_longer_future_ranges(self):
        self.assertIn('["3m", "3M", 91]', self.source)
        self.assertIn('["1y", "1Y", 365]', self.source)
        self.assertIn('["5y", "5Y", 1825]', self.source)
        self.assertIn('data-future-range="${key}"', self.source)

    def test_graph_remembers_and_uses_the_future_range(self):
        self.assertIn('localStorage.getItem("bod_future_range")', self.source)
        self.assertIn("project(cur, now, futureDays, k.bonds || [])", self.source)
        self.assertIn('localStorage.setItem("bod_future_range", chartFutureRange)', self.source)

    def test_large_future_values_use_short_axis_labels(self):
        self.assertIn("function chartDollarLabel", self.source)
        self.assertIn("chartDollarLabel(v)", self.source)

    def test_past_label_is_hidden_when_there_is_no_past_line(self):
        self.assertIn('const pastLabel = past.length > 1', self.source)

    def test_dashboard_has_expand_button_without_future_controls(self):
        self.assertIn('class="btn-expand"', self.card_source)
        self.assertNotIn("FUTURE_RANGES.map", self.card_source)

    def test_dashboard_card_shares_the_future_range_and_defaults_to_one_year(self):
        self.assertIn('let chartFutureRange = "1y";', self.source)
        self.assertNotIn('expanded ? chartFutureRange : "3m"', self.source)
        self.assertIn('$(".future-cycle", el)', self.card_source)

    def test_invest_projection_climbs_in_relock_stairs(self):
        project = self.source.split("function project(", 1)[1].split("function cleanHistory", 1)[0]
        self.assertNotIn("investR", project)
        self.assertIn("relockValue(", project)

    def test_dashboard_cards_share_one_dollar_scale(self):
        self.assertIn("let CARD_SCALE = null;", self.source)
        self.assertIn("(!expanded && CARD_SCALE) || range", self.source)

    def test_tapping_a_chart_shows_its_numbers(self):
        self.assertIn('<g class="chart-tip"></g>', self.source)
        self.assertIn("wireChartTips(wrap);", self.source)
        self.assertIn("wireChartTips(card);", self.source)

    def test_expanded_graph_modal_contains_future_controls(self):
        self.assertIn("function openGraphModal", self.source)
        self.assertIn("moneyChart(k, true)", self.source)
        self.assertIn('class="graph-future-controls"', self.source)
        self.assertIn(".graph-modal-card", self.styles)

    def test_expanded_graph_uses_nearly_the_full_screen_width(self):
        self.assertIn(".panel-card.graph-modal-card", self.styles)
        self.assertIn("width: calc(100vw - 32px)", self.styles)
        self.assertIn("expanded && window.innerWidth > 600", self.source)


if __name__ == "__main__":
    unittest.main()
