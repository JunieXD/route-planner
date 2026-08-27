#!/usr/bin/env python3
"""Query AMap geocoding and public-transit REST APIs without a browser."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from amap_credentials import CredentialError, resolve_key


GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
TRANSIT_URL = "https://restapi.amap.com/v3/direction/transit/integrated"
USER_AGENT = "route-planner/1.0"
CHINA_TZ = timezone(timedelta(hours=8))


class ConfigError(RuntimeError):
    pass


class UpstreamError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def first_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        return next((str(item) for item in value if item), None)
    return None


def float_or_none(value: Any) -> float | None:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def int_or_none(value: Any) -> int | None:
    number = float_or_none(value)
    return int(number) if number is not None else None


def seconds_to_minutes(value: Any) -> int | None:
    seconds = float_or_none(value)
    if seconds is None:
        return None
    return max(0, math.ceil(seconds / 60))


def positive_fare(value: Any) -> float | None:
    fare = float_or_none(value)
    if fare is None or fare <= 0:
        return None
    return round(fare, 2)


def require_key() -> str:
    try:
        key, _ = resolve_key()
        return key
    except CredentialError as error:
        raise ConfigError(str(error)) from error


def fetch_json(
    endpoint: str,
    params: dict[str, str],
    *,
    key: str,
    label: str,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    # Keep the credential confined to the request URL. Error messages deliberately
    # refer only to the logical operation and never echo a URL or request headers.
    request_params = {**params, "key": key, "output": "JSON"}
    last_error = "unknown network error"
    for attempt in range(retries + 1):
        request = Request(
            f"{endpoint}?{urlencode(request_params)}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
                text = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise UpstreamError(f"AMap {label} returned a non-object JSON response")
            if str(payload.get("status", "")) != "1":
                code = str(payload.get("infocode", "unknown"))
                info = str(payload.get("info", "request rejected"))
                raise UpstreamError(f"AMap {label} error {code}: {info}")
            return payload
        except HTTPError as error:
            last_error = f"HTTP {error.code}"
            if error.code != 429 and error.code < 500:
                break
        except URLError as error:
            last_error = f"network error: {error.reason}"
        except TimeoutError:
            last_error = "request timed out"
        except json.JSONDecodeError:
            last_error = "invalid JSON response"
        if attempt < retries:
            time.sleep(0.4 * (2**attempt))
    raise UpstreamError(f"AMap {label} failed: {last_error}")


def geocode(
    address: str,
    city: str | None,
    *,
    key: str,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    params = {"address": address}
    if city:
        params.update({"city": city, "citylimit": "false"})
    payload = fetch_json(
        GEOCODE_URL,
        params,
        key=key,
        label="geocoding",
        timeout=timeout,
        retries=retries,
    )
    rows = payload.get("geocodes")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise UpstreamError(f"AMap could not geocode address: {address}")
    row = rows[0]
    location = str(row.get("location", ""))
    if not location or "," not in location:
        raise UpstreamError(f"AMap geocoding omitted coordinates for: {address}")
    return {
        "query": address,
        "formatted_address": row.get("formatted_address"),
        "location": location,
        "province": first_text(row.get("province")),
        "city": first_text(row.get("city")),
        "district": first_text(row.get("district")),
        "adcode": first_text(row.get("adcode")),
        "level": first_text(row.get("level")),
        "candidate_count": len(rows),
    }


def stop_name(value: Any) -> str | None:
    return str(value.get("name")) if isinstance(value, dict) and value.get("name") else None


def clean_instruction(value: Any) -> str | None:
    if not value:
        return None
    text = re.sub(r"0x[0-9a-fA-F]{2}", "", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def normalize_walking(walking: dict[str, Any]) -> dict[str, Any]:
    steps = walking.get("steps") if isinstance(walking.get("steps"), list) else []
    instructions: list[str] = []
    for step in steps:
        instruction = clean_instruction(step.get("instruction")) if isinstance(step, dict) else None
        if instruction:
            instructions.append(instruction)
    return {
        "mode": "walk",
        "from": first_text(walking.get("origin")),
        "to": first_text(walking.get("destination")),
        "duration_minutes": seconds_to_minutes(walking.get("duration")),
        "distance_meters": int_or_none(walking.get("distance")),
        "instructions": instructions,
    }


def bus_mode(line: dict[str, Any]) -> str:
    clue = f"{line.get('type', '')} {line.get('name', '')}"
    return "metro" if any(token in clue for token in ("地铁", "轨道交通", "轻轨", "磁悬浮")) else "bus"


def normalize_bus(bus: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    lines = bus.get("buslines") if isinstance(bus.get("buslines"), list) else []
    lines = [line for line in lines if isinstance(line, dict)]
    if not lines:
        return None, 0
    selected = lines[0]
    via_stops = selected.get("via_stops") if isinstance(selected.get("via_stops"), list) else []
    return (
        {
            "mode": bus_mode(selected),
            "service": selected.get("name"),
            "from": stop_name(selected.get("departure_stop")),
            "to": stop_name(selected.get("arrival_stop")),
            "duration_minutes": seconds_to_minutes(selected.get("duration")),
            "distance_meters": int_or_none(selected.get("distance")),
            "via_stop_count": int_or_none(selected.get("via_num")),
            "via_stops": [stop_name(item) for item in via_stops if stop_name(item)],
            "alternative_services": [
                str(item.get("name")) for item in lines[1:] if item.get("name")
            ],
        },
        1,
    )


def normalize_railway(railway: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    item = {
        "mode": "rail",
        "service": railway.get("name") or railway.get("trip"),
        "from": stop_name(railway.get("departure_stop")),
        "to": stop_name(railway.get("arrival_stop")),
        "duration_minutes": seconds_to_minutes(railway.get("time") or railway.get("duration")),
        "distance_meters": int_or_none(railway.get("distance")),
    }
    fields = ("service", "from", "to", "duration_minutes", "distance_meters")
    return (item, 1) if any(item.get(field) is not None for field in fields) else (None, 0)


def normalize_taxi(taxi: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    item = {
        "mode": "taxi",
        "from": first_text(taxi.get("origin")),
        "to": first_text(taxi.get("destination")),
        "duration_minutes": seconds_to_minutes(taxi.get("duration")),
        "distance_meters": int_or_none(taxi.get("distance")),
        "price_cny": positive_fare(taxi.get("price") or taxi.get("cost")),
    }
    fields = ("from", "to", "duration_minutes", "distance_meters", "price_cny")
    return (item, 1) if any(item.get(field) is not None for field in fields) else (None, 0)


def normalize_transit(transit: dict[str, Any], index: int) -> dict[str, Any]:
    raw_segments = transit.get("segments") if isinstance(transit.get("segments"), list) else []
    segments: list[dict[str, Any]] = []
    boardings = 0
    for wrapper in raw_segments:
        if not isinstance(wrapper, dict):
            continue
        walking = wrapper.get("walking")
        if isinstance(walking, dict) and walking:
            segments.append(normalize_walking(walking))
        bus = wrapper.get("bus")
        if isinstance(bus, dict) and bus:
            item, count = normalize_bus(bus)
            if item:
                segments.append(item)
                boardings += count
        railway = wrapper.get("railway")
        if isinstance(railway, dict) and railway:
            item, count = normalize_railway(railway)
            if item:
                segments.append(item)
                boardings += count
        taxi = wrapper.get("taxi")
        if isinstance(taxi, dict) and taxi:
            item, count = normalize_taxi(taxi)
            if item:
                segments.append(item)
                boardings += count

    return {
        "id": f"amap-{index}",
        "duration_minutes": seconds_to_minutes(transit.get("duration")),
        "fare_cny_per_person": positive_fare(transit.get("cost")),
        "distance_meters": int_or_none(transit.get("distance")),
        "walking_distance_meters": int_or_none(transit.get("walking_distance")),
        "transfer_count": max(0, boardings - 1),
        "vehicle_boardings": boardings,
        "nightflag": transit.get("nightflag"),
        "segments": segments,
    }


def transit_query(
    origin: dict[str, Any],
    destination: dict[str, Any],
    args: argparse.Namespace,
    *,
    key: str,
) -> list[dict[str, Any]]:
    origin_city = args.origin_city or origin.get("city") or origin.get("adcode")
    destination_city = args.destination_city or destination.get("city") or destination.get("adcode")
    if not origin_city:
        raise UpstreamError("AMap could not determine the origin city")
    params = {
        "origin": str(origin["location"]),
        "destination": str(destination["location"]),
        "city": str(origin_city),
        "strategy": str(args.strategy),
        "nightflag": str(args.nightflag),
        "extensions": "all",
    }
    if destination_city:
        params["cityd"] = str(destination_city)
    if args.date:
        params["date"] = args.date
    if args.time:
        params["time"] = args.time
    payload = fetch_json(
        TRANSIT_URL,
        params,
        key=key,
        label="public-transit routing",
        timeout=args.timeout,
        retries=args.retries,
    )
    route = payload.get("route")
    transits = route.get("transits") if isinstance(route, dict) else None
    if not isinstance(transits, list):
        raise UpstreamError("AMap transit response omitted route.transits")
    return [
        normalize_transit(item, index)
        for index, item in enumerate(transits, start=1)
        if isinstance(item, dict)
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True, help="Origin address or place name")
    parser.add_argument("--destination", required=True, help="Destination address or place name")
    parser.add_argument("--origin-city", help="City name or adcode used to disambiguate the origin")
    parser.add_argument("--destination-city", help="City name or adcode used to disambiguate the destination")
    parser.add_argument("--date", help="Travel date, YYYY-MM-DD")
    parser.add_argument("--time", help="Departure time, HH:MM")
    parser.add_argument("--strategy", type=int, choices=range(0, 6), default=0)
    parser.add_argument("--nightflag", type=int, choices=[0, 1], default=0)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.limit < 0:
            raise ValueError("--limit must be non-negative")
        if args.date:
            datetime.strptime(args.date, "%Y-%m-%d")
        if args.time:
            datetime.strptime(args.time, "%H:%M")
        key = require_key()
        origin = geocode(
            args.origin,
            args.origin_city,
            key=key,
            timeout=args.timeout,
            retries=max(0, args.retries),
        )
        destination = geocode(
            args.destination,
            args.destination_city,
            key=key,
            timeout=args.timeout,
            retries=max(0, args.retries),
        )
        routes = transit_query(origin, destination, args, key=key)
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
        result = {
            "query": {
                "origin": args.origin,
                "destination": args.destination,
                "origin_city": args.origin_city,
                "destination_city": args.destination_city,
                "date": args.date,
                "time": args.time,
                "strategy": args.strategy,
            },
            "source": "amap-web-service",
            "queried_at": now_iso(),
            "origin": origin,
            "destination": destination,
            "count": len(routes),
            "routes": routes,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(
            json.dumps({"error": str(error), "type": type(error).__name__}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
