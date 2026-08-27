from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import amap_transit  # noqa: E402
import rank_routes  # noqa: E402


class AMapNormalizationTests(unittest.TestCase):
    def test_empty_railway_placeholders_are_ignored(self) -> None:
        raw = {
            "duration": "1260",
            "cost": "3.0",
            "segments": [
                {
                    "walking": {
                        "duration": "60",
                        "distance": "80",
                        "steps": [{"instruction": "步行80米0x00到达车站"}],
                    },
                    "bus": {
                        "buslines": [
                            {
                                "name": "地铁1号线",
                                "type": "地铁",
                                "duration": "660",
                                "departure_stop": {"name": "火车东站"},
                                "arrival_stop": {"name": "西湖文化广场"},
                            }
                        ]
                    },
                    "railway": {"via_stops": []},
                },
                {"railway": {"via_stops": []}},
            ],
        }

        route = amap_transit.normalize_transit(raw, 1)

        self.assertEqual(route["vehicle_boardings"], 1)
        self.assertEqual(route["transfer_count"], 0)
        self.assertEqual([item["mode"] for item in route["segments"]], ["walk", "metro"])
        self.assertEqual(route["segments"][0]["instructions"], ["步行80米到达车站"])


class RankingTests(unittest.TestCase):
    def test_unknown_price_is_not_ranked_as_cheapest(self) -> None:
        routes = [
            {
                "id": "fast-unpriced",
                "duration_minutes": 40,
                "price_cny_per_person": None,
                "transfer_count": 1,
                "risk_score": 0.1,
            },
            {
                "id": "priced",
                "duration_minutes": 50,
                "price_cny_per_person": 20.0,
                "transfer_count": 0,
                "risk_score": 0.05,
            },
        ]
        rank_routes.add_balanced_scores(routes)
        frontier = rank_routes.pareto_ids(routes)
        routes.sort(key=lambda item: rank_routes.sort_key(item, "cheapest"))
        rank_routes.label_routes(routes, frontier, "cheapest")

        self.assertEqual(routes[0]["id"], "priced")
        self.assertIn("cheapest", routes[0]["ranking"]["labels"])
        self.assertNotIn("cheapest", routes[1]["ranking"]["labels"])


if __name__ == "__main__":
    unittest.main()
