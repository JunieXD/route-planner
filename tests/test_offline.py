from __future__ import annotations

import argparse
import contextlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import amap_transit  # noqa: E402
import rail_12306  # noqa: E402
import rank_routes  # noqa: E402
import shanghai_metro  # noqa: E402


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
        self.assertEqual(
            [item["mode"] for item in route["segments"]], ["walk", "metro"]
        )
        self.assertEqual(route["segments"][0]["instructions"], ["步行80米到达车站"])

    def test_numbered_bus_name_overrides_misleading_type(self) -> None:
        self.assertEqual(
            amap_transit.bus_mode({"name": "闵行11路", "type": "地铁线路"}), "bus"
        )
        self.assertEqual(
            amap_transit.bus_mode({"name": "地铁11号线", "type": "轨道交通"}), "metro"
        )

    def test_district_constraint_selects_matching_candidate(self) -> None:
        candidates = [
            {
                "formatted_address": "上海市闵行区某大学地铁站",
                "province": "上海市",
                "city": "上海市",
                "district": "闵行区",
                "location": "121.1,31.1",
            },
            {
                "formatted_address": "上海市普陀区某大学校区",
                "province": "上海市",
                "city": "上海市",
                "district": "普陀区",
                "location": "121.2,31.2",
            },
        ]

        selected = amap_transit.select_geocode_candidate(
            candidates,
            address="某大学校区",
            expected_city="上海",
            expected_district="普陀",
        )

        self.assertEqual(selected["district"], "普陀区")

    def test_autonomous_region_short_name_matches_official_name(self) -> None:
        self.assertTrue(
            amap_transit.area_matches("广西壮族自治区", "广西", "广西壮族自治区南宁市")
        )

    def test_district_conflict_never_silently_accepts_first_candidate(self) -> None:
        with self.assertRaises(amap_transit.LocationMismatchError):
            amap_transit.select_geocode_candidate(
                [
                    {
                        "formatted_address": "甲市乙区某地点",
                        "province": "甲省",
                        "city": "甲市",
                        "district": "乙区",
                        "location": "120,30",
                    }
                ],
                address="某地点",
                expected_city="甲市",
                expected_district="丙区",
            )

    def test_specific_place_rejects_district_centroid_fallback(self) -> None:
        with self.assertRaises(amap_transit.LocationMismatchError):
            amap_transit.select_geocode_candidate(
                [
                    {
                        "formatted_address": "甲市乙区",
                        "province": "甲省",
                        "city": "甲市",
                        "district": "乙区",
                        "level": "区县",
                        "location": "120,30",
                    }
                ],
                address="某大学校区",
                expected_city="甲市",
                expected_district="乙区",
            )

    def test_walking_path_is_normalized(self) -> None:
        path = amap_transit.normalize_walking_path(
            {
                "duration": "1080",
                "distance": "1335",
                "steps": [{"instruction": "向东步行0x00", "distance": "100"}],
            },
            1,
        )

        self.assertEqual(path["duration_minutes"], 18)
        self.assertEqual(path["distance_meters"], 1335)
        self.assertEqual(path["steps"][0]["instruction"], "向东步行")

    def test_qps_error_is_retried_without_exposing_key(self) -> None:
        class Headers:
            @staticmethod
            def get_content_charset() -> str:
                return "utf-8"

        class Response:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload
                self.headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        responses = [
            Response(
                {
                    "status": "0",
                    "infocode": "10021",
                    "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT",
                }
            ),
            Response({"status": "1", "geocodes": []}),
        ]
        with (
            mock.patch.object(amap_transit, "urlopen", side_effect=responses) as opener,
            mock.patch.object(
                amap_transit,
                "request_slot",
                side_effect=lambda: contextlib.nullcontext(),
            ),
            mock.patch.object(amap_transit.time, "sleep"),
        ):
            result = amap_transit.fetch_json(
                amap_transit.GEOCODE_URL,
                {"address": "测试"},
                key="secret-that-must-not-be-logged",
                label="geocoding",
                timeout=1,
                retries=1,
            )

        self.assertEqual(result["status"], "1")
        self.assertEqual(opener.call_count, 2)


