# Provider selection and reliability

Read this file when selecting a data source, diagnosing an upstream failure, or deciding whether a browser fallback is justified.

## China Railway 12306 public website endpoints

Use `scripts/rail_12306.py` first.

- Session initialization: `https://kyfw.12306.cn/otn/leftTicket/init`
- Direct trains: the path is discovered from `CLeftTicketUrl`; common values include `/otn/leftTicket/queryG` and `/otn/leftTicket/query`.
- Ticket prices: `https://kyfw.12306.cn/otn/leftTicket/queryTicketPrice`
- Train stops: `https://kyfw.12306.cn/otn/czxx/queryByTrainNo`
- Station names: discover the versioned `station_name*.js` script from `https://www.12306.cn/index/`.
- Station sale times: `https://www.12306.cn/index/otn/index12306/queryAllCacheSaleTime`

These are public endpoints used by the official website, but they are not a documented developer API with a stability SLA. They need an initialized public Cookie session and can change paths or response fields. Respect rate limits, use a short cache, and do not retry indefinitely. A browser is not required for ordinary public queries.

The login-free direct endpoint does not provide a dependable official transfer-planning result. `rail_12306.py transfer` constructs a bounded timetable graph from direct queries. It then queries the full stop sequences of promising two-train pairs, intersects ordered stops, checks dated connection times, and requeries each segment so an intermediate regional station can compete on actual fare and inventory. This is still bounded: its default seed hubs are broad but not exhaustive, while hub, direct-query, stop-query, refinement and beam limits can truncate exploration. Always retain `search_coverage` in analysis and never promote `lowest_among_searched_candidates` to a network-wide optimum.

Transfer search distinguishes a named station from a city-wide query and checks cross-midnight datetimes and minimum same-station margins. Administrative city equality is not evidence of a feasible cross-station transfer. Cross-station candidates require both `--allow-cross-station` and a `--cross-station-rules` JSON file whose directed rules provide `from_station`, `to_station`, `duration_minutes`, `price_cny`, `source`, and optional `buffer_minutes` / `verified_at`. Obtain those values from a real local route query; without a matching rule the candidate is excluded. The ground time, fare and buffer are included in the itinerary.

Seat policy also matters: `cheapest-available` uses currently available inventory; `sleeper-required` requires a sleeper on railway legs that actually cross the 22:00–06:00 window while keeping daytime feeder legs economical. Quoted-but-unavailable prices are a separate preview and are not executable recommendations.

Rail responses are snapshots. A missing target date can mean the date is outside the sale window, the timetable is unpublished, or the upstream request failed. Preserve those distinctions.

## Shanghai Metro official website backend

Use `scripts/shanghai_metro.py` for Shanghai station-to-station routing.

- Station lookup: `https://m.shmetro.com/core/shmetro/mdstationinfoback_new.ashx`
- Route and fare: `https://m.shmetro.com/interface/plantrip/pt.aspx`

No key or browser is required. The route response provides lines, transfer stations, a fare, in-vehicle time, an impedance-style expected duration, and usually last-boarding/last-arrival references. Prefer expected duration for comparison, and retain in-vehicle time as a separate field. It does not provide a dependable first-service field, so the adapter keeps `service_window_verified=false`; an early-morning connection still needs confirmation. This is city-specific and should not be presented as a national metro API.

## AMap Web Service

Use `scripts/amap_transit.py` for addresses, walking legs, metro or bus routing outside the supported city-specific adapters.

- Geocoding: `https://restapi.amap.com/v3/geocode/geo`
- Integrated public transit: `https://restapi.amap.com/v3/direction/transit/integrated`
- Walking: `https://restapi.amap.com/v3/direction/walking`

This is an official REST service and does not need a browser, but it requires a Web Service key. The adapter checks `AMAP_MAPS_API_KEY` first, then the native macOS Keychain or Windows Credential Manager. For credential setup or failures, read [AMap key setup](amap-key-setup.md). Never print, log, place the key in user-visible URLs, or persist it outside an approved native credential store. Respect the account quota. AMap may omit a transit fare; treat that as unknown rather than zero.

Pass every known province/city/district constraint to the adapter. It returns all normalized geocoding candidates and rejects an administrative conflict. It also rejects a province/city/district centroid when the query names a specific POI, station, campus, road address, or building. Retry with a verified full street address or trusted coordinates; do not weaken the constraint simply to obtain a route.

The adapter serializes requests across processes, keeps only a short geocoding cache in the system temporary directory, and retries AMap QPS errors, HTTP 429 and 5xx with a finite backoff. The cache and throttle state never contain the key. Still invoke AMap routes sequentially rather than launching multiple adapters in parallel.

Integrated transit responses do not reliably prove that a suggested service is running at the requested early/late time. They carry `service_window_verified=false`; near a service boundary, seek a city-specific official source or label the first/last-service window unverified.

For ordered via points, query consecutive segments and add user-specified dwell time. Public-transit routing does not guarantee one globally optimal route across arbitrary via points.

## Fallback policy

1. Pure API script with authoritative or structured data.
2. Another official provider already available in the environment.
3. Official website or map lookup when the API path is unavailable.
4. Clearly labeled estimate or partial answer.

Do not silently substitute an old schedule, a different weekday, straight-line distance, or a generic metro fare rule for live provider data.
