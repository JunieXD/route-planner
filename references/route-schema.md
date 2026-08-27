# 路线数据结构

需要组合多个数据来源、按顺序经过多个途经点、计算多人费用，或处理三个以上交通路段时，阅读本文件。

## 完整行程

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

- `duration_minutes` 表示完整行程耗时，不只是乘车时间。
- `door_to_door_duration_minutes` 是为兼容现有脚本而保留的字段；数据完整时，它与向用户展示的完整行程总耗时相同。`scheduled_span_minutes` 表示第一个固定班次发车至最后一个固定班次到达之间的时间；`in_vehicle_minutes`、`waiting_minutes`、`checkin_buffer_minutes` 和 `local_connection_minutes` 用来说明时间构成。即使调用方只提供部分明细，`rank_routes.py` 也会整理这些字段。
- 无法确定实际起点到首站的接驳时，应设置 `unknown_origin_connection=true`、`duration_complete=false` 和 `door_to_door_duration_minutes=null`。已计算的铁路行程跨度仍保存在 `scheduled_span_minutes` 中，不能改称完整行程总耗时。
- `recommended_leave_at` 表示旅客从实际起点出发的建议时间，不是列车发车时间。
- `primary_service_departure_at` 表示主要固定班次的计划发车时间，通常是城际列车。其他固定班次的发车时间保存在对应路段中。
- `estimated_arrival_at` 表示到达用户最终目的地的时间。如果市内交通没有固定时刻，或站内步行时间会让单一时刻显得过于精确，应改用 `arrival_window` 表示到达时间范围。
- `price_complete=true` 时，`price_cny_per_person` 才表示每人完整费用；`price_complete=false` 时，它可以表示已知部分的费用，但必须写成“已知费用”，不能写成总费用。缺少价格表示未知，不能按 0 元计算。
- `time_complete=false` 表示 `duration_minutes` 只是目前能够计算的时间。所有遗漏的出发地接驳、目的地接驳、换乘或停留路段都应列入 `unmodeled_legs`，此类路线不能标为最快。
- `unpriced_legs` 列出已经计入时间但费用未知的路段；`unmodeled_legs` 列出没有计入时间线的路段。某个路段若已计时但费用未知，只应放入前者。
- 只有实际核对过相关首末班时间，`service_window_verified` 才能设为 `true`。行程接近首末班且时间尚未确认时，`rank_routes.py` 会将单一到达时刻改为 `earliest_if_service_available`，设置 `arrival_estimate_confirmed=false`，并不再用该方案判断最快路线或能否按时到达。
- `search_coverage` 记录备选方案的查找方式。按限定枢纽或候选数量搜索时，`complete` 通常为 `false`；应说明查过的枢纽或车次组合、最多换乘次数，以及查询是否提前结束，并写“在已查询方案中费用最低”或“在已查询方案中用时最短”，不能据此断言已经找到了所有车次中的最低费用或最短用时。
- `risk_score` 的取值范围是 `[0, 1]`，只应反映确有依据的差异，例如换乘时间紧、需要跨站、部分费用未知、数据较旧或班次稀少。
- `connection_reliability` 表示能否赶上固定班次；`transfer_burden` 和 `overnight_seated_minutes` 表示旅途劳累或不便。候车时间长可能更容易赶上后续班次，但也更累，不能把两者合并成同一个判断。
- `inventory_confirmed=false` 或 `executable=false` 表示票价仅供参考或当前无票。此类路线可以作为参考，但不能标为推荐、最快、最省、少换乘，也不能列入帕累托前沿。
- `transfer_count` 只计算更换交通工具的次数；步行进入第一个车站不算换乘。

## 路段

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

铁路进站预留和保守的换乘预留使用 `buffer`，用户要求在途经点停留的时间使用 `dwell`。这样可以清楚说明总耗时的构成，避免加入没有说明的预留时间。

## 组合时间

有固定时刻的路段应使用实际发车和到达时间。不同路段之间要另行加入换乘或预留时间，不能只把各段乘车时间相加。如果下一班车的发车时间早于上一段到达时间与必要换乘时间之和，应排除该组合。

市内交通没有固定时刻时，应累加预计耗时；若数据来源没有计入候车或站内步行，应使用时间范围表示不确定性。

`recommended_leave_at` 根据完整行程的第一个路段得出，`primary_service_departure_at` 取主要固定班次的发车时间，`estimated_arrival_at` 取最后一个路段的到达时间。按最晚到达时间倒推时，先选择符合要求的最终到达班次，再在保留所有必要预留时间的前提下计算最晚安全出发时间。

如果用户只提供城市、区县，或无法定位的出发地，不要编造第一段路线。应将 `recommended_leave_at` 设为空，把这一段加入 `unmodeled_legs`，并告诉用户最晚应在何时到达第一个已计算的车站，另加其自行前往车站的时间。

## 方案排序

先排除不满足必要条件的方案。费用不完整时，不能据此判断是否符合预算；时间线不完整时，也不能据此判断是否符合总耗时或换乘次数限制。只有补齐出发地和目的地的接驳路线后才能排序，因为不同到达站可能改变只比较铁路部分时的先后次序。

对于数据完整且实际可行的方案，应综合比较耗时、费用、换乘次数和可靠性，并将旅途负担作为单独的取舍因素。费用不完整但时间完整的路线可以参与最快方案比较，但不能标为最省。当低价方案更慢或快速方案更贵时，至少保留帕累托前沿中的方案。
