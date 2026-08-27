#!/usr/bin/env python3
"""Query AMap geocoding and public-transit REST APIs without a browser."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from amap_credentials import CredentialError, resolve_key

GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
TRANSIT_URL = "https://restapi.amap.com/v3/direction/transit/integrated"
WALKING_URL = "https://restapi.amap.com/v3/direction/walking"
USER_AGENT = "route-planner/1.0"
CHINA_TZ = timezone(timedelta(hours=8))
AMAP_CACHE_ROOT = Path(tempfile.gettempdir()) / "codex-route-planner" / "amap"
AMAP_LOCK_DIR = AMAP_CACHE_ROOT / "request.lock"
AMAP_STAMP_PATH = AMAP_CACHE_ROOT / "last-request.txt"
GEOCODE_CACHE_DIR = AMAP_CACHE_ROOT / "geocode"
RETRIABLE_AMAP_CODES = {"10020", "10021", "10044"}
MIN_REQUEST_INTERVAL_SECONDS = 0.8
GEOCODE_CACHE_TTL_SECONDS = 7 * 86400


class ConfigError(RuntimeError):
    pass


class UpstreamError(RuntimeError):
    pass


class LocationMismatchError(ValueError):
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


@contextmanager
def request_slot(min_interval: float = MIN_REQUEST_INTERVAL_SECONDS):
    """Serialize AMap calls across processes without persisting the credential."""
    AMAP_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 30
    acquired = False
    while not acquired:
        try:
            AMAP_LOCK_DIR.mkdir()
            acquired = True
        except FileExistsError:
            try:
                stale = time.time() - AMAP_LOCK_DIR.stat().st_mtime > 120
            except OSError:
                stale = False
            if stale:
                with suppress(OSError):
                    AMAP_LOCK_DIR.rmdir()
                continue
            if time.monotonic() >= deadline:
                raise UpstreamError("AMap request throttle lock timed out") from None
            time.sleep(0.1)
    try:
        try:
            last_request = float(AMAP_STAMP_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last_request = 0.0
        delay = max(0.0, min_interval - (time.time() - last_request))
        if delay:
            time.sleep(delay)
        yield
    finally:
        with suppress(OSError):
            AMAP_STAMP_PATH.write_text(str(time.time()), encoding="utf-8")
        with suppress(OSError):
            AMAP_LOCK_DIR.rmdir()


def load_cache(path: Path, ttl_seconds: int) -> dict[str, Any] | None:
    try:
        if time.time() - path.stat().st_mtime > ttl_seconds:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


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
            with request_slot(), urlopen(request, timeout=timeout) as response:
                raw = response.read()
                text = raw.decode(
                    response.headers.get_content_charset() or "utf-8",
                    errors="replace",
                )
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise UpstreamError(f"AMap {label} returned a non-object JSON response")
            if str(payload.get("status", "")) != "1":
                code = str(payload.get("infocode", "unknown"))
                info = str(payload.get("info", "request rejected"))
                last_error = f"error {code}: {info}"
                if code not in RETRIABLE_AMAP_CODES or attempt >= retries:
                    raise UpstreamError(f"AMap {label} {last_error}")
            else:
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
            time.sleep(min(4.0, 0.5 * (2**attempt)))
    raise UpstreamError(f"AMap {label} failed: {last_error}")


def normalize_area(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"\s+", "", value).lower()
    text = re.sub(r"(特别行政区|自治区|自治州|地区|省|市|区|县)$", "", text)
    for canonical, prefixes in {
        "内蒙古": ("内蒙古",),
        "广西": ("广西壮族", "广西"),
        "西藏": ("西藏",),
        "宁夏": ("宁夏回族", "宁夏"),
        "新疆": ("新疆维吾尔", "新疆"),
        "香港": ("香港",),
        "澳门": ("澳门",),
    }.items():
        if any(text.startswith(prefix) for prefix in prefixes):
            return canonical
    return text


def valid_location(value: str) -> str:
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", value)
    if not match:
        raise ValueError(f"invalid coordinate, expected longitude,latitude: {value}")
    longitude, latitude = map(float, match.groups())
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError(f"coordinate outside valid longitude/latitude range: {value}")
    return f"{longitude:g},{latitude:g}"


def normalize_geocode_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        location = valid_location(str(row.get("location", "")))
    except ValueError:
        return None
    return {
        "formatted_address": first_text(row.get("formatted_address")),
        "location": location,
        "province": first_text(row.get("province")),
        "city": first_text(row.get("city")),
        "district": first_text(row.get("district")),
        "adcode": first_text(row.get("adcode")),
        "level": first_text(row.get("level")),
    }


def area_matches(
    actual: str | None, expected: str | None, formatted_address: str | None
) -> bool:
    if not expected or str(expected).isdigit():
        return True
    expected_normalized = normalize_area(expected)
    actual_normalized = normalize_area(actual)
    if actual_normalized:
        return actual_normalized == expected_normalized
    formatted_normalized = normalize_area(formatted_address)
    return bool(expected_normalized and expected_normalized in formatted_normalized)


def candidate_matches(
    candidate: dict[str, Any],
    *,
    expected_province: str | None,
    expected_city: str | None,
    expected_district: str | None,
) -> bool:
    formatted = first_text(candidate.get("formatted_address"))
    return all(
        (
            area_matches(candidate.get("province"), expected_province, formatted),
            area_matches(candidate.get("city"), expected_city, formatted),
            area_matches(candidate.get("district"), expected_district, formatted),
        )
    )


def looks_like_administrative_area_query(address: str) -> bool:
    text = re.sub(r"\s+", "", address)
    if text.endswith(("校区", "园区", "景区", "小区", "厂区", "院区", "馆区")):
        return False
    return bool(
        re.fullmatch(
            r"[\u4e00-\u9fff]{1,30}(省|市|自治区|特别行政区|自治州|地区|区|县|旗|镇|乡|街道)",
            text,
        )
    )


def candidate_has_usable_precision(candidate: dict[str, Any], address: str) -> bool:
    level = str(candidate.get("level") or "")
    administrative_levels = {"国家", "省", "市", "区县", "乡镇", "村庄"}
    return level not in administrative_levels or looks_like_administrative_area_query(
        address
    )


def select_geocode_candidate(
    candidates: list[dict[str, Any]],
    *,
    address: str,
    expected_province: str | None = None,
    expected_city: str | None = None,
    expected_district: str | None = None,
) -> dict[str, Any]:
    area_matches_candidates = [
        item
        for item in candidates
        if candidate_matches(
            item,
            expected_province=expected_province,
            expected_city=expected_city,
            expected_district=expected_district,
        )
    ]
    matches = [
        item
        for item in area_matches_candidates
        if candidate_has_usable_precision(item, address)
    ]
    if matches:
        return matches[0]
    summary = "；".join(
        f"{item.get('formatted_address') or '地址未知'}"
        f"（{item.get('district') or '区县未知'}，{item.get('level') or '精度未知'}）"
        for item in candidates[:5]
    )
    constraints = "、".join(
        value
        for value in (expected_province, expected_city, expected_district)
        if value and not str(value).isdigit()
    )
    reason = (
        "only matched an administrative-area centroid instead of "
        "the requested specific place"
        if area_matches_candidates
        else f"conflicts with expected area {constraints or 'constraint'}"
    )
    raise LocationMismatchError(
        f"AMap geocoding for '{address}' {reason}; candidates: {summary or 'none'}"
    )


def geocode_payload(
    address: str,
    city: str | None,
    *,
    key: str,
    timeout: float,
    retries: int,
) -> tuple[dict[str, Any], str]:
    params = {"address": address}
    if city:
        params.update({"city": city, "citylimit": "false"})
    digest = hashlib.sha256(
        json.dumps(params, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cache_path = GEOCODE_CACHE_DIR / f"{digest}.json"
    cached = load_cache(cache_path, GEOCODE_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached, "hit"
    payload = fetch_json(
        GEOCODE_URL,
        params,
        key=key,
        label="geocoding",
        timeout=timeout,
        retries=retries,
    )
    save_cache(cache_path, payload)
    return payload, "live"


def geocode(
    address: str,
    city: str | None,
    *,
    key: str,
    timeout: float,
    retries: int,
    expected_province: str | None = None,
    expected_city: str | None = None,
    expected_district: str | None = None,
) -> dict[str, Any]:
    payload, cache_status = geocode_payload(
        address,
        city,
        key=key,
        timeout=timeout,
        retries=retries,
    )
    rows = payload.get("geocodes")
    if not isinstance(rows, list):
        rows = []
    candidates = [
        candidate
        for row in rows
        if isinstance(row, dict)
        for candidate in [normalize_geocode_candidate(row)]
        if candidate is not None
    ]
    if not candidates:
        raise UpstreamError(f"AMap could not geocode address: {address}")
    enriched_address = "".join(
        value
        for value in (expected_province, expected_city, expected_district, address)
        if value and not str(value).isdigit()
    )
    try:
        selected = select_geocode_candidate(
            candidates,
            address=address,
            expected_province=expected_province,
            expected_city=expected_city,
            expected_district=expected_district,
        )
    except LocationMismatchError:
        if not enriched_address or normalize_area(enriched_address) == normalize_area(
            address
        ):
            raise
        second_payload, second_status = geocode_payload(
            enriched_address,
            city,
            key=key,
            timeout=timeout,
            retries=retries,
        )
        second_rows = second_payload.get("geocodes")
        if not isinstance(second_rows, list):
            second_rows = []
        second_candidates = [
            candidate
            for row in second_rows
            if isinstance(row, dict)
            for candidate in [normalize_geocode_candidate(row)]
            if candidate is not None
        ]
        by_location = {
            item["location"]: item for item in [*candidates, *second_candidates]
        }
        candidates = list(by_location.values())
        selected = select_geocode_candidate(
            candidates,
            address=address,
            expected_province=expected_province,
            expected_city=expected_city,
            expected_district=expected_district,
        )
        cache_status = "live" if second_status == "live" else cache_status
    return {
        "query": address,
        **selected,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "validation": {
            "status": "matched"
            if any((expected_province, expected_city, expected_district))
            else "not_requested",
            "expected_province": expected_province,
            "expected_city": expected_city,
            "expected_district": expected_district,
        },
        "cache_status": cache_status,
    }


def coordinate_endpoint(label: str, location: str) -> dict[str, Any]:
    return {
        "query": label,
        "formatted_address": None,
        "location": valid_location(location),
        "province": None,
        "city": None,
        "district": None,
        "adcode": None,
        "level": "coordinate",
        "candidate_count": 0,
        "candidates": [],
        "validation": {"status": "coordinate_provided"},
        "cache_status": "not_applicable",
    }


def stop_name(value: Any) -> str | None:
    return (
        str(value.get("name"))
        if isinstance(value, dict) and value.get("name")
        else None
    )


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
        instruction = (
            clean_instruction(step.get("instruction"))
            if isinstance(step, dict)
            else None
        )
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


def normalize_walking_path(path: dict[str, Any], index: int) -> dict[str, Any]:
    steps = path.get("steps") if isinstance(path.get("steps"), list) else []
    normalized_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        normalized_steps.append(
            {
                "instruction": clean_instruction(step.get("instruction")),
                "road": first_text(step.get("road")),
                "orientation": first_text(step.get("orientation")),
                "distance_meters": int_or_none(step.get("distance")),
                "duration_minutes": seconds_to_minutes(step.get("duration")),
            }
        )
    return {
        "id": f"amap-walk-{index}",
        "mode": "walk",
        "duration_minutes": seconds_to_minutes(path.get("duration")),
        "distance_meters": int_or_none(path.get("distance")),
        "steps": normalized_steps,
    }


def bus_mode(line: dict[str, Any]) -> str:
    name = str(line.get("name", ""))
    line_type = str(line.get("type", ""))
    metro_tokens = ("地铁", "轨道交通", "轻轨", "磁悬浮")
    bus_tokens = ("公交", "巴士", "班车", "快速公交", "有轨电车")
    if any(token in name for token in metro_tokens):
        return "metro"
    # AMap can return a broad or misleading type. A conventional numbered
    # "...路" service name is stronger evidence that this is a bus.
    if ("路" in name or any(token in name for token in bus_tokens)) and not any(
        token in name for token in metro_tokens
    ):
        return "bus"
    return "metro" if any(token in line_type for token in metro_tokens) else "bus"


def normalize_bus(bus: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    lines = bus.get("buslines") if isinstance(bus.get("buslines"), list) else []
    lines = [line for line in lines if isinstance(line, dict)]
    if not lines:
        return None, 0
    selected = lines[0]
    via_stops = (
        selected.get("via_stops") if isinstance(selected.get("via_stops"), list) else []
    )
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
        "duration_minutes": seconds_to_minutes(
            railway.get("time") or railway.get("duration")
        ),
        "distance_meters": int_or_none(railway.get("distance")),
    }
    fields = ("service", "from", "to", "duration_minutes", "distance_meters")
    return (
        (item, 1) if any(item.get(field) is not None for field in fields) else (None, 0)
    )


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
    return (
        (item, 1) if any(item.get(field) is not None for field in fields) else (None, 0)
    )


def normalize_transit(transit: dict[str, Any], index: int) -> dict[str, Any]:
    raw_segments = (
        transit.get("segments") if isinstance(transit.get("segments"), list) else []
    )
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
    destination_city = (
        args.destination_city or destination.get("city") or destination.get("adcode")
    )
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


def walking_query(
    origin: dict[str, Any],
    destination: dict[str, Any],
    args: argparse.Namespace,
    *,
    key: str,
) -> list[dict[str, Any]]:
    payload = fetch_json(
        WALKING_URL,
        {
            "origin": str(origin["location"]),
            "destination": str(destination["location"]),
            "extensions": "all",
        },
        key=key,
        label="walking routing",
        timeout=args.timeout,
        retries=args.retries,
    )
    route = payload.get("route")
    paths = route.get("paths") if isinstance(route, dict) else None
    if not isinstance(paths, list):
        raise UpstreamError("AMap walking response omitted route.paths")
    return [
        normalize_walking_path(path, index)
        for index, path in enumerate(paths, start=1)
        if isinstance(path, dict)
    ]


def resolve_endpoint(
    args: argparse.Namespace, prefix: str, *, key: str
) -> dict[str, Any]:
    label = str(getattr(args, prefix))
    location = getattr(args, f"{prefix}_location")
    if location:
        return coordinate_endpoint(label, location)
    return geocode(
        label,
        getattr(args, f"{prefix}_city"),
        key=key,
        timeout=args.timeout,
        retries=max(0, args.retries),
        expected_province=getattr(args, f"{prefix}_province"),
        expected_city=getattr(args, f"{prefix}_city"),
        expected_district=getattr(args, f"{prefix}_district"),
    )


def add_endpoint_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--origin", required=True, help="Origin address or place name")
    parser.add_argument(
        "--destination", required=True, help="Destination address or place name"
    )
    parser.add_argument(
        "--origin-location", help="Trusted origin coordinate as longitude,latitude"
    )
    parser.add_argument(
        "--destination-location",
        help="Trusted destination coordinate as longitude,latitude",
    )
    parser.add_argument(
        "--origin-province", help="Expected province used to validate origin geocoding"
    )
    parser.add_argument(
        "--destination-province",
        help="Expected province used to validate destination geocoding",
    )
    parser.add_argument(
        "--origin-city",
        help="Expected city or adcode used to geocode and validate the origin",
    )
    parser.add_argument(
        "--destination-city",
        help="Expected city or adcode used to geocode and validate the destination",
    )
    parser.add_argument(
        "--origin-district",
        help="Expected district/county used to validate origin geocoding",
    )
    parser.add_argument(
        "--destination-district",
        help="Expected district/county used to validate destination geocoding",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    transit = sub.add_parser("transit", help="Query integrated public-transit routes")
    add_endpoint_arguments(transit)
    transit.add_argument("--date", help="Travel date, YYYY-MM-DD")
    transit.add_argument("--time", help="Departure time, HH:MM")
    transit.add_argument("--strategy", type=int, choices=range(6), default=0)
    transit.add_argument("--nightflag", type=int, choices=[0, 1], default=0)

    walk = sub.add_parser("walk", help="Query walking routes")
    add_endpoint_arguments(walk)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    # Preserve the v0.1 command shape as an alias for the transit subcommand.
    if not raw_args or raw_args[0] not in {"transit", "walk"}:
        raw_args.insert(0, "transit")
    args = build_parser().parse_args(raw_args)
    try:
        if args.limit < 0:
            raise ValueError("--limit must be non-negative")
        if args.timeout <= 0:
            raise ValueError("--timeout must be positive")
        if args.retries < 0:
            raise ValueError("--retries must be non-negative")
        if args.command == "transit" and args.date:
            datetime.strptime(args.date, "%Y-%m-%d")
        if args.command == "transit" and args.time:
            datetime.strptime(args.time, "%H:%M")
        key = require_key()
        origin = resolve_endpoint(args, "origin", key=key)
        destination = resolve_endpoint(args, "destination", key=key)
        warnings: list[str] = []
        if args.command == "transit":
            routes = transit_query(origin, destination, args, key=key)
            for route in routes:
                route["service_window_verified"] = False
            if args.time:
                hour, minute = map(int, args.time.split(":"))
                minutes = hour * 60 + minute
                if minutes < 360 or minutes >= 1350:
                    warnings.append(
                        "行程接近清晨或深夜，提供方未返回可核验的首末班窗口"
                    )
        else:
            routes = walking_query(origin, destination, args, key=key)
        routes.sort(
            key=lambda item: (
                item["duration_minutes"] is None,
                item["duration_minutes"] or 10**9,
                item.get("transfer_count", 0),
                item.get("fare_cny_per_person") is None,
                item.get("fare_cny_per_person") or 10**9,
            )
        )
        if args.limit:
            routes = routes[: args.limit]
        result = {
            "query": {
                "mode": args.command,
                "origin": args.origin,
                "destination": args.destination,
                "origin_city": args.origin_city,
                "destination_city": args.destination_city,
                "origin_district": args.origin_district,
                "destination_district": args.destination_district,
                "date": getattr(args, "date", None),
                "time": getattr(args, "time", None),
                "strategy": getattr(args, "strategy", None),
            },
            "source": "amap-web-service",
            "queried_at": now_iso(),
            "origin": origin,
            "destination": destination,
            "warnings": warnings,
            "count": len(routes),
            "routes": routes,
        }
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
