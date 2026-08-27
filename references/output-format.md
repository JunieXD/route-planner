# Compact user-facing route format

Read this file when presenting the final route comparison. The aim is fast visual comparison, not a prose travel diary.

## Default structure

1. Start with one sentence naming the recommendation and why it wins for the user's stated preference.
2. Show one comparison table with three to five distinct, feasible routes. Prefer these columns:

   | 方案 | 关键时刻（出门 → 发车 → 到达） | 总耗时 | 费用 | 换乘 / 步行 | 稳妥度 |
   |---|---|---:|---:|---|---|

   Put the train number or other main service beside its departure time. Show price as `¥81/人；2人¥162`; show unknown amounts as `待查`, never `¥0`.
3. Under the table, expand the recommended route as a single chronological line, for example: `08:03 出门 → 08:38 到杭州东 → 09:13 Gxxxx → 10:05 到上海南 → 10:42 到目的地`.
4. Expand at most one additional route when it represents a meaningful tradeoff such as saving money, arriving materially earlier, or avoiding a difficult transfer.
5. Finish with no more than three compact notes covering shared assumptions, missing costs or uncertainty, and provider/query freshness.

## Information hierarchy

- The three key times must be visible without opening details: recommended door departure, main scheduled departure, and estimated final arrival.
- Use labels such as `推荐`, `最快`, `最省`, and `少换乘`; combine labels when one route wins multiple categories and do not duplicate the route.
- Express tradeoffs as deltas where possible: `比推荐早到12分钟，贵¥18/人` is easier to compare than repeating both routes.
- Keep per-leg fares and minor stops in the expanded timeline or notes unless they explain a route's price difference.
- Put common data sources and query timestamps in one footer instead of repeating them in every row.

## Reliability labels

- `稳`: all scheduled connections retain the conservative buffer and there is no material unresolved warning.
- `一般`: feasible but includes ordinary uncertainty such as unscheduled metro waiting or moderate walking.
- `赶`: a tight connection, cross-station movement, or disruption would plausibly cause a missed fixed service.
- `待确认`: target inventory is not on sale, a key fare is unknown, or the schedule is stale or provisional.

Do not use a confidence label merely for decoration. State the decisive reason beside `赶` or `待确认`.

## Conditional detail

Show first/last-service constraints only when the trip is near service boundaries. Show accessibility, elevator, luggage, child, or elderly-traveler concerns when the user mentions them or when they materially distinguish routes. This keeps the default table clean while preserving important constraints.
