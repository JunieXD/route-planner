#!/usr/bin/env python3
"""Query Shanghai Metro's official public station and route backends."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

STATION_URL = "https://m.shmetro.com/core/shmetro/mdstationinfoback_new.ashx"
ROUTE_URL = "https://m.shmetro.com/interface/plantrip/pt.aspx"
USER_AGENT = "Mozilla/5.0 Shanghai-Metro-Route-Planner/1.0"
CHINA_TZ = timezone(timedelta(hours=8))


class UpstreamError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def fetch_text(url: str, timeout: float, retries: int) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://service.shmetro.com/cphc/index.htm",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return raw.decode(
                    response.headers.get_content_charset() or "utf-8", errors="replace"
                )
        except HTTPError as error:
            last_error = error
            if error.code != 429 and error.code < 500:
                break
        except (URLError, TimeoutError) as error:
            last_error = error
        if attempt < retries:
            time.sleep(0.35 * (2**attempt))
    raise UpstreamError(f"request failed for {url}: {last_error}")


def fetch_json(url: str, timeout: float, retries: int) -> dict[str, Any]:
    text = fetch_text(url, timeout, retries)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise UpstreamError(
            f"route backend did not return JSON: {text[:160]}"
        ) from error
    if not isinstance(payload, dict):
        raise UpstreamError("route backend JSON root is not an object")
    return payload


def search_station(name: str, timeout: float, retries: int) -> list[dict[str, str]]:
    url = f"{STATION_URL}?{urlencode({'act': 'ssdiv', 'cv': name})}"
    html = fetch_text(url, timeout, retries)
    matches = re.findall(
        r'<a[^>]+id="(\d{4})"[^>]+title="([^"]+)"[^>]*>[\s\S]*?'
        r'<span class="st-name">([^<]+)</span>',
        html,
        re.I,
    )
    stations: list[dict[str, str]] = []
    for station_id, title, station_name in matches:
        line_match = re.match(r"(.+?)的", unescape(title))
        stations.append(
            {
                "station_id": station_id,
                "station_name": unescape(station_name).strip(),
                "line": line_match.group(1)
                if line_match
                else station_id[:2].lstrip("0"),
            }
        )
    return stations


def weekday_for(value: str | None) -> int:
    target = (
        date.today() if value is None else datetime.strptime(value, "%Y-%m-%d").date()
    )
    python_weekday = target.weekday()
    return 0 if python_weekday == 6 else python_weekday + 1


def hhmm(value: str) -> str:
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid HH:MM time: {value}") from error
    return value


def int_or_none(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def float_or_none(value: Any) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def normalize_path(
    payload: dict[str, Any],
    path: dict[str, Any],
    origin_id: str,
    destination_id: str,
    queried_at: str,
) -> dict[str, Any]:
    transfers = (
        path.get("transferStationList")
        if isinstance(path.get("transferStationList"), list)
        else []
    )
    pass_stations = (
        path.get("passStationList")
        if isinstance(path.get("passStationList"), list)
        else []
    )
    expected = int_or_none(path.get("impedancevalue"))
    in_vehicle = int_or_none(path.get("time")) or int_or_none(
        path.get("beginTravelTime")
    )
    if expected is None:
        expected = in_vehicle
    return {
        "from_station": payload.get("startStName"),
        "from_station_id": origin_id,
        "to_station": payload.get("endStName"),
        "to_station_id": destination_id,
        "duration_minutes": expected,
        "in_vehicle_minutes": in_vehicle,
        "fare_cny_per_person": float_or_none(path.get("price")),
        "transfer_count": int_or_none(path.get("passLineCount")) or 0,
        "station_count": int_or_none(path.get("stationNum")),
        "lines": [
            str(item.get("line"))
            for item in transfers
            if isinstance(item, dict) and item.get("line") is not None
        ],
        "directions": [
            {
                "line": str(item.get("line")),
                "at": item.get("stationName"),
                "toward": item.get("transferStationDirection"),
                "transfer_minutes": int_or_none(item.get("transferStationTime")),
            }
            for item in transfers
            if isinstance(item, dict)
        ],
        "stations": [
            item.get("stationName") for item in pass_stations if isinstance(item, dict)
        ],
        "last_boarding_time": path.get("lastBoardingTime"),
        "last_arrival_time": path.get("lastArrivalTime"),
        "first_service_verified": False,
        "last_service_verified": bool(path.get("lastBoardingTime")),
        "service_window_verified": False,
        "source": "shanghai-metro-official",
        "queried_at": queried_at,
    }


def route_query(
    origin_id: str,
    destination_id: str,
    *,
    plan_time: str,
    weekday: int,
    ticket: str,
    timeout: float,
    retries: int,
) -> list[dict[str, Any]]:
    params = {
        "func": "plantrip",
        "startId": origin_id,
        "endId": destination_id,
        "planTime": plan_time,
        "week": str(weekday),
        "ticket": ticket,
        "type": "1",
    }
    payload = fetch_json(f"{ROUTE_URL}?{urlencode(params)}", timeout, retries)
    paths = payload.get("pathList") if isinstance(payload.get("pathList"), list) else []
    queried_at = now_iso()
    return [
        normalize_path(payload, path, origin_id, destination_id, queried_at)
        for path in paths
        if isinstance(path, dict)
    ]


def command_station(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "query": args.name,
        "source": "shanghai-metro-official",
        "queried_at": now_iso(),
        "matches": search_station(args.name, args.timeout, args.retries),
    }


def command_route(args: argparse.Namespace) -> dict[str, Any]:
    origins = search_station(args.from_name, args.timeout, args.retries)
    destinations = search_station(args.to_name, args.timeout, args.retries)
    if args.from_id:
        origins = [item for item in origins if item["station_id"] == args.from_id]
    if args.to_id:
        destinations = [
            item for item in destinations if item["station_id"] == args.to_id
        ]
    if not origins:
        raise ValueError(f"Shanghai Metro station not found: {args.from_name}")
    if not destinations:
        raise ValueError(f"Shanghai Metro station not found: {args.to_name}")

    weekday = weekday_for(args.date)
    collected: list[dict[str, Any]] = []
    for origin in origins:
        for destination in destinations:
            if origin["station_id"] == destination["station_id"]:
                continue
            collected.extend(
                route_query(
                    origin["station_id"],
                    destination["station_id"],
                    plan_time=args.time,
                    weekday=weekday,
                    ticket=args.ticket,
                    timeout=args.timeout,
                    retries=args.retries,
                )
            )
            time.sleep(0.12)

    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in collected:
        key = (
            tuple(item["lines"]),
            tuple(item["stations"]),
            item["duration_minutes"],
            item["fare_cny_per_person"],
        )
        deduped.setdefault(key, item)
    routes = list(deduped.values())
    routes.sort(
        key=lambda item: (
            item["duration_minutes"] is None,
            item["duration_minutes"] or 10**9,
            item["transfer_count"],
            item["fare_cny_per_person"] is None,
            item["fare_cny_per_person"] or 10**9,
        )
    )
    if args.limit:
        routes = routes[: args.limit]
    warnings: list[str] = []
    hour, minute = map(int, args.time.split(":"))
    requested_minutes = hour * 60 + minute
    if requested_minutes < 360:
        warnings.append("官方路线数据无法确认首班车时间，清晨换乘仍需另行核对")
    elif requested_minutes >= 1350:
        warnings.append("所选时间接近末班车，应逐段核对各线路的最晚上车时间")
    return {
        "query": {
            "from": args.from_name,
            "to": args.to_name,
            "date": args.date,
            "weekday": weekday,
            "time": args.time,
            "ticket": args.ticket,
        },
        "source": "shanghai-metro-official",
        "queried_at": now_iso(),
        "warnings": warnings,
        "count": len(routes),
        "routes": routes,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=2)
    sub = parser.add_subparsers(dest="command", required=True)

    station = sub.add_parser("station", help="Resolve a Shanghai Metro station")
    station.add_argument("--name", required=True)

    route = sub.add_parser("route", help="Query station-to-station route and fare")
    route.add_argument("--from", dest="from_name", required=True)
    route.add_argument("--to", dest="to_name", required=True)
    route.add_argument("--from-id")
    route.add_argument("--to-id")
    route.add_argument("--date")
    route.add_argument("--time", type=hhmm, default="09:00")
    route.add_argument("--ticket", choices=["oneCard", "oneWay"], default="oneCard")
    route.add_argument("--limit", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.timeout <= 0:
            raise ValueError("--timeout must be positive")
        if args.retries < 0:
            raise ValueError("--retries must be non-negative")
        if hasattr(args, "limit") and args.limit < 0:
            raise ValueError("--limit must be non-negative")
        result = (
            command_station(args) if args.command == "station" else command_route(args)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {"error": str(error), "type": type(error).__name__}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
