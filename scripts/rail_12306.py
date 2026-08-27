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
CACHE_PATH = Path(tempfile.gettempdir()) / "codex-china-route-planner" / "stations.json"
CHINA_TZ = timezone(timedelta(hours=8))


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
                city=(fields[7].strip() if len(fields) > 7 and fields[7].strip() else fields[1].strip()),
            )
        )
    if not stations:
        raise UpstreamError("official station script contained no stations")
    return stations


def download_stations(session: PublicSession) -> list[Station]:
    html = session.request_text(WWW_HOME, headers={"Accept": "text/html,application/xhtml+xml"})
    sources = re.findall(r"<script[^>]+src=['\"]([^'\"]*station_name[^'\"]+\.js(?:\?[^'\"]*)?)['\"]", html, re.I)
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
    if not refresh and CACHE_PATH.exists() and time.time() - CACHE_PATH.stat().st_mtime < 86400:
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            return [Station(**item) for item in data]
        except (OSError, TypeError, json.JSONDecodeError):
            pass
    stations = download_stations(session)
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps([asdict(item) for item in stations], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return stations


def station_matches(query: str, stations: list[Station]) -> list[Station]:
    if re.fullmatch(r"[A-Z]{3}", query.strip()):
        return [station for station in stations if station.code == query.strip()]
    needle = normalize_name(query)
    exact_name = [station for station in stations if normalize_name(station.name) == needle]
    if exact_name:
        return exact_name
    city = [station for station in stations if normalize_name(station.city) == needle]
    if city:
        return sorted(city, key=lambda item: (item.name != item.city, len(item.name), item.name))
    fuzzy = [
        station
        for station in stations
        if needle in normalize_name(station.name)
        or needle == station.pinyin.lower()
        or needle == station.short.lower()
    ]
    return sorted(fuzzy, key=lambda item: (normalize_name(item.name) != needle, len(item.name), item.name))


def resolve_station(query: str, stations: list[Station]) -> Station:
    matches = station_matches(query, stations)
    if not matches:
        raise ValueError(f"station or city not found: {query}")
    needle = normalize_name(query)
    exact = [item for item in matches if normalize_name(item.name) == needle]
    if len(exact) == 1:
        return exact[0]
    city_representative = [item for item in matches if normalize_name(item.name) == normalize_name(item.city) == needle]
    if city_representative:
        return city_representative[0]
    if len(matches) == 1:
        return matches[0]
    names = "、".join(item.name for item in matches[:8])
    raise ValueError(f"ambiguous station '{query}'; candidates: {names}")


def query_scope(query: str, stations: list[Station]) -> str:
    """Treat a city name as city-wide even when it is also a station name."""
    needle = normalize_name(query)
    return "city" if any(normalize_name(item.city) == needle for item in stations) else "station"


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
        raw_availability = fields[availability_field] if len(fields) > availability_field else ""
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
        messages = payload.get("messages") or payload.get("message") or "missing data.result"
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


def command_station(args: argparse.Namespace, session: PublicSession) -> dict[str, Any]:
    stations = load_stations(session, args.refresh)
    matches = station_matches(args.name, stations)
    return {
        "query": args.name,
        "queried_at": now_iso(),
        "source": "12306-public",
        "matches": [asdict(item) for item in matches[: args.limit]],
    }


def command_sale_time(args: argparse.Namespace, session: PublicSession) -> dict[str, Any]:
    payload = session.request_json(
        SALE_TIME_URL,
        data=b"",
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "Referer": WWW_HOME},
    )
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    needle = normalize_name(args.station)
    matches = []
    for row in rows:
        if not isinstance(row, dict) or normalize_name(str(row.get("station_name", ""))) != needle:
            continue
        sale = str(row.get("sale_time", ""))
        if len(sale) == 4 and sale.isdigit():
            sale = f"{sale[:2]}:{sale[2:]}"
        matches.append({**row, "sale_time": sale})
    return {"station": args.station, "queried_at": now_iso(), "source": "12306-public", "matches": matches}


def command_direct(args: argparse.Namespace, session: PublicSession) -> dict[str, Any]:
    datetime.strptime(args.date, "%Y-%m-%d")
    stations = load_stations(session, args.refresh)
    origin = resolve_station(args.from_name, stations)
    destination = resolve_station(args.to_name, stations)
    origin_scope = query_scope(args.from_name, stations)
    destination_scope = query_scope(args.to_name, stations)
    session.initialize_rail()
    params = urlencode(
        {
            "leftTicketDTO.train_date": args.date,
            "leftTicketDTO.from_station": origin.code,
            "leftTicketDTO.to_station": destination.code,
            "purpose_codes": "ADULT",
        }
    )
    payload = session.request_json(
        f"{KYFW_BASE}{session.query_path}?{params}",
        headers={"Referer": f"{KYFW_BASE}/otn/leftTicket/init"},
    )
    trains = parse_train_rows(payload, args.date)
    # The official endpoint expands a city telecode to sibling stations. Preserve
    # that behavior for city input, but never silently move a user who named an
    # exact station to another station in the same city.
    if origin_scope == "station":
        trains = [item for item in trains if item["from_station_code"] == origin.code]
    if destination_scope == "station":
        trains = [item for item in trains if item["to_station_code"] == destination.code]
    types = {item.strip().upper() for item in args.train_types.split(",") if item.strip()}
    if types:
        trains = [item for item in trains if item["train_code"][:1].upper() in types]
    if args.depart_after is not None:
        trains = [item for item in trains if parse_hhmm(item["departure_at"][11:16]) >= args.depart_after]
    if args.depart_before is not None:
        trains = [item for item in trains if parse_hhmm(item["departure_at"][11:16]) < args.depart_before]
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
        trains.sort(key=lambda item: (seat_price(item, args.seat) is None, seat_price(item, args.seat) or float("inf"), item["duration_minutes"]))
    else:
        trains.sort(key=lambda item: (item["duration_minutes"], seat_price(item, args.seat) or float("inf")))
    if args.limit:
        trains = trains[: args.limit]
    for item in trains:
        item["selected_seat"] = next((seat for seat in item["seats"] if seat["name"] == args.seat), None)

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--retries", type=int, default=2)
    sub = parser.add_subparsers(dest="command", required=True)

    station = sub.add_parser("station", help="Resolve a station or city name")
    station.add_argument("--name", required=True)
    station.add_argument("--limit", type=int, default=30)
    station.add_argument("--refresh", action="store_true")

    sale = sub.add_parser("sale-time", help="Query the official station ticket sale time")
    sale.add_argument("--station", required=True)

    direct = sub.add_parser("direct", help="Query direct trains, fares and availability")
    direct.add_argument("--date", required=True)
    direct.add_argument("--from", dest="from_name", required=True)
    direct.add_argument("--to", dest="to_name", required=True)
    direct.add_argument("--train-types", default="")
    direct.add_argument("--depart-after", type=parse_hhmm)
    direct.add_argument("--depart-before", type=parse_hhmm)
    direct.add_argument("--seat", default="二等座")
    direct.add_argument("--available-only", action="store_true")
    direct.add_argument("--sort", choices=["duration", "departure", "arrival", "price"], default="duration")
    direct.add_argument("--limit", type=int, default=20)
    direct.add_argument("--refresh", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    session = PublicSession(timeout=args.timeout, retries=max(0, args.retries))
    try:
        if args.command == "station":
            result = command_station(args, session)
        elif args.command == "sale-time":
            result = command_sale_time(args, session)
        else:
            result = command_direct(args, session)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error), "type": type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
