#!/usr/bin/env python3
"""Filter and rank normalized door-to-door route candidates."""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any

OPTIMIZATIONS = ("balanced", "fastest", "cheapest", "fewest-transfers")


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


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
        if route["price_complete"] and route["price_cny_per_person"] is not None
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
        return *time_key, transfers, risk, *price_key
    if optimize == "cheapest":
        return *price_key, duration, transfers, risk
    if optimize == "fewest-transfers":
        return (
            not route["time_complete"] or bool(route["unmodeled_legs"]),
            transfers,
            duration,
            risk,
            *price_key,
        )
    return route["_balanced_score"], duration, transfers, *price_key, risk


def label_routes(routes: list[dict[str, Any]], pareto: set[str], optimize: str) -> None:
    time_complete = [
        route
        for route in routes
        if route["time_complete"] and not route["unmodeled_legs"]
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
        if route["price_complete"] and route["price_cny_per_person"] is not None
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