class RailTransferTests(unittest.TestCase):
    @staticmethod
    def station(code: str, name: str, city: str) -> rail_12306.Station:
        return rail_12306.Station(
            code=code, name=name, city=city, pinyin=name, short=name
        )

    @staticmethod
    def leg(
        train: str,
        from_code: str,
        to_code: str,
        departure: str,
        arrival: str,
        price: float = 50,
        seat: str = "硬座",
    ) -> dict[str, object]:
        duration = int(
            (
                rail_12306.datetime.fromisoformat(arrival)
                - rail_12306.datetime.fromisoformat(departure)
            ).total_seconds()
            // 60
        )
        return {
            "train_code": train,
            "train_no": train,
            "from_station": from_code,
            "from_station_code": from_code,
            "to_station": to_code,
            "to_station_code": to_code,
            "departure_at": departure,
            "arrival_at": arrival,
            "duration_minutes": duration,
            "seats": [
                {
                    "name": seat,
                    "price_cny": price,
                    "status": "available",
                    "inventory_confirmed": True,
                }
            ],
            "selected_seat": {
                "name": seat,
                "price_cny": price,
                "status": "available",
                "inventory_confirmed": True,
            },
        }

    def test_cross_day_same_station_transfer_is_feasible(self) -> None:
        stations = {
            "AAA": self.station("AAA", "甲站", "甲市"),
            "HHH": self.station("HHH", "枢纽站", "枢纽市"),
            "DDD": self.station("DDD", "丁站", "丁市"),
        }
        graph = [
            self.leg(
                "K1", "AAA", "HHH", "2026-09-10T20:00+08:00", "2026-09-10T23:50+08:00"
            ),
            self.leg(
                "K2", "HHH", "DDD", "2026-09-11T00:40+08:00", "2026-09-11T06:00+08:00"
            ),
        ]

        routes = rail_12306.search_transfer_graph(
            graph,
            origin_codes={"AAA"},
            destination_codes={"DDD"},
            station_by_code=stations,
            max_transfers=1,
            allow_cross_station=False,
            same_station_min=30,
            cross_station_min=100,
            max_wait=720,
        )

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["transfers"][0]["wait_minutes"], 50)
        self.assertEqual(routes[0]["overnight_class"], "seated")

    def test_early_morning_leg_counts_as_overnight(self) -> None:
        self.assertTrue(
            rail_12306.is_overnight("2026-09-11T00:40+08:00", "2026-09-11T05:30+08:00")
        )

    def test_cross_station_transfer_requires_same_city_and_explicit_permission(
        self,
    ) -> None:
        stations = {
            "A": self.station("A", "甲", "甲市"),
            "H1": self.station("H1", "枢纽东", "枢纽市"),
            "H2": self.station("H2", "枢纽西", "枢纽市"),
            "D": self.station("D", "丁", "丁市"),
        }
        first = self.leg(
            "G1", "A", "H1", "2026-09-10T08:00+08:00", "2026-09-10T10:00+08:00"
        )
        second = self.leg(
            "G2", "H2", "D", "2026-09-10T12:00+08:00", "2026-09-10T14:00+08:00"
        )

        denied = rail_12306.transfer_between(
            first,
            second,
            station_by_code=stations,
            allow_cross_station=False,
            same_station_min=30,
            cross_station_min=100,
            max_wait=720,
        )
        allowed = rail_12306.transfer_between(
            first,
            second,
            station_by_code=stations,
            allow_cross_station=True,
            same_station_min=30,
            cross_station_min=100,
            max_wait=720,
        )

        self.assertIsNone(denied)
        self.assertEqual(allowed["kind"], "cross_station")
        self.assertEqual(allowed["slack_minutes"], 20)

    def test_seat_policies_separate_price_and_overnight_comfort(self) -> None:
        train = {
            "seats": [
                {"name": "硬座", "price_cny": 50, "status": "available"},
                {"name": "硬卧", "price_cny": 100, "status": "available"},
                {"name": "软卧", "price_cny": 180, "status": "sold_out"},
            ]
        }

        cheapest = rail_12306.select_seat(train, policy="cheapest-available")
        sleeper = rail_12306.select_seat(train, policy="sleeper-required")

        self.assertEqual(cheapest["name"], "硬座")
        self.assertEqual(sleeper["name"], "硬卧")

        daytime_train = {
            **train,
            "departure_at": "2026-09-10T09:00+08:00",
            "arrival_at": "2026-09-10T12:00+08:00",
        }
        daytime_feeder = rail_12306.select_seat(
            daytime_train, policy="sleeper-required"
        )
        self.assertEqual(daytime_feeder["name"], "硬座")


