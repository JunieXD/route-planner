#!/usr/bin/env python3
"""Query public 12306 website data without a browser or account."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/136 Safari/537.36 "
    "route-planner/1.0"
)
WWW_HOME = "https://www.12306.cn/index/"
KYFW_BASE = "https://kyfw.12306.cn"
SALE_TIME_URL = "https://www.12306.cn/index/otn/index12306/queryAllCacheSaleTime"
TRAIN_STOPS_URL = f"{KYFW_BASE}/otn/czxx/queryByTrainNo"
CACHE_PATH = Path(tempfile.gettempdir()) / "codex-china-route-planner" / "stations.json"
CHINA_TZ = timezone(timedelta(hours=8))

# The public direct-train endpoint does not expose a login-free transfer search.
# Transfer mode therefore explores a bounded, transparent set of major interchange
# cities. Callers can replace this list with --hubs; no result is described as a
# network-wide optimum.
DEFAULT_TRANSFER_HUBS = (
    "北京",
    "上海",
    "广州",
    "深圳",
    "武汉",
    "郑州",
    "南京",
    "杭州",
    "合肥",
    "南昌",
    "长沙",
    "济南",
    "徐州",
    "西安",
    "石家庄",
    "天津",
    "成都",
    "重庆",
    "贵阳",
    "昆明",
    "福州",
    "厦门",
    "太原",
    "兰州",
    "沈阳",
    "哈尔滨",
    "长春",
    "桂林",
    "柳州",
    "赣州",
    "鹰潭",
)

AVAILABLE_STATUSES = {"available", "count"}
SEATED_SEATS = {
    "商务座",
    "特等座",
    "优选一等座",
    "一等座",
    "二等座",
    "软座",
    "硬座",
}
SLEEPER_SEATS = {"高级软卧", "软卧", "一等卧", "动卧", "硬卧", "二等卧"}
SEAT_POLICIES = (
    "cheapest-available",
    "cheapest-priced",
    "seat-only",
    "sleeper-required",
    "named",
)


@dataclass(frozen=True)
class Station:
    code: str
    name: str
    city: str
    pinyin: str
    short: str


SEAT_DEFINITIONS: dict[str, tuple[str, int]] = {
    "9": ("商务座", 32),
    "A": ("商务座", 32),
    "P": ("特等座", 25),
    "M": ("一等座", 31),
    "D": ("优选一等座", 31),
    "O": ("二等座", 30),
    "S": ("二等座", 30),
    "6": ("高级软卧", 21),
    "4": ("软卧", 23),
    "I": ("一等卧", 23),
    "F": ("动卧", 33),
    "3": ("硬卧", 28),
    "J": ("二等卧", 28),
    "2": ("软座", 24),
    "1": ("硬座", 29),
    "W": ("无座", 26),
}


class UpstreamError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def normalize_name(value: str) -> str:
    text = re.sub(r"\s+", "", value.strip())
    if text.endswith("站"):
        text = text[:-1]
    if text.endswith("市"):
        text = text[:-1]
    return text.lower()


def parse_hhmm(value: str) -> int:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
    if not match:
        raise argparse.ArgumentTypeError(f"invalid HH:MM time: {value}")
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        raise argparse.ArgumentTypeError(f"invalid HH:MM time: {value}")
    return hour * 60 + minute


def duration_minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def availability(raw: str) -> dict[str, Any]:
    value = raw.strip()
    if value in {"有", "充足"}:
        return {"status": "available", "raw": value}
    if value in {"无", "--", ""}:
        return {"status": "sold_out" if value == "无" else "unknown", "raw": value}
    if value == "候补":
        return {"status": "waitlist", "raw": value}
    if value.isdigit():
        return {"status": "count", "count": int(value), "raw": value}
    return {"status": "unknown", "raw": value}


class PublicSession:
    def __init__(self, timeout: float = 12.0, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.last_request_at = 0.0
        self.query_path = "/otn/leftTicket/queryG"
        self.train_stop_cache: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    def request_text(
        self,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "application/json, text/plain, */*",
        }
        request_headers.update(headers or {})
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            delay = max(0.0, 0.35 - (time.monotonic() - self.last_request_at))
            if delay:
                time.sleep(delay)
            request = Request(url, data=data, headers=request_headers)
            self.last_request_at = time.monotonic()
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = response.read()
                    charset = response.headers.get_content_charset() or "utf-8"
                    return raw.decode(charset, errors="replace")
            except HTTPError as error:
                last_error = error
                if error.code != 429 and error.code < 500:
                    break
            except (URLError, TimeoutError) as error:
                last_error = error
            if attempt < self.retries:
                time.sleep(0.4 * (2**attempt))
        raise UpstreamError(f"request failed for {url}: {last_error}")

    def request_json(
        self,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        text = self.request_text(url, data=data, headers=headers)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            preview = re.sub(r"\s+", " ", text[:180])
            raise UpstreamError(f"upstream did not return JSON: {preview}") from error
        if not isinstance(payload, dict):
            raise UpstreamError("upstream JSON root is not an object")
        return payload

    def initialize_rail(self) -> None:
        html = self.request_text(
            f"{KYFW_BASE}/otn/leftTicket/init",
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        match = re.search(r"CLeftTicketUrl\s*=\s*['\"]([^'\"]+)", html)
        if match:
            route = match.group(1).strip()
            if route.startswith("/"):
                self.query_path = route
            elif route.startswith("otn/"):
                self.query_path = f"/{route}"
            else:
                self.query_path = f"/otn/{route}"


def parse_station_script(script: str) -> list[Station]:
    match = re.search(r"station_names\s*=\s*['\"]([\s\S]*?)['\"]\s*;?", script)
    if not match:
        raise UpstreamError("could not parse station_names from official script")
    stations: list[Station] = []
    for record in match.group(1).split("@"):
        if not record:
            continue
        fields = record.split("|")
        if len(fields) < 5 or not fields[1] or not fields[2]:
            continue
        stations.append(
            Station(
                code=fields[2].strip(),
                name=fields[1].strip(),
                pinyin=fields[3].strip(),
                short=fields[4].strip(),
                city=(
                    fields[7].strip()
                    if len(fields) > 7 and fields[7].strip()
                    else fields[1].strip()
                ),
            )
        )
    if not stations:
        raise UpstreamError("official station script contained no stations")
    return stations


def download_stations(session: PublicSession) -> list[Station]:
    html = session.request_text(
        WWW_HOME, headers={"Accept": "text/html,application/xhtml+xml"}
    )
    sources = re.findall(
        r"<script[^>]+src=['\"]([^'\"]*station_name[^'\"]+\.js(?:\?[^'\"]*)?)['\"]",
        html,
        re.I,
    )
    if not sources:
        sources = re.findall(r"([^'\"\s<>]*station_name[^'\"\s<>]+\.js)", html, re.I)
    if not sources:
        raise UpstreamError("official homepage did not expose a station_name script")
    errors: list[str] = []
    for source in sources:
        try:
            return parse_station_script(session.request_text(urljoin(WWW_HOME, source)))
        except UpstreamError as error:
            errors.append(str(error))
    raise UpstreamError("; ".join(errors))


def load_stations(session: PublicSession, refresh: bool = False) -> list[Station]:
    if (
        not refresh
        and CACHE_PATH.exists()
        and time.time() - CACHE_PATH.stat().st_mtime < 86400
    ):
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            return [Station(**item) for item in data]
        except (OSError, TypeError, json.JSONDecodeError):
            pass
    stations = download_stations(session)
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps([asdict(item) for item in stations], ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass
    return stations


def station_matches(query: str, stations: list[Station]) -> list[Station]:
    if re.fullmatch(r"[A-Z]{3}", query.strip()):
        return [station for station in stations if station.code == query.strip()]
    needle = normalize_name(query)
    exact_name = [
        station for station in stations if normalize_name(station.name) == needle
    ]
    if exact_name:
        return exact_name
    city = [station for station in stations if normalize_name(station.city) == needle]
    if city:
        return sorted(
            city, key=lambda item: (item.name != item.city, len(item.name), item.name)
        )
    fuzzy = [
        station
        for station in stations
        if needle in normalize_name(station.name)
        or needle == station.pinyin.lower()
        or needle == station.short.lower()
    ]
    return sorted(
        fuzzy,
        key=lambda item: (
            normalize_name(item.name) != needle,
            len(item.name),
            item.name,
        ),
    )


def resolve_station(query: str, stations: list[Station]) -> Station:
    matches = station_matches(query, stations)
    if not matches:
        raise ValueError(f"station or city not found: {query}")
    needle = normalize_name(query)
    exact = [item for item in matches if normalize_name(item.name) == needle]
    if len(exact) == 1:
        return exact[0]
    city_representative = [
        item
        for item in matches
        if normalize_name(item.name) == normalize_name(item.city) == needle
    ]
    if city_representative:
        return city_representative[0]
    if len(matches) == 1:
        return matches[0]
    names = "、".join(item.name for item in matches[:8])
    raise ValueError(f"ambiguous station '{query}'; candidates: {names}")


def query_scope(query: str, stations: list[Station]) -> str:
    """Treat a city name as city-wide even when it is also a station name."""
    needle = normalize_name(query)
    return (
        "city"
        if any(normalize_name(item.city) == needle for item in stations)
        else "station"
    )


def parse_prices(encoded: str, fields: list[str]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(encoded), 10):
        chunk = encoded[offset : offset + 10]
        if len(chunk) != 10 or not chunk[1:6].isdigit():
            continue
        code = chunk[0]
        definition = SEAT_DEFINITIONS.get(code)
        if not definition:
            continue
        name, availability_field = definition
        if name in by_name:
            continue
        raw_availability = (
            fields[availability_field] if len(fields) > availability_field else ""
        )
        by_name[name] = {
            "name": name,
            "code": code,
            "price_cny": int(chunk[1:6]) / 10,
            **availability(raw_availability),
        }
    return list(by_name.values())


def parse_train_rows(payload: dict[str, Any], query_date: str) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        messages = (
            payload.get("messages") or payload.get("message") or "missing data.result"
        )
        raise UpstreamError(f"12306 query unavailable: {messages}")
    response_map = data.get("map") if isinstance(data.get("map"), dict) else {}
    results: list[dict[str, Any]] = []
    for row in data["result"]:
        if not isinstance(row, str):
            continue
        fields = row.split("|")
        if len(fields) <= 39:
            continue
        duration = duration_minutes(fields[10])
        departure = datetime.fromisoformat(f"{query_date}T{fields[8]}:00+08:00")
        arrival = departure + timedelta(minutes=duration)
        prices = parse_prices(fields[39], fields)
        results.append(
            {
                "train_code": fields[3],
                "train_no": fields[2],
                "from_station": response_map.get(fields[6], fields[6]),
                "from_station_code": fields[6],
                "to_station": response_map.get(fields[7], fields[7]),
                "to_station_code": fields[7],
                "departure_at": departure.isoformat(timespec="minutes"),
                "arrival_at": arrival.isoformat(timespec="minutes"),
                "duration_minutes": duration,
                "can_buy": fields[11] == "Y",
                "button_text": re.sub(r"<[^>]+>", " ", fields[1]).strip(),
                "from_station_no": fields[16],
                "to_station_no": fields[17],
                "seat_types": fields[35],
                "seats": prices,
                "source": "12306-public",
            }
        )
    return results


def seat_price(train: dict[str, Any], seat_name: str) -> float | None:
    for seat in train["seats"]:
        if seat["name"] == seat_name:
            return float(seat["price_cny"])
    return None


def query_train_pair(
    session: PublicSession,
    *,
    date: str,
    from_query: str,
    to_query: str,
    stations: list[Station],
) -> tuple[list[dict[str, Any]], Station, Station, str, str]:
    """Query one public direct-train pair while preserving city/station scope."""
    origin = resolve_station(from_query, stations)
    destination = resolve_station(to_query, stations)
    origin_scope = query_scope(from_query, stations)
    destination_scope = query_scope(to_query, stations)
    params = urlencode(
        {
            "leftTicketDTO.train_date": date,
            "leftTicketDTO.from_station": origin.code,
            "leftTicketDTO.to_station": destination.code,
            "purpose_codes": "ADULT",
        }
    )
    payload = session.request_json(
        f"{KYFW_BASE}{session.query_path}?{params}",
        headers={"Referer": f"{KYFW_BASE}/otn/leftTicket/init"},
    )
    trains = parse_train_rows(payload, date)
    if origin_scope == "station":
        trains = [item for item in trains if item["from_station_code"] == origin.code]
    if destination_scope == "station":
        trains = [
            item for item in trains if item["to_station_code"] == destination.code
        ]
    return trains, origin, destination, origin_scope, destination_scope


def parse_train_stops(
    payload: dict[str, Any], stations: list[Station]
) -> list[dict[str, Any]]:
    data = payload.get("data")
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        messages = payload.get("messages") or "missing data.data"
        raise UpstreamError(f"12306 train-stop query unavailable: {messages}")
    station_by_name = {normalize_name(item.name): item for item in stations}
    stops: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("station_name"):
            continue
        station = station_by_name.get(normalize_name(str(row["station_name"])))
        stops.append(
            {
                "station_name": str(row["station_name"]),
                "station_code": station.code if station else None,
                "station_no": str(row.get("station_no", "")),
                "arrive_time": str(row.get("arrive_time", "----")),
                "start_time": str(row.get("start_time", "----")),
                "stopover_time": str(row.get("stopover_time", "")),
            }
        )
    if not stops:
        raise UpstreamError("12306 train-stop query returned no stops")
    return stops


def add_stop_datetimes(
    stops: list[dict[str, Any]], *, anchor_station_no: str, anchor_departure_at: str
) -> list[dict[str, Any]]:
    """Attach real dates to clock-only stop rows using a known service departure."""
    base = datetime(2000, 1, 1, tzinfo=CHINA_TZ)
    previous: datetime | None = None
    timed: list[dict[str, Any]] = []
    for stop in stops:
        item = dict(stop)
        for source_field, output_field in (
            ("arrive_time", "arrival_at"),
            ("start_time", "departure_at"),
        ):
            clock = str(stop.get(source_field, ""))
            if not re.fullmatch(r"\d{2}:\d{2}", clock):
                item[output_field] = None
                continue
            hour, minute = map(int, clock.split(":"))
            event = base.replace(hour=hour, minute=minute)
            while previous is not None and event < previous:
                event += timedelta(days=1)
            item[output_field] = event
            previous = event
        timed.append(item)

    anchor = next(
        (item for item in timed if str(item.get("station_no")) == anchor_station_no),
        None,
    )
    if not anchor or not isinstance(anchor.get("departure_at"), datetime):
        raise UpstreamError(
            f"train-stop response lacks anchor departure at station {anchor_station_no}"
        )
    offset = datetime.fromisoformat(anchor_departure_at) - anchor["departure_at"]
    for item in timed:
        for field in ("arrival_at", "departure_at"):
            value = item.get(field)
            item[field] = (
                (value + offset).isoformat(timespec="minutes")
                if isinstance(value, datetime)
                else None
            )
    return timed


def query_train_stops(
    session: PublicSession,
    *,
    train: dict[str, Any],
    stations: list[Station],
) -> list[dict[str, Any]]:
    departure_date = str(train["departure_at"])[:10]
    cache_key = (
        str(train["train_no"]),
        str(train["from_station_code"]),
        departure_date,
    )
    cached = session.train_stop_cache.get(cache_key)
    if cached is not None:
        return [dict(item) for item in cached]
    params = urlencode(
        {
            "train_no": train["train_no"],
            "from_station_telecode": train["from_station_code"],
            "to_station_telecode": train["to_station_code"],
            "depart_date": departure_date,
        }
    )
    payload = session.request_json(
        f"{TRAIN_STOPS_URL}?{params}",
        headers={"Referer": f"{KYFW_BASE}/otn/czxx/init"},
    )
    stops = add_stop_datetimes(
        parse_train_stops(payload, stations),
        anchor_station_no=str(train["from_station_no"]),
        anchor_departure_at=str(train["departure_at"]),
    )
    session.train_stop_cache[cache_key] = stops
    return [dict(item) for item in stops]


def allowed_seat_names(policy: str, named: set[str]) -> set[str] | None:
    if policy == "seat-only":
        return SEATED_SEATS
    if policy == "sleeper-required":
        return SLEEPER_SEATS
    if policy == "named":
        return named
    return None


def select_seat(
    train: dict[str, Any],
    *,
    policy: str,
    named: set[str] | None = None,
    available_only: bool = False,
) -> dict[str, Any] | None:
    """Select a priced seat without confusing a quoted fare with live inventory."""
    allowed = allowed_seat_names(policy, named or set())
    if (
        policy == "sleeper-required"
        and train.get("departure_at")
        and train.get("arrival_at")
        and not is_overnight(str(train["departure_at"]), str(train["arrival_at"]))
    ):
        # Require a sleeper where the train itself runs through the night, but
        # keep daytime feeder legs at their cheapest suitable available fare.
        allowed = None
    candidates = [
        seat
        for seat in train.get("seats", [])
        if isinstance(seat, dict)
        and seat.get("price_cny") is not None
        and (allowed is None or str(seat.get("name")) in allowed)
    ]
    strict_availability = available_only or policy == "cheapest-available"
    if strict_availability and train.get("can_buy", True) is False:
        return None
    if strict_availability:
        candidates = [
            seat for seat in candidates if seat.get("status") in AVAILABLE_STATUSES
        ]
    if not candidates:
        return None
    if policy == "cheapest-priced":
        candidates.sort(
            key=lambda seat: (float(seat["price_cny"]), str(seat.get("name", "")))
        )
    else:
        candidates.sort(
            key=lambda seat: (
                seat.get("status") not in AVAILABLE_STATUSES,
                float(seat["price_cny"]),
                str(seat.get("name", "")),
            )
        )
    selected = dict(candidates[0])
    selected["inventory_confirmed"] = (
        selected.get("status") in AVAILABLE_STATUSES
        and train.get("can_buy", True) is not False
    )
    return selected


def station_codes_for_query(query: str, stations: list[Station]) -> set[str]:
    resolved = resolve_station(query, stations)
    if query_scope(query, stations) == "station":
        return {resolved.code}
    city = normalize_name(resolved.city)
    return {
        station.code for station in stations if normalize_name(station.city) == city
    }


def load_cross_station_rules(
    path: Path | None, stations: list[Station]
) -> dict[tuple[str, str], dict[str, Any]]:
    """Load externally verified station-to-station ground connections."""
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rules") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("cross-station rule file must be an array or {rules: [...]}")
    rules: dict[tuple[str, str], dict[str, Any]] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"cross-station rule {index} is not an object")
        try:
            origin = resolve_station(str(row["from_station"]), stations)
            destination = resolve_station(str(row["to_station"]), stations)
            duration = int(row["duration_minutes"])
            price = float(row["price_cny"])
            source = str(row["source"]).strip()
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"cross-station rule {index} requires resolvable stations, "
                "positive duration_minutes, non-negative price_cny and source"
            ) from error
        if duration <= 0 or price < 0 or not source:
            raise ValueError(
                f"cross-station rule {index} has invalid duration, price or source"
            )
        buffer = int(row.get("buffer_minutes", 0))
        if buffer < 0:
            raise ValueError(f"cross-station rule {index} has a negative buffer")
        rules[(origin.code, destination.code)] = {
            "duration_minutes": duration,
            "buffer_minutes": buffer,
            "price_cny": price,
            "source": source,
            "verified_at": row.get("verified_at"),
            "notes": row.get("notes", []),
        }
    return rules


def transfer_between(
    previous: dict[str, Any],
    following: dict[str, Any],
    *,
    station_by_code: dict[str, Station],
    allow_cross_station: bool,
    same_station_min: int,
    cross_station_min: int,
    max_wait: int,
    cross_station_rules: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    previous_arrival = datetime.fromisoformat(str(previous["arrival_at"]))
    following_departure = datetime.fromisoformat(str(following["departure_at"]))
    wait = int((following_departure - previous_arrival).total_seconds() // 60)
    same_station = previous["to_station_code"] == following["from_station_code"]
    if same_station:
        required = same_station_min
        kind = "same_station"
        connection: dict[str, Any] | None = None
    else:
        if not allow_cross_station:
            return None
        pair = (
            str(previous["to_station_code"]),
            str(following["from_station_code"]),
        )
        rule = (cross_station_rules or {}).get(pair)
        if rule is None:
            return None
        connection = dict(rule)
        required = (
            int(rule["duration_minutes"])
            + int(rule.get("buffer_minutes", 0))
            + cross_station_min
        )
        kind = "cross_station"
    if wait < required or wait > max_wait:
        return None
    slack = wait - required
    result = {
        "kind": kind,
        "from_station": previous["to_station"],
        "to_station": following["from_station"],
        "wait_minutes": wait,
        "required_minutes": required,
        "slack_minutes": slack,
        "risk": "high" if slack < 15 else "medium" if slack < 35 else "low",
    }
    if connection is not None:
        result["connection"] = connection
        result["connection"]["required_minutes_with_buffer"] = required
    return result


def is_overnight(departure_at: str, arrival_at: str) -> bool:
    departure = datetime.fromisoformat(departure_at)
    arrival = datetime.fromisoformat(arrival_at)
    night_start = departure.replace(
        hour=22, minute=0, second=0, microsecond=0
    ) - timedelta(days=1)
    while night_start < arrival:
        night_end = night_start + timedelta(hours=8)
        if departure < night_end and arrival > night_start:
            return True
        night_start += timedelta(days=1)
    return False


def overnight_overlap_minutes(departure_at: str, arrival_at: str) -> int:
    departure = datetime.fromisoformat(departure_at)
    arrival = datetime.fromisoformat(arrival_at)
    total = 0
    night_start = departure.replace(
        hour=22, minute=0, second=0, microsecond=0
    ) - timedelta(days=1)
    while night_start < arrival:
        night_end = night_start + timedelta(hours=8)
        overlap_start = max(departure, night_start)
        overlap_end = min(arrival, night_end)
        if overlap_end > overlap_start:
            total += int((overlap_end - overlap_start).total_seconds() // 60)
        night_start += timedelta(days=1)
    return total


def itinerary_from_path(
    path: list[dict[str, Any]], transfers: list[dict[str, Any]]
) -> dict[str, Any]:
    selected = [leg.get("selected_seat") for leg in path]
    rail_price_complete = all(
        isinstance(seat, dict) and seat.get("price_cny") is not None
        for seat in selected
    )
    connection_prices = [
        item.get("connection", {}).get("price_cny")
        for item in transfers
        if item.get("kind") == "cross_station"
    ]
    connections_price_complete = all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in connection_prices
    )
    price_complete = rail_price_complete and connections_price_complete
    price = (
        round(
            sum(float(seat["price_cny"]) for seat in selected if isinstance(seat, dict))
            + sum(float(value) for value in connection_prices),
            2,
        )
        if price_complete
        else None
    )
    inventory_confirmed = all(
        isinstance(seat, dict) and bool(seat.get("inventory_confirmed"))
        for seat in selected
    )
    departure_at = str(path[0]["departure_at"])
    arrival_at = str(path[-1]["arrival_at"])
    duration = int(
        (
            datetime.fromisoformat(arrival_at) - datetime.fromisoformat(departure_at)
        ).total_seconds()
        // 60
    )
    overnight_leg_indexes = [
        index
        for index, leg in enumerate(path)
        if is_overnight(str(leg["departure_at"]), str(leg["arrival_at"]))
    ]
    overnight = is_overnight(departure_at, arrival_at)
    sleeper_on_overnight_legs = [
        isinstance(selected[index], dict)
        and str(selected[index].get("name")) in SLEEPER_SEATS
        for index in overnight_leg_indexes
    ]
    if not overnight:
        overnight_class = "not_overnight"
        comfort_notes: list[str] = []
    elif not overnight_leg_indexes:
        overnight_class = "overnight_transfer"
        comfort_notes = ["夜间时段主要用于候车或换乘，无车上卧睡段"]
    elif all(sleeper_on_overnight_legs):
        overnight_class = "sleeper"
        comfort_notes = ["夜间行程含卧铺席别"]
    elif any(sleeper_on_overnight_legs):
        overnight_class = "mixed"
        comfort_notes = ["仅部分夜间铁路段选择卧铺"]
    else:
        overnight_class = "seated"
        comfort_notes = ["夜间行程未选择卧铺"]
    overnight_seated_minutes = sum(
        overnight_overlap_minutes(str(leg["departure_at"]), str(leg["arrival_at"]))
        for index, leg in enumerate(path)
        if not (
            isinstance(selected[index], dict)
            and str(selected[index].get("name")) in SLEEPER_SEATS
        )
    )
    connection_minutes = sum(
        int(item.get("connection", {}).get("duration_minutes", 0)) for item in transfers
    )
    waiting_minutes = sum(
        max(
            0,
            int(item["wait_minutes"])
            - int(item.get("connection", {}).get("duration_minutes", 0)),
        )
        for item in transfers
    )
    risk_levels = {"low": 0, "medium": 1, "high": 2}
    worst_risk = max(
        (str(item.get("risk", "low")) for item in transfers),
        key=lambda value: risk_levels.get(value, 2),
        default="low",
    )
    burden_points = (
        len(transfers)
        + sum(item.get("kind") == "cross_station" for item in transfers)
        + sum(int(item["wait_minutes"]) >= 180 for item in transfers)
    )
    return {
        "id": "/".join(
            f"{leg['train_code']}:{leg['from_station_code']}-{leg['to_station_code']}"
            f"@{str(leg['departure_at'])[:16]}"
            for leg in path
        ),
        "departure_at": departure_at,
        "arrival_at": arrival_at,
        "duration_minutes": duration,
        "scheduled_span_minutes": duration,
        "train_duration_minutes": sum(int(leg["duration_minutes"]) for leg in path),
        "in_vehicle_minutes": sum(int(leg["duration_minutes"]) for leg in path),
        "waiting_minutes": waiting_minutes,
        "checkin_buffer_minutes": 0,
        "local_connection_minutes": connection_minutes,
        "door_to_door_duration_minutes": None,
        "unknown_origin_connection": True,
        "duration_complete": False,
        "transfer_count": len(path) - 1,
        "minimum_connection_slack_minutes": min(
            (int(item["slack_minutes"]) for item in transfers), default=None
        ),
        "price_cny_per_person": price,
        "price_complete": price_complete,
        "inventory_confirmed": inventory_confirmed,
        "inventory_status": (
            "available" if inventory_confirmed else "quoted_or_unavailable"
        ),
        "executable": inventory_confirmed,
        "connection_reliability": f"{worst_risk}_risk",
        "transfer_burden": (
            "high" if burden_points >= 4 else "medium" if burden_points >= 2 else "low"
        ),
        "overnight_class": overnight_class,
        "overnight_seated_minutes": overnight_seated_minutes,
        "comfort_notes": comfort_notes,
        "transfers": transfers,
        "legs": path,
    }


def search_transfer_graph(
    graph: list[dict[str, Any]],
    *,
    origin_codes: set[str],
    destination_codes: set[str],
    station_by_code: dict[str, Station],
    max_transfers: int,
    allow_cross_station: bool,
    same_station_min: int,
    cross_station_min: int,
    max_wait: int,
    beam_width: int = 2000,
    candidate_limit: int = 5000,
    search_stats: dict[str, Any] | None = None,
    cross_station_rules: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Search a pre-fetched timetable graph; kept pure for offline verification."""
    unique_graph = list(
        {
            (
                str(leg.get("train_no")),
                str(leg.get("from_station_code")),
                str(leg.get("to_station_code")),
                str(leg.get("departure_at")),
            ): leg
            for leg in graph
        }.values()
    )
    ordered = sorted(unique_graph, key=lambda leg: str(leg["departure_at"]))
    frontier: list[tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]] = []
    for leg in ordered:
        if str(leg["from_station_code"]) not in origin_codes:
            continue
        origin_station = station_by_code.get(str(leg["from_station_code"]))
        destination_station = station_by_code.get(str(leg["to_station_code"]))
        visited = {
            normalize_name(origin_station.city)
            if origin_station
            else str(leg["from_station_code"]),
            normalize_name(destination_station.city)
            if destination_station
            else str(leg["to_station_code"]),
        }
        frontier.append(([leg], [], visited))

    completed: list[dict[str, Any]] = []
    expanded_paths = 0
    pruned_paths = 0
    while frontier:
        path, transfers, visited_cities = frontier.pop(0)
        expanded_paths += 1
        last = path[-1]
        if str(last["to_station_code"]) in destination_codes:
            completed.append(itinerary_from_path(path, transfers))
            if len(completed) >= candidate_limit:
                pruned_paths += len(frontier)
                break
            continue
        if len(path) >= max_transfers + 1:
            continue
        for candidate in ordered:
            if any(
                leg["train_no"] == candidate["train_no"]
                and leg["departure_at"] == candidate["departure_at"]
                for leg in path
            ):
                continue
            destination_station = station_by_code.get(str(candidate["to_station_code"]))
            candidate_city = (
                normalize_name(destination_station.city)
                if destination_station
                else str(candidate["to_station_code"])
            )
            if candidate_city in visited_cities:
                continue
            transfer = transfer_between(
                last,
                candidate,
                station_by_code=station_by_code,
                allow_cross_station=allow_cross_station,
                same_station_min=same_station_min,
                cross_station_min=cross_station_min,
                max_wait=max_wait,
                cross_station_rules=cross_station_rules,
            )
            if not transfer:
                continue
            if len(frontier) >= beam_width:
                pruned_paths += 1
                continue
            frontier.append(
                (
                    [*path, candidate],
                    [*transfers, transfer],
                    {*visited_cities, candidate_city},
                )
            )
    if search_stats is not None:
        search_stats.update(
            {
                "expanded_paths": expanded_paths,
                "pruned_paths": pruned_paths,
                "beam_width": beam_width,
                "candidate_limit": candidate_limit,
                "truncated": pruned_paths > 0,
            }
        )
    return list({str(item["id"]): item for item in completed}.values())


