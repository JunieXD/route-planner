#!/usr/bin/env python3
"""Filter and rank normalized door-to-door route candidates."""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

OPTIMIZATIONS = ("balanced", "fastest", "cheapest", "fewest-transfers")
VEHICLE_MODES = {"rail", "metro", "bus", "taxi", "ferry", "flight"}
LOCAL_CONNECTION_MODES = {"walk", "metro", "bus", "taxi"}


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def near_service_boundary(value: Any) -> bool:
    instant = iso_datetime(value)
    if instant is None:
        return False
    minute = instant.hour * 60 + instant.minute
    return minute < 6 * 60 + 30 or minute >= 22 * 60 + 30


def is_executable(route: dict[str, Any]) -> bool:
    return route.get("executable", route.get("inventory_confirmed", True)) is True


def apply_service_window_gate(route: dict[str, Any]) -> None:
    legs = route.get("legs") if isinstance(route.get("legs"), list) else []
    boundary_local_service = any(
        isinstance(leg, dict)
        and str(leg.get("mode")) in {"metro", "bus"}
        and (
            near_service_boundary(leg.get("departure_at"))
            or near_service_boundary(leg.get("arrival_at"))
        )
        for leg in legs
    )
    verified = route.get("service_window_verified") is True
    route["arrival_estimate_confirmed"] = not boundary_local_service or verified
    if not boundary_local_service or verified:
        route.setdefault("service_window_status", "verified_or_not_boundary_sensitive")
        return
    route["service_window_status"] = "unverified_boundary"
    arrival = route.get("estimated_arrival_at")
    if arrival:
        route.setdefault("earliest_if_service_available", arrival)
        route["estimated_arrival_at"] = None
    warnings = route.setdefault("warnings", [])
    if isinstance(warnings, list) and "首末班运营时段待确认" not in warnings:
        warnings.append("首末班运营时段待确认")
    route["time_complete"] = False


