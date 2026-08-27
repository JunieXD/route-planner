# Normalized route schema

Read this file when combining multiple providers, ordered via points, party pricing, or more than two transport legs.

## Itinerary

```json
{
  "id": "stable-local-id",
  "label": "optional human label",
  "recommended_leave_at": "2026-09-11T08:03:00+08:00",
  "primary_service_departure_at": "2026-09-11T09:13:00+08:00",
  "estimated_arrival_at": "2026-09-11T10:42:00+08:00",
  "arrival_window": {
    "earliest": "2026-09-11T10:37:00+08:00",
    "latest": "2026-09-11T10:50:00+08:00"
  },
  "duration_minutes": 132,
  "door_to_door_duration_minutes": 132,
  "scheduled_span_minutes": 52,
  "in_vehicle_minutes": 79,
  "waiting_minutes": 11,
  "checkin_buffer_minutes": 30,
  "local_connection_minutes": 27,
  "unknown_origin_connection": false,
  "duration_complete": true,
  "price_cny_per_person": 81,
  "price_complete": true,
  "time_complete": true,
  "unpriced_legs": [],
  "unmodeled_legs": [],
  "transfer_count": 1,
  "walking_minutes": 18,
  "walking_distance_meters": 1200,
  "wait_and_buffer_minutes": 41,
  "minimum_connection_slack_minutes": 26,
  "risk_score": 0.15,
  "connection_reliability": "low_risk",
  "transfer_burden": "medium",
  "overnight_seated_minutes": 0,
  "inventory_confirmed": true,
  "inventory_status": "available",
  "executable": true,
  "service_window_verified": false,
  "search_coverage": {
    "strategy": "bounded_hub_graph_with_stop_refinement",
    "complete": false,
    "max_transfers": 1,
    "explored_pair_queries": 37,
    "stop_refinement": {
      "stop_queries": 8,
      "common_stop_candidates": 5,
      "refinement_successes": 4,
      "refinement_failures": 1,
      "truncated": false
    },
    "truncated": false
  },
  "assumptions": ["铁路出发前预留30分钟"],
  "warnings": [],
  "legs": []
}
```

- `duration_minutes` is door-to-door, not only in-vehicle time.
- `door_to_door_duration_minutes` repeats the complete user-facing total when it is known. `scheduled_span_minutes` measures the first-to-last fixed-service span; `in_vehicle_minutes`, `waiting_minutes`, `checkin_buffer_minutes`, and `local_connection_minutes` explain where the time went. `rank_routes.py` normalizes these fields even when a caller supplied only part of the breakdown.
- If the real origin connection is unknown, set `unknown_origin_connection=true`, `duration_complete=false`, and `door_to_door_duration_minutes=null`. Keep the modeled railway span in `scheduled_span_minutes`; do not relabel it as the overall duration.
- `recommended_leave_at` is when the traveler should leave the actual origin. It is not the railway departure time.
- `primary_service_departure_at` is the scheduled departure of the main reserved service, normally the intercity train. Keep additional scheduled departures on their legs.
- `estimated_arrival_at` is arrival at the user's final destination. Use `arrival_window` when unscheduled local transit or station-internal walking makes a point estimate look more precise than the data.
- `price_cny_per_person` is the complete price when `price_complete=true`. When `price_complete=false`, it may be the known subtotal; describe it as `已知费用`, never as the total. Missing price is unknown, not zero.
- `time_complete=false` means `duration_minutes` is only the modeled subtotal. List every omitted origin, destination, transfer, or dwell segment in `unmodeled_legs`; do not label such a route fastest.
- `unpriced_legs` names included legs whose price is unknown. `unmodeled_legs` names legs absent from the timeline entirely. An unpriced but timed leg belongs only in the former.
- `service_window_verified` is true only when a relevant first/last-service window was actually checked. Near a boundary, `rank_routes.py` changes an unverified point arrival into `earliest_if_service_available`, sets `arrival_estimate_confirmed=false`, and makes the timeline ineligible for a confirmed fastest/deadline claim.
- `search_coverage` records how alternatives were generated. A bounded hub or beam search normally has `complete=false`; report queried hubs/pairs, maximum transfers and truncation, and use `已查最省/最快` rather than an absolute claim.
- `risk_score` is in `[0, 1]`; use it only for real differences such as tight transfers, cross-station travel, unpriced legs, stale data, or low-frequency services.
- `connection_reliability` describes the chance of making fixed connections. `transfer_burden` and `overnight_seated_minutes` describe effort or discomfort. Do not collapse a long but reliable wait into the same concept as a tight risky connection.
- `inventory_confirmed=false` or `executable=false` means a fare is only a quote or inventory is unavailable. Such a route may be shown as a preview but cannot receive recommendation, fastest, cheapest, fewest-transfer, or Pareto labels.
- `transfer_count` counts vehicle changes. Walking into the first station is not a transfer.

## Leg

```json
{
  "mode": "walk|metro|bus|rail|taxi|buffer|dwell",
  "from": "杭州东站",
  "to": "上海南站",
  "departure_at": "2026-09-11T09:13:00+08:00",
  "arrival_at": "2026-09-11T10:05:00+08:00",
  "duration_minutes": 52,
  "price_cny": 77,
  "service": "G7194 二等座",
  "source": "12306-public",
  "queried_at": "2026-08-28T10:46:00+08:00",
  "scheduled": true,
  "price_known": true,
  "notes": []
}
```

Use `buffer` for railway check-in or conservative interchange allowance and `dwell` for time intentionally spent at a via point. This makes the total explainable and prevents hidden padding.

## Combining times

For scheduled legs, use their actual timestamps. Insert transfer or buffer time between legs rather than simply summing vehicle durations. Reject combinations whose next departure precedes the previous arrival plus the required transfer time.

For unscheduled local legs, sum expected durations and expose uncertainty as a range when the provider does not model wait time or station-internal walking.

Derive `recommended_leave_at` from the first door-to-door leg, `primary_service_departure_at` from the main scheduled service, and `estimated_arrival_at` from the final leg. When planning backward from an arrival deadline, select a feasible final arrival first and then calculate the latest safe leave time with all buffers intact.

If the user supplied only a city, district, or unlocated origin, do not invent the first leg. Leave `recommended_leave_at` null, add that origin leg to `unmodeled_legs`, and state the latest time the traveler must reach the first modeled station plus their own origin-to-station time.

## Ranking

Apply hard filters first. A partial price cannot prove a budget is met, and a partial timeline cannot prove a duration or transfer limit is met. Rank only after origin and destination connections have been attached, because a different arrival station can reverse the railway-only order. Among complete executable routes, use duration, price, transfers, and reliability; retain burden as a separate tradeoff. Routes with incomplete prices may compete for fastest only when their time is complete; they cannot be labeled cheapest. Preserve at least the Pareto frontier when a cheaper route is slower or a faster route costs more.