def common_transfer_stops(
    first_train: dict[str, Any],
    first_stops: list[dict[str, Any]],
    second_train: dict[str, Any],
    second_stops: list[dict[str, Any]],
    *,
    excluded_codes: set[str],
    same_station_min: int,
    max_wait: int,
) -> list[dict[str, Any]]:
    """Find ordered, timetable-feasible common stops for a two-train service pair."""
    first_from_no = int(first_train["from_station_no"])
    second_to_no = int(second_train["to_station_no"])
    first_by_code = {
        str(stop["station_code"]): stop
        for stop in first_stops
        if stop.get("station_code")
        and str(stop["station_code"]) not in excluded_codes
        and str(stop.get("station_no", "")).isdigit()
        and int(stop["station_no"]) > first_from_no
        and stop.get("arrival_at")
    }
    candidates: list[dict[str, Any]] = []
    for second in second_stops:
        code = str(second.get("station_code") or "")
        if (
            not code
            or code in excluded_codes
            or code not in first_by_code
            or not str(second.get("station_no", "")).isdigit()
            or int(second["station_no"]) >= second_to_no
            or not second.get("departure_at")
        ):
            continue
        first = first_by_code[code]
        wait = int(
            (
                datetime.fromisoformat(str(second["departure_at"]))
                - datetime.fromisoformat(str(first["arrival_at"]))
            ).total_seconds()
            // 60
        )
        if same_station_min <= wait <= max_wait:
            candidates.append(
                {
                    "station_name": first["station_name"],
                    "station_code": code,
                    "first_arrival_at": first["arrival_at"],
                    "second_departure_at": second["departure_at"],
                    "wait_minutes": wait,
                    "slack_minutes": wait - same_station_min,
                }
            )
    candidates.sort(
        key=lambda item: (
            str(item["first_arrival_at"]),
            str(item["station_name"]),
        )
    )
    return candidates