class ShanghaiMetroTests(unittest.TestCase):
    def test_last_service_does_not_imply_full_service_window(self) -> None:
        route = shanghai_metro.normalize_path(
            {"startStName": "甲", "endStName": "乙"},
            {
                "time": "25",
                "price": "4",
                "lastBoardingTime": "22:45",
                "lastArrivalTime": "23:10",
                "transferStationList": [],
                "passStationList": [],
            },
            "0101",
            "0201",
            "2026-08-28T00:00:00+08:00",
        )

        self.assertTrue(route["last_service_verified"])
        self.assertFalse(route["first_service_verified"])
        self.assertFalse(route["service_window_verified"])


class RankingTests(unittest.TestCase):
    def test_unknown_price_is_not_ranked_as_cheapest(self) -> None:
        routes = [
            {
                "id": "fast-unpriced",
                "duration_minutes": 40,
                "price_cny_per_person": None,
                "price_complete": False,
                "time_complete": True,
                "unpriced_legs": ["接驳"],
                "unmodeled_legs": [],
                "transfer_count": 1,
                "risk_score": 0.1,
            },
            {
                "id": "priced",
                "duration_minutes": 50,
                "price_cny_per_person": 20.0,
                "price_complete": True,
                "time_complete": True,
                "unpriced_legs": [],
                "unmodeled_legs": [],
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

    def test_known_subtotal_is_not_labeled_as_complete_cheapest(self) -> None:
        routes = [
            {
                "id": "partial-five",
                "duration_minutes": 40,
                "price_cny_per_person": 5,
                "price_complete": False,
                "time_complete": True,
                "unpriced_legs": ["末段"],
                "unmodeled_legs": [],
                "transfer_count": 1,
                "risk_score": 0.2,
            },
            {
                "id": "complete-twenty",
                "duration_minutes": 50,
                "price_cny_per_person": 20,
                "price_complete": True,
                "time_complete": True,
                "unpriced_legs": [],
                "unmodeled_legs": [],
                "transfer_count": 1,
                "risk_score": 0.1,
            },
        ]
        rank_routes.add_balanced_scores(routes)
        frontier = rank_routes.pareto_ids(routes)
        routes.sort(key=lambda item: rank_routes.sort_key(item, "cheapest"))
        rank_routes.label_routes(routes, frontier, "cheapest")

        self.assertEqual(routes[0]["id"], "complete-twenty")
        self.assertIn("cheapest", routes[0]["ranking"]["labels"])
        self.assertNotIn("cheapest", routes[1]["ranking"]["labels"])

    def test_incomplete_price_cannot_satisfy_a_budget_filter(self) -> None:
        route = {
            "duration_minutes": 60,
            "transfer_count": 1,
            "price_cny_per_person": 10,
            "price_complete": False,
            "time_complete": True,
            "unpriced_legs": ["末段"],
            "unmodeled_legs": [],
        }
        args = argparse.Namespace(
            party_size=1,
            max_duration_minutes=None,
            max_transfers=None,
            max_price_per_person=20,
            max_total_price=None,
        )

        self.assertIn(
            "incomplete_price_for_budget", rank_routes.filter_reasons(route, args)
        )

    def test_partial_timeline_is_not_labeled_fastest(self) -> None:
        routes = [
            {
                "id": "partial-ten",
                "duration_minutes": 10,
                "price_cny_per_person": 5,
                "price_complete": True,
                "time_complete": False,
                "unpriced_legs": [],
                "unmodeled_legs": ["起点接驳"],
                "transfer_count": 0,
                "risk_score": 0.2,
            },
            {
                "id": "complete-twenty",
                "duration_minutes": 20,
                "price_cny_per_person": 8,
                "price_complete": True,
                "time_complete": True,
                "unpriced_legs": [],
                "unmodeled_legs": [],
                "transfer_count": 0,
                "risk_score": 0.1,
            },
        ]
        rank_routes.add_balanced_scores(routes)
        frontier = rank_routes.pareto_ids(routes)
        routes.sort(key=lambda item: rank_routes.sort_key(item, "fastest"))
        rank_routes.label_routes(routes, frontier, "fastest")

        self.assertEqual(routes[0]["id"], "complete-twenty")
        self.assertIn("fastest", routes[0]["ranking"]["labels"])
        self.assertNotIn("fastest", routes[1]["ranking"]["labels"])


if __name__ == "__main__":
    unittest.main()
