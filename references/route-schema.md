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
  "service_window_verified": false,
  "search_coverage": {
    "strategy": "bounded_hub_graph",
    "complete": false,
    "max_transfers": 1,
    "explored_pair_queries": 37,
    "truncated": false
  },
  "assumptions": ["铁路出发前预留30分钟"],
  "warnings": [],
  "legs": []
}
```

- `duration_minutes` is door-to-door, not only in-vehicle time.
- `recommended_leave_at` is when the traveler should leave the actual origin. It is not the railway departure time.
- `primary_service_departure_at` is the scheduled departure of the main reserved service, normally the intercity train. Keep additional scheduled departures on their legs.
- `estimated_arrival_at` is arrival at the user's final destination. Use `arrival_window` when unscheduled local transit or station-internal walking makes a point estimate look more precise than the data.
- `price_cny_per_person` is the complete price when `price_complete=true`. When `price_complete=false`, it may be the known subtotal; describe it as `已知费用`, never as the total. Missing price is unknown, not zero.
- `time_complete=false` means `duration_minutes` is only the modeled subtotal. List every omitted origin, destination, transfer, or dwell segment in `unmodeled_legs`; do not label such a route fastest.
- `unpriced_legs` names included legs whose price is unknown. `unmodeled_legs` names legs absent from the timeline entirely. An unpriced but timed leg belongs only in the former.
- `service_window_verified` is true only when a relevant first/last-service window was actually checked. Use false near service boundaries when the provider supplies a route but no verifiable operating window.
- `search_coverage` records how alternatives were generated. A bounded hub or beam search normally has `complete=false`; report queried hubs/pairs, maximum transfers and truncation, and use `已查最省/最快` rather than an absolute claim.
- `risk_score` is in `[0, 1]`; use it only for real differences such as tight transfers, cross-station travel, unpriced legs, stale data, or low-frequency services.
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

Apply hard filters first. A partial price cannot prove a budget is met, and a partial timeline cannot prove a duration or transfer limit is met. Among complete routes, use duration, price, transfers, and risk. Routes with incomplete prices may compete for fastest only when their time is complete; they cannot be labeled cheapest. Preserve at least the Pareto frontier when a cheaper route is slower or a faster route costs more.