def command_station(args: argparse.Namespace, session: PublicSession) -> dict[str, Any]:
    stations = load_stations(session, args.refresh)
    matches = station_matches(args.name, stations)
    return {
        "query": args.name,
        "queried_at": now_iso(),
        "source": "12306-public",
        "matches": [asdict(item) for item in matches[: args.limit]],
    }


def command_sale_time(
    args: argparse.Namespace, session: PublicSession
) -> dict[str, Any]:
    payload = session.request_json(
        SALE_TIME_URL,
        data=b"",
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": WWW_HOME,
        },
    )
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    needle = normalize_name(args.station)
    matches = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or normalize_name(str(row.get("station_name", ""))) != needle
        ):
            continue
        sale = str(row.get("sale_time", ""))
        if len(sale) == 4 and sale.isdigit():
            sale = f"{sale[:2]}:{sale[2:]}"
        matches.append({**row, "sale_time": sale})
    return {
        "station": args.station,
        "queried_at": now_iso(),
        "source": "12306-public",
        "matches": matches,
    }


def command_direct(args: argparse.Namespace, session: PublicSession) -> dict[str, Any]:
    datetime.strptime(args.date, "%Y-%m-%d")
    stations = load_stations(session, args.refresh)
    session.initialize_rail()
    trains, origin, destination, origin_scope, destination_scope = query_train_pair(
        session,
        date=args.date,
        from_query=args.from_name,
        to_query=args.to_name,
        stations=stations,
    )
    types = {
        item.strip().upper() for item in args.train_types.split(",") if item.strip()
    }
    if types:
        trains = [item for item in trains if item["train_code"][:1].upper() in types]
    if args.depart_after is not None:
        trains = [
            item
            for item in trains
            if parse_hhmm(item["departure_at"][11:16]) >= args.depart_after
        ]
    if args.depart_before is not None:
        trains = [
            item
            for item in trains
            if parse_hhmm(item["departure_at"][11:16]) < args.depart_before
        ]
    if args.available_only:
        trains = [
            item
            for item in trains
            if any(
                seat["name"] == args.seat and seat["status"] in {"available", "count"}
                for seat in item["seats"]
            )
        ]

    if args.sort == "departure":
        trains.sort(key=lambda item: item["departure_at"])
    elif args.sort == "arrival":
        trains.sort(key=lambda item: item["arrival_at"])
    elif args.sort == "price":
        trains.sort(
            key=lambda item: (
                seat_price(item, args.seat) is None,
                seat_price(item, args.seat) or float("inf"),
                item["duration_minutes"],
            )
        )
    else:
        trains.sort(
            key=lambda item: (
                item["duration_minutes"],
                seat_price(item, args.seat) or float("inf"),
            )
        )
    if args.limit:
        trains = trains[: args.limit]
    for item in trains:
        item["selected_seat"] = next(
            (seat for seat in item["seats"] if seat["name"] == args.seat), None
        )

    return {
        "query": {
            "date": args.date,
            "from": asdict(origin),
            "to": asdict(destination),
            "from_scope": origin_scope,
            "to_scope": destination_scope,
            "train_types": sorted(types),
            "selected_seat": args.seat,
        },
        "queried_at": now_iso(),
        "source": "12306-public",
        "query_path": session.query_path,
        "count": len(trains),
        "trains": trains,
    }