def apply_duration_breakdown(route: dict[str, Any]) -> None:
    legs = route.get("legs") if isinstance(route.get("legs"), list) else []
    scheduled = [
        leg
        for leg in legs
        if isinstance(leg, dict)
        and (leg.get("scheduled") is True or leg.get("mode") == "rail")
        and iso_datetime(leg.get("departure_at"))
        and iso_datetime(leg.get("arrival_at"))
    ]
    if scheduled:
        first_departure = min(iso_datetime(leg["departure_at"]) for leg in scheduled)
        last_arrival = max(iso_datetime(leg["arrival_at"]) for leg in scheduled)
        scheduled_span = int((last_arrival - first_departure).total_seconds() // 60)
    else:
        scheduled_span = number(route.get("scheduled_span_minutes"))

    def summed_duration(modes: set[str]) -> int:
        return sum(
            int(value)
            for leg in legs
            if isinstance(leg, dict) and str(leg.get("mode")) in modes
            if (value := number(leg.get("duration_minutes"))) is not None
        )

    in_vehicle = summed_duration(VEHICLE_MODES)
    local_connection = summed_duration(LOCAL_CONNECTION_MODES)
    checkin_buffer = sum(
        int(value)
        for leg in legs
        if isinstance(leg, dict)
        and leg.get("mode") == "buffer"
        and str(leg.get("buffer_type", "checkin")) == "checkin"
        if (value := number(leg.get("duration_minutes"))) is not None
    )
    waiting = sum(
        int(value)
        for leg in legs
        if isinstance(leg, dict) and leg.get("mode") in {"dwell", "wait"}
        if (value := number(leg.get("duration_minutes"))) is not None
    )
    waiting += sum(
        int(value)
        for leg in legs
        if isinstance(leg, dict)
        and leg.get("mode") == "buffer"
        and str(leg.get("buffer_type", "checkin")) != "checkin"
        if (value := number(leg.get("duration_minutes"))) is not None
    )

    leave = iso_datetime(route.get("recommended_leave_at"))
    arrival = iso_datetime(route.get("estimated_arrival_at"))
    if arrival is None:
        arrival = iso_datetime(route.get("earliest_if_service_available"))
    door_to_door = (
        int((arrival - leave).total_seconds() // 60)
        if leave is not None and arrival is not None and arrival >= leave
        else None
    )
    if door_to_door is None and route.get("time_complete") is True:
        legacy_duration = number(route.get("duration_minutes"))
        door_to_door = int(legacy_duration) if legacy_duration is not None else None

    unknown_origin = bool(route.get("unknown_origin_connection", leave is None))
    route["door_to_door_duration_minutes"] = door_to_door
    route["scheduled_span_minutes"] = (
        int(scheduled_span) if scheduled_span is not None else None
    )
    route["in_vehicle_minutes"] = int(
        number(route.get("in_vehicle_minutes")) or in_vehicle
    )
    route["waiting_minutes"] = int(number(route.get("waiting_minutes")) or waiting)
    route["checkin_buffer_minutes"] = int(
        number(route.get("checkin_buffer_minutes")) or checkin_buffer
    )
    route["local_connection_minutes"] = int(
        number(route.get("local_connection_minutes")) or local_connection
    )
    route["unknown_origin_connection"] = unknown_origin
    route["duration_complete"] = bool(
        route.get("time_complete") is True
        and not route.get("unmodeled_legs")
        and door_to_door is not None
        and route.get("arrival_estimate_confirmed") is not False
    )


def read_routes(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("routes")
    if not isinstance(payload, list):
        raise ValueError(
            "input JSON must be a route array or an object containing a routes array"
        )
    routes: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"route {index} is not an object")
        route = copy.deepcopy(item)
        route.setdefault("id", f"route-{index}")
        duration = number(route.get("duration_minutes"))
        transfers = number(route.get("transfer_count"))
        risk = number(route.get("risk_score", 0))
        price = number(route.get("price_cny_per_person"))
        price_complete = route.get("price_complete", price is not None)
        time_complete = route.get("time_complete", True)
        if not isinstance(price_complete, bool) or not isinstance(time_complete, bool):
            raise ValueError(
                f"route {route['id']} completeness fields must be booleans"
            )
        if duration is None or duration < 0:
            raise ValueError(f"route {route['id']} has invalid duration_minutes")
        if transfers is None or transfers < 0 or not transfers.is_integer():
            raise ValueError(f"route {route['id']} has invalid transfer_count")
        if risk is None or not 0 <= risk <= 1:
            raise ValueError(f"route {route['id']} has risk_score outside [0, 1]")
        if price is not None and price < 0:
            raise ValueError(f"route {route['id']} has a negative price_cny_per_person")
        if price_complete and price is None:
            raise ValueError(f"route {route['id']} marks an unknown price as complete")
        for field in ("unpriced_legs", "unmodeled_legs"):
            value = route.get(field, [])
            if not isinstance(value, list):
                raise ValueError(f"route {route['id']} field {field} must be an array")
            route[field] = value
        apply_service_window_gate(route)
        time_complete = route.get("time_complete", time_complete)
        apply_duration_breakdown(route)
        if price_complete and route["unpriced_legs"]:
            raise ValueError(
                f"route {route['id']} has unpriced legs but marks price complete"
            )
        if time_complete and route["unmodeled_legs"]:
            raise ValueError(
                f"route {route['id']} has unmodeled legs but marks time complete"
            )
        route["duration_minutes"] = int(duration) if duration.is_integer() else duration
        route["transfer_count"] = int(transfers)
        route["risk_score"] = risk
        route["price_cny_per_person"] = price
        route["price_complete"] = price_complete
        route["time_complete"] = time_complete
        inventory_confirmed = route.get("inventory_confirmed", True) is True
        route["inventory_confirmed"] = inventory_confirmed
        route["executable"] = (
            bool(route.get("executable", True)) and inventory_confirmed
        )
        route.setdefault(
            "inventory_status",
            "available" if inventory_confirmed else "quoted_or_unavailable",
        )
        route.setdefault(
            "connection_reliability",
            "high_risk"
            if risk >= 0.65
            else "medium_risk"
            if risk >= 0.3
            else "low_risk",
        )
        route.setdefault("transfer_burden", "not_assessed")
        route.setdefault("overnight_seated_minutes", 0)
        routes.append(route)
    return routes


def filter_reasons(route: dict[str, Any], args: argparse.Namespace) -> list[str]:
    reasons: list[str] = []
    duration = route["duration_minutes"]
    transfers = route["transfer_count"]
    price = route["price_cny_per_person"]
    total = None if price is None else price * args.party_size
    if args.max_duration_minutes is not None:
        if not route["time_complete"] or route["unmodeled_legs"]:
            reasons.append("incomplete_time_for_duration_limit")
        elif duration > args.max_duration_minutes:
            reasons.append("exceeds_max_duration")
    if args.max_transfers is not None:
        if route["unmodeled_legs"]:
            reasons.append("incomplete_route_for_transfer_limit")
        elif transfers > args.max_transfers:
            reasons.append("exceeds_max_transfers")
    if args.max_price_per_person is not None:
        if price is None:
            reasons.append("unknown_price_for_budget")
        elif not route["price_complete"]:
            reasons.append("incomplete_price_for_budget")
        elif price > args.max_price_per_person:
            reasons.append("exceeds_per_person_budget")
    if args.max_total_price is not None:
        if total is None:
            reasons.append("unknown_price_for_budget")
        elif not route["price_complete"]:
            reasons.append("incomplete_price_for_budget")
        elif total > args.max_total_price:
            reasons.append("exceeds_total_budget")
    return list(dict.fromkeys(reasons))


def normalized(value: float, values: list[float]) -> float:
    low, high = min(values), max(values)
    return 0.0 if high == low else (value - low) / (high - low)


def add_balanced_scores(routes: list[dict[str, Any]]) -> None:
    durations = [float(route["duration_minutes"]) for route in routes]
    transfers = [float(route["transfer_count"]) for route in routes]
    risks = [float(route["risk_score"]) for route in routes]
    known_prices = [
        float(route["price_cny_per_person"])
        for route in routes
        if route["price_complete"]
        and route["price_cny_per_person"] is not None
        and is_executable(route)
    ]
    for route in routes:
        price = route["price_cny_per_person"]
        price_score = (
            normalized(float(price), known_prices)
            if route["price_complete"] and price is not None and known_prices
            else 1.1
        )
        score = (
            0.4 * normalized(float(route["duration_minutes"]), durations)
            + 0.3 * price_score
            + 0.2 * normalized(float(route["transfer_count"]), transfers)
            + 0.1 * normalized(float(route["risk_score"]), risks)
            + (0.2 if not route["price_complete"] else 0)
            + (0.2 if not route["time_complete"] or route["unmodeled_legs"] else 0)
            + (0.8 if not is_executable(route) else 0)
        )
        route["_balanced_score"] = round(score, 6)


def dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if (
        not left["price_complete"]
        or not right["price_complete"]
        or not left["time_complete"]
        or not right["time_complete"]
        or left["unmodeled_legs"]
        or right["unmodeled_legs"]
        or left["price_cny_per_person"] is None
        or right["price_cny_per_person"] is None
        or not is_executable(left)
        or not is_executable(right)
    ):
        return False
    left_values = (
        float(left["duration_minutes"]),
        float(left["price_cny_per_person"]),
        float(left["transfer_count"]),
        float(left["risk_score"]),
    )
    right_values = (
        float(right["duration_minutes"]),
        float(right["price_cny_per_person"]),
        float(right["transfer_count"]),
        float(right["risk_score"]),
    )
    return all(a <= b for a, b in zip(left_values, right_values, strict=True)) and any(
        a < b for a, b in zip(left_values, right_values, strict=True)
    )


def pareto_ids(routes: list[dict[str, Any]]) -> set[str]:
    priced = [
        route
        for route in routes
        if route["price_complete"]
        and route["time_complete"]
        and not route["unmodeled_legs"]
        and route["price_cny_per_person"] is not None
        and is_executable(route)
    ]
    return {
        str(candidate["id"])
        for candidate in priced
        if not any(
            dominates(other, candidate) for other in priced if other is not candidate
        )
    }


def sort_key(route: dict[str, Any], optimize: str) -> tuple[Any, ...]:
    duration = route["duration_minutes"]
    price = route["price_cny_per_person"]
    transfers = route["transfer_count"]
    risk = route["risk_score"]
    price_key = (
        not route["price_complete"],
        price is None,
        price if price is not None else math.inf,
    )
    time_key = (not route["time_complete"] or bool(route["unmodeled_legs"]), duration)
    if optimize == "fastest":
        return not is_executable(route), *time_key, transfers, risk, *price_key
    if optimize == "cheapest":
        return not is_executable(route), *price_key, duration, transfers, risk
    if optimize == "fewest-transfers":
        return (
            not is_executable(route),
            not route["time_complete"] or bool(route["unmodeled_legs"]),
            transfers,
            duration,
            risk,
            *price_key,
        )
    return (
        not is_executable(route),
        route["_balanced_score"],
        duration,
        transfers,
        *price_key,
        risk,
    )


def label_routes(routes: list[dict[str, Any]], pareto: set[str], optimize: str) -> None:
    time_complete = [
        route
        for route in routes
        if route["time_complete"]
        and not route["unmodeled_legs"]
        and is_executable(route)
    ]
    min_duration = (
        min(float(route["duration_minutes"]) for route in time_complete)
        if time_complete
        else None
    )
    min_transfers = (
        min(int(route["transfer_count"]) for route in time_complete)
        if time_complete
        else None
    )
    priced = [
        route
        for route in routes
        if route["price_complete"]
        and route["price_cny_per_person"] is not None
        and is_executable(route)
    ]
    min_price = (
        min(float(route["price_cny_per_person"]) for route in priced)
        if priced
        else None
    )
    for rank, route in enumerate(routes, start=1):
        labels: list[str] = []
        if (
            min_duration is not None
            and route in time_complete
            and float(route["duration_minutes"]) == min_duration
        ):
            labels.append("fastest")
        if (
            min_price is not None
            and is_executable(route)
            and route["price_complete"]
            and route["price_cny_per_person"] == min_price
        ):
            labels.append("cheapest")
        if (
            min_transfers is not None
            and route in time_complete
            and int(route["transfer_count"]) == min_transfers
        ):
            labels.append("fewest-transfers")
        if str(route["id"]) in pareto:
            labels.append("pareto")
        route["ranking"] = {
            "rank": rank,
            "optimized_for": optimize,
            "labels": labels,
            "price_complete": route["price_complete"],
            "time_complete": route["time_complete"],
            "unpriced_legs": route["unpriced_legs"],
            "unmodeled_legs": route["unmodeled_legs"],
            "executable": is_executable(route),
            "inventory_status": route.get("inventory_status", "available"),
            "duration_complete": route.get("duration_complete", route["time_complete"]),
        }
        if optimize == "balanced":
            route["ranking"]["balanced_score"] = route["_balanced_score"]
        route.pop("_balanced_score", None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input", type=Path, help="JSON file matching references/route-schema.md"
    )
    parser.add_argument("--optimize", choices=OPTIMIZATIONS, default="balanced")
    parser.add_argument("--party-size", type=int, default=1)
    parser.add_argument("--max-duration-minutes", type=float)
    parser.add_argument("--max-price-per-person", type=float)
    parser.add_argument("--max-total-price", type=float)
    parser.add_argument("--max-transfers", type=int)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--include-filtered", action="store_true")
    parser.add_argument("--indent", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.party_size <= 0:
            raise ValueError("--party-size must be positive")
        if args.limit < 0:
            raise ValueError("--limit must be non-negative")
        for flag in (
            "max_duration_minutes",
            "max_price_per_person",
            "max_total_price",
            "max_transfers",
        ):
            value = getattr(args, flag)
            if value is not None and value < 0:
                raise ValueError(f"--{flag.replace('_', '-')} must be non-negative")

        source_routes = read_routes(args.input)
        routes: list[dict[str, Any]] = []
        filtered: list[dict[str, Any]] = []
        for route in source_routes:
            price = route["price_cny_per_person"]
            computed_total = (
                None if price is None else round(price * args.party_size, 2)
            )
            route["price_cny_total"] = (
                computed_total if route["price_complete"] else None
            )
            route["known_price_cny_total"] = computed_total
            reasons = filter_reasons(route, args)
            if reasons:
                filtered.append({"id": route["id"], "reasons": reasons})
            else:
                routes.append(route)

        if routes:
            add_balanced_scores(routes)
            pareto = pareto_ids(routes)
            routes.sort(key=lambda item: sort_key(item, args.optimize))
            label_routes(routes, pareto, args.optimize)
        total_eligible = len(routes)
        if args.limit:
            routes = routes[: args.limit]
        result: dict[str, Any] = {
            "optimize": args.optimize,
            "party_size": args.party_size,
            "input_count": len(source_routes),
            "eligible_count": total_eligible,
            "returned_count": len(routes),
            "routes": routes,
        }
        if args.include_filtered:
            result["filtered"] = filtered
        print(json.dumps(result, ensure_ascii=False, indent=args.indent))
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