def command_transfer(
    args: argparse.Namespace, session: PublicSession
) -> dict[str, Any]:
    """Explore direct and bounded transfer candidates using public direct queries."""
    travel_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    if args.hub_limit <= 0:
        raise ValueError("--hub-limit must be positive")
    if args.query_budget <= 0:
        raise ValueError("--query-budget must be positive")
    if args.stop_query_budget < 0 or args.refinement_pair_limit < 0:
        raise ValueError("stop-query and refinement limits must be non-negative")
    if args.refinement_candidate_limit < 0:
        raise ValueError("--refinement-candidate-limit must be non-negative")
    if args.beam_width <= 0 or args.candidate_limit <= 0:
        raise ValueError("--beam-width and --candidate-limit must be positive")
    if args.limit < 0:
        raise ValueError("--limit must be non-negative")
    if args.same_station_min < 0 or args.cross_station_min < 0 or args.max_wait <= 0:
        raise ValueError(
            "transfer time limits must be non-negative, and --max-wait must be positive"
        )
    if args.seat_policy == "named" and not args.seats.strip():
        raise ValueError("--seat-policy named requires --seats")
    if args.arrive_before:
        datetime.strptime(args.arrive_before, "%Y-%m-%dT%H:%M")

    stations = load_stations(session, args.refresh)
    origin = resolve_station(args.from_name, stations)
    destination = resolve_station(args.to_name, stations)
    origin_codes = station_codes_for_query(args.from_name, stations)
    destination_codes = station_codes_for_query(args.to_name, stations)
    station_by_code = {station.code: station for station in stations}
    cross_station_rules = load_cross_station_rules(args.cross_station_rules, stations)
    session.initialize_rail()

    requested_hubs = [
        item.strip()
        for item in (args.hubs.split(",") if args.hubs else DEFAULT_TRANSFER_HUBS)
        if item.strip()
    ]
    origin_scope = query_scope(args.from_name, stations)
    destination_scope = query_scope(args.to_name, stations)
    hub_candidates: list[str] = []
    unresolved_hubs: list[str] = []
    for hub in requested_hubs:
        try:
            resolved = resolve_station(hub, stations)
        except ValueError:
            unresolved_hubs.append(hub)
            continue
        if resolved.code in {origin.code, destination.code}:
            continue
        if origin_scope == "city" and normalize_name(resolved.city) == normalize_name(
            origin.city
        ):
            continue
        if destination_scope == "city" and normalize_name(
            resolved.city
        ) == normalize_name(destination.city):
            continue
        if hub not in hub_candidates:
            hub_candidates.append(hub)
    hub_limit_truncated = len(hub_candidates) > args.hub_limit
    hubs = hub_candidates[: args.hub_limit]

    graph: list[dict[str, Any]] = []
    warnings: list[str] = []
    attempted_pairs: list[dict[str, str]] = []
    failed_pairs: list[dict[str, str]] = []
    budget_exhausted = False
    pair_cache: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    def fetch_pair(
        from_query: str, to_query: str, date_value: str
    ) -> list[dict[str, Any]]:
        nonlocal budget_exhausted
        cache_key = (date_value, from_query, to_query)
        if cache_key in pair_cache:
            return pair_cache[cache_key]
        if len(attempted_pairs) >= args.query_budget:
            budget_exhausted = True
            return []
        attempted_pairs.append({"date": date_value, "from": from_query, "to": to_query})
        try:
            trains, *_ = query_train_pair(
                session,
                date=date_value,
                from_query=from_query,
                to_query=to_query,
                stations=stations,
            )
            pair_cache[cache_key] = trains
            return trains
        except Exception as error:
            failed_pairs.append(
                {
                    "date": date_value,
                    "from": from_query,
                    "to": to_query,
                    "error": str(error),
                }
            )
            pair_cache[cache_key] = []
            return []

    first_date = travel_date.isoformat()
    second_date = (travel_date + timedelta(days=1)).isoformat()
    graph.extend(fetch_pair(args.from_name, args.to_name, first_date))

    reachable_from_origin: list[str] = []
    reaching_destination: list[str] = []
    queried_hubs: list[str] = []
    if args.max_transfers >= 1:
        for hub in hubs:
            before_hub = len(attempted_pairs)
            left = fetch_pair(args.from_name, hub, first_date)
            graph.extend(left)
            if left:
                reachable_from_origin.append(hub)
            right_count = 0
            for date_value in (first_date, second_date):
                right = fetch_pair(hub, args.to_name, date_value)
                graph.extend(right)
                right_count += len(right)
            if right_count:
                reaching_destination.append(hub)
            if len(attempted_pairs) > before_hub:
                queried_hubs.append(hub)
            if budget_exhausted:
                break

    middle_pair_total = 0
    middle_pair_attempted = 0
    if args.max_transfers >= 2:
        middle_pairs = [
            (left, right)
            for left in reachable_from_origin
            for right in reaching_destination
            if normalize_name(left) != normalize_name(right)
        ]
        middle_pair_total = len(middle_pairs) * 2
        for left, right in middle_pairs:
            for date_value in (first_date, second_date):
                before = len(attempted_pairs)
                graph.extend(fetch_pair(left, right, date_value))
                if len(attempted_pairs) > before:
                    middle_pair_attempted += 1
                if budget_exhausted:
                    break
            if budget_exhausted:
                break

    named_seats = {item.strip() for item in args.seats.split(",") if item.strip()}
    eligible_graph: list[dict[str, Any]] = []
    for leg in graph:
        departure_minutes = parse_hhmm(str(leg["departure_at"])[11:16])
        if (
            str(leg["from_station_code"]) in origin_codes
            and str(leg["departure_at"])[:10] == first_date
        ):
            if args.depart_after is not None and departure_minutes < args.depart_after:
                continue
            if (
                args.depart_before is not None
                and departure_minutes >= args.depart_before
            ):
                continue
        selected = select_seat(
            leg,
            policy=args.seat_policy,
            named=named_seats,
            available_only=args.available_only,
        )
        if selected is None:
            continue
        item = dict(leg)
        item["selected_seat"] = selected
        eligible_graph.append(item)

    graph_search_stats: dict[str, Any] = {}
    itineraries = search_transfer_graph(
        eligible_graph,
        origin_codes=origin_codes,
        destination_codes=destination_codes,
        station_by_code=station_by_code,
        max_transfers=args.max_transfers,
        allow_cross_station=args.allow_cross_station,
        same_station_min=args.same_station_min,
        cross_station_min=args.cross_station_min,
        max_wait=args.max_wait,
        beam_width=args.beam_width,
        candidate_limit=args.candidate_limit,
        search_stats=graph_search_stats,
        cross_station_rules=cross_station_rules,
    )

    refinement_stats: dict[str, Any] = {
        "enabled": not args.no_stop_refinement and args.max_transfers >= 1,
        "stop_query_budget": args.stop_query_budget,
        "stop_queries": 0,
        "stop_query_failures": 0,
        "train_pairs_considered": 0,
        "common_stop_candidates": 0,
        "refinement_attempts": 0,
        "refinement_successes": 0,
        "refinement_failures": 0,
        "truncated": False,
    }
    queried_stop_keys: set[tuple[str, str, str]] = set()

    def fetch_stops(train: dict[str, Any]) -> list[dict[str, Any]] | None:
        key = (
            str(train["train_no"]),
            str(train["from_station_no"]),
            str(train["departure_at"]),
        )
        if key in queried_stop_keys:
            return query_train_stops(session, train=train, stations=stations)
        if len(queried_stop_keys) >= args.stop_query_budget:
            refinement_stats["truncated"] = True
            return None
        queried_stop_keys.add(key)
        refinement_stats["stop_queries"] += 1
        try:
            return query_train_stops(session, train=train, stations=stations)
        except Exception:
            refinement_stats["stop_query_failures"] += 1
            return None

    def seed_key(item: dict[str, Any]) -> tuple[Any, ...]:
        price = item.get("price_cny_per_person")
        price_key = float(price) if price is not None else float("inf")
        if args.sort == "price":
            return (
                not item["inventory_confirmed"],
                price_key,
                item["duration_minutes"],
            )
        if args.sort in {"duration", "arrival"}:
            return (
                not item["inventory_confirmed"],
                item["duration_minutes"],
                price_key,
            )
        return (
            not item["inventory_confirmed"],
            item["duration_minutes"] + price_key,
            price_key,
        )

    if refinement_stats["enabled"] and args.refinement_pair_limit:
        seen_train_pairs: set[tuple[str, str, str, str]] = set()
        seeds: list[dict[str, Any]] = []
        for itinerary in sorted(itineraries, key=seed_key):
            legs = itinerary.get("legs", [])
            if len(legs) != 2:
                continue
            pair_key = (
                str(legs[0]["train_no"]),
                str(legs[0]["departure_at"]),
                str(legs[1]["train_no"]),
                str(legs[1]["departure_at"]),
            )
            if pair_key in seen_train_pairs:
                continue
            seen_train_pairs.add(pair_key)
            seeds.append(itinerary)
            if len(seeds) >= args.refinement_pair_limit:
                break

        refinement_attempt_limit_hit = False
        for seed in seeds:
            refinement_stats["train_pairs_considered"] += 1
            first, second = seed["legs"]
            first_stops = fetch_stops(first)
            second_stops = fetch_stops(second)
            if first_stops is None or second_stops is None:
                continue
            common = common_transfer_stops(
                first,
                first_stops,
                second,
                second_stops,
                excluded_codes=origin_codes | destination_codes,
                same_station_min=args.same_station_min,
                max_wait=args.max_wait,
            )
            refinement_stats["common_stop_candidates"] += len(common)
            for common_stop in common:
                if (
                    refinement_stats["refinement_attempts"]
                    >= args.refinement_candidate_limit
                ):
                    refinement_attempt_limit_hit = True
                    break
                refinement_stats["refinement_attempts"] += 1
                station_name = str(common_stop["station_name"])
                station_code = str(common_stop["station_code"])
                first_options = fetch_pair(
                    args.from_name,
                    station_name,
                    str(first["departure_at"])[:10],
                )
                second_options = fetch_pair(
                    station_name,
                    args.to_name,
                    str(common_stop["second_departure_at"])[:10],
                )
                if budget_exhausted:
                    refinement_stats["truncated"] = True
                    refinement_attempt_limit_hit = True
                    break
                refined_first = next(
                    (
                        item
                        for item in first_options
                        if item["train_no"] == first["train_no"]
                        and str(item["to_station_code"]) == station_code
                        and str(item["from_station_code"]) in origin_codes
                    ),
                    None,
                )
                refined_second = next(
                    (
                        item
                        for item in second_options
                        if item["train_no"] == second["train_no"]
                        and str(item["from_station_code"]) == station_code
                        and str(item["to_station_code"]) in destination_codes
                    ),
                    None,
                )
                refined_legs: list[dict[str, Any]] = []
                for leg in (refined_first, refined_second):
                    if leg is None:
                        break
                    selected = select_seat(
                        leg,
                        policy=args.seat_policy,
                        named=named_seats,
                        available_only=args.available_only,
                    )
                    if selected is None:
                        break
                    refined_legs.append({**leg, "selected_seat": selected})
                if len(refined_legs) != 2:
                    refinement_stats["refinement_failures"] += 1
                    continue
                transfer = transfer_between(
                    refined_legs[0],
                    refined_legs[1],
                    station_by_code=station_by_code,
                    allow_cross_station=False,
                    same_station_min=args.same_station_min,
                    cross_station_min=args.cross_station_min,
                    max_wait=args.max_wait,
                )
                if transfer is None:
                    refinement_stats["refinement_failures"] += 1
                    continue
                itineraries.append(itinerary_from_path(refined_legs, [transfer]))
                refinement_stats["refinement_successes"] += 1
            if refinement_attempt_limit_hit:
                refinement_stats["truncated"] = True
                break

    itineraries = list({str(item["id"]): item for item in itineraries}.values())
    if args.arrive_before:
        deadline = datetime.fromisoformat(f"{args.arrive_before}:00+08:00")
        itineraries = [
            item
            for item in itineraries
            if datetime.fromisoformat(item["arrival_at"]) <= deadline
        ]

    if args.sort == "price":
        itineraries.sort(
            key=lambda item: (
                not item["executable"],
                not item["price_complete"],
                item["price_cny_per_person"]
                if item["price_cny_per_person"] is not None
                else float("inf"),
                item["duration_minutes"],
            )
        )
    elif args.sort == "arrival":
        itineraries.sort(
            key=lambda item: (
                not item["executable"],
                item["arrival_at"],
                item["duration_minutes"],
            )
        )
    elif args.sort == "departure":
        itineraries.sort(
            key=lambda item: (
                not item["executable"],
                item["departure_at"],
                item["arrival_at"],
            )
        )
    elif args.sort == "balanced":
        itineraries.sort(
            key=lambda item: (
                not item["executable"],
                item["duration_minutes"]
                + 45 * item["transfer_count"]
                + (0 if item["inventory_confirmed"] else 120)
                + (0 if item["price_complete"] else 120),
                item["price_cny_per_person"]
                if item["price_cny_per_person"] is not None
                else float("inf"),
            )
        )
    else:
        itineraries.sort(
            key=lambda item: (
                not item["executable"],
                item["duration_minutes"],
                item["transfer_count"],
                item["price_cny_per_person"]
                if item["price_cny_per_person"] is not None
                else float("inf"),
            )
        )

    total_candidates = len(itineraries)
    if args.limit:
        itineraries = itineraries[: args.limit]
    middle_truncated = (
        args.max_transfers >= 2 and middle_pair_attempted < middle_pair_total
    )
    hub_query_truncated = args.max_transfers >= 1 and len(queried_hubs) < len(hubs)
    coverage_truncated = (
        budget_exhausted
        or hub_limit_truncated
        or hub_query_truncated
        or middle_truncated
        or bool(failed_pairs)
        or bool(graph_search_stats.get("truncated"))
        or bool(refinement_stats.get("truncated"))
    )
    if failed_pairs:
        warnings.append(
            f"有 {len(failed_pairs)} 个铁路区段查询失败，当前结果未覆盖原定查询范围"
        )
    if args.max_transfers >= 1:
        warnings.append(
            "换乘方案的查询范围受枢纽列表和查询次数限制；相关结论只能表述为“在已查询方案中费用最低”或“在已查询方案中用时最短”，不能视为所有车次中的最优结果"
        )
    if args.allow_cross_station and not cross_station_rules:
        warnings.append("未提供经过核实的跨站接驳数据，已排除所有跨站换乘方案")
    if any(not item["inventory_confirmed"] for item in itineraries):
        warnings.append("部分方案只有票价信息或当前无票，出行前需重新查询余票")

    return {
        "query": {
            "date": first_date,
            "from": asdict(origin),
            "to": asdict(destination),
            "max_transfers": args.max_transfers,
            "allow_cross_station": args.allow_cross_station,
            "seat_policy": args.seat_policy,
            "named_seats": sorted(named_seats),
            "available_only": args.available_only,
        },
        "queried_at": now_iso(),
        "source": "12306-public",
        "search_coverage": {
            "strategy": "bounded_hub_graph_with_stop_refinement",
            "complete": args.max_transfers == 0 and not coverage_truncated,
            "minimum_claim": (
                "direct_query_complete"
                if args.max_transfers == 0 and not coverage_truncated
                else "lowest_among_searched_candidates"
            ),
            "max_transfers": args.max_transfers,
            "hubs_considered": hubs,
            "hubs_queried": queried_hubs,
            "unresolved_hubs": unresolved_hubs,
            "reachable_from_origin": reachable_from_origin,
            "reaching_destination": reaching_destination,
            "explored_pair_queries": len(attempted_pairs),
            "failed_pair_queries": len(failed_pairs),
            "query_budget": args.query_budget,
            "budget_exhausted": budget_exhausted,
            "truncated": coverage_truncated,
            "candidate_count_before_limit": total_candidates,
            "graph_search": graph_search_stats,
            "stop_refinement": refinement_stats,
        },
        "warnings": warnings,
        "count": len(itineraries),
        "itineraries": itineraries,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--retries", type=int, default=2)
    sub = parser.add_subparsers(dest="command", required=True)

    station = sub.add_parser("station", help="Resolve a station or city name")
    station.add_argument("--name", required=True)
    station.add_argument("--limit", type=int, default=30)
    station.add_argument("--refresh", action="store_true")

    sale = sub.add_parser(
        "sale-time", help="Query the official station ticket sale time"
    )
    sale.add_argument("--station", required=True)

    direct = sub.add_parser(
        "direct", help="Query direct trains, fares and availability"
    )
    direct.add_argument("--date", required=True)
    direct.add_argument("--from", dest="from_name", required=True)
    direct.add_argument("--to", dest="to_name", required=True)
    direct.add_argument("--train-types", default="")
    direct.add_argument("--depart-after", type=parse_hhmm)
    direct.add_argument("--depart-before", type=parse_hhmm)
    direct.add_argument("--seat", default="二等座")
    direct.add_argument("--available-only", action="store_true")
    direct.add_argument(
        "--sort",
        choices=["duration", "departure", "arrival", "price"],
        default="duration",
    )
    direct.add_argument("--limit", type=int, default=20)
    direct.add_argument("--refresh", action="store_true")

    transfer = sub.add_parser(
        "transfer",
        help="Search direct and bounded one/two-transfer itineraries",
    )
    transfer.add_argument("--date", required=True)
    transfer.add_argument("--from", dest="from_name", required=True)
    transfer.add_argument("--to", dest="to_name", required=True)
    transfer.add_argument("--max-transfers", type=int, choices=[0, 1, 2], default=1)
    transfer.add_argument("--allow-cross-station", action="store_true")
    transfer.add_argument(
        "--cross-station-rules",
        type=Path,
        help=(
            "JSON file of verified cross-station ground connections; "
            "--allow-cross-station alone never assumes feasibility"
        ),
    )
    transfer.add_argument("--same-station-min", type=int, default=30)
    transfer.add_argument(
        "--cross-station-min",
        type=int,
        default=30,
        help="Extra buffer after a verified cross-station ground connection",
    )
    transfer.add_argument(
        "--max-wait", type=int, default=720, help="Maximum transfer wait in minutes"
    )
    transfer.add_argument(
        "--hubs",
        default="",
        help=(
            "Comma-separated hub city/station list replacing the built-in bounded list"
        ),
    )
    transfer.add_argument("--hub-limit", type=int, default=len(DEFAULT_TRANSFER_HUBS))
    transfer.add_argument("--query-budget", type=int, default=120)
    transfer.add_argument("--stop-query-budget", type=int, default=16)
    transfer.add_argument("--refinement-pair-limit", type=int, default=8)
    transfer.add_argument("--refinement-candidate-limit", type=int, default=16)
    transfer.add_argument(
        "--no-stop-refinement",
        action="store_true",
        help="Disable common-stop discovery and segment repricing",
    )
    transfer.add_argument("--beam-width", type=int, default=2000)
    transfer.add_argument("--candidate-limit", type=int, default=5000)
    transfer.add_argument("--depart-after", type=parse_hhmm)
    transfer.add_argument("--depart-before", type=parse_hhmm)
    transfer.add_argument(
        "--arrive-before",
        help="Latest arrival as YYYY-MM-DDTHH:MM in China Standard Time",
    )
    transfer.add_argument(
        "--seat-policy", choices=SEAT_POLICIES, default="cheapest-available"
    )
    transfer.add_argument(
        "--seats", default="", help="Comma-separated seat names for policy=named"
    )
    transfer.add_argument("--available-only", action="store_true")
    transfer.add_argument(
        "--sort",
        choices=["balanced", "duration", "price", "departure", "arrival"],
        default="balanced",
    )
    transfer.add_argument("--limit", type=int, default=10)
    transfer.add_argument("--refresh", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.timeout <= 0:
            raise ValueError("--timeout must be positive")
        if args.retries < 0:
            raise ValueError("--retries must be non-negative")
        if hasattr(args, "limit") and args.limit < 0:
            raise ValueError("--limit must be non-negative")
        session = PublicSession(timeout=args.timeout, retries=args.retries)
        if args.command == "station":
            result = command_station(args, session)
        elif args.command == "sale-time":
            result = command_sale_time(args, session)
        elif args.command == "transfer":
            result = command_transfer(args, session)
        else:
            result = command_direct(args, session)
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
