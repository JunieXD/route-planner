# Provider selection and reliability

Read this file when selecting a data source, diagnosing an upstream failure, or deciding whether a browser fallback is justified.

## China Railway 12306 public website endpoints

Use `scripts/rail_12306.py` first.

- Session initialization: `https://kyfw.12306.cn/otn/leftTicket/init`
- Direct trains: the path is discovered from `CLeftTicketUrl`; common values include `/otn/leftTicket/queryG` and `/otn/leftTicket/query`.
- Ticket prices: `https://kyfw.12306.cn/otn/leftTicket/queryTicketPrice`
- Station names: discover the versioned `station_name*.js` script from `https://www.12306.cn/index/`.
- Station sale times: `https://www.12306.cn/index/otn/index12306/queryAllCacheSaleTime`

These are public endpoints used by the official website, but they are not a documented developer API with a stability SLA. They need an initialized public Cookie session and can change paths or response fields. Respect rate limits, use a short cache, and do not retry indefinitely. A browser is not required for ordinary public queries.

Rail responses are snapshots. A missing target date can mean the date is outside the sale window, the timetable is unpublished, or the upstream request failed. Preserve those distinctions.

## Shanghai Metro official website backend

Use `scripts/shanghai_metro.py` for Shanghai station-to-station routing.

- Station lookup: `https://m.shmetro.com/core/shmetro/mdstationinfoback_new.ashx`
- Route and fare: `https://m.shmetro.com/interface/plantrip/pt.aspx`

No key or browser is required. The route response provides lines, transfer stations, a fare, in-vehicle time and an impedance-style expected duration. Prefer expected duration for comparison, and retain in-vehicle time as a separate field. This is city-specific and should not be presented as a national metro API.

## AMap Web Service

Use `scripts/amap_transit.py` for addresses, walking legs, metro or bus routing outside the supported city-specific adapters.

- Geocoding: `https://restapi.amap.com/v3/geocode/geo`
- Integrated public transit: `https://restapi.amap.com/v3/direction/transit/integrated`

This is an official REST service and does not need a browser, but it requires a Web Service key. The adapter checks `AMAP_MAPS_API_KEY` first, then the native macOS Keychain or Windows Credential Manager. For credential setup or failures, read [AMap key setup](amap-key-setup.md). Never print, log, place the key in user-visible URLs, or persist it outside an approved native credential store. Respect the account quota. AMap may omit a transit fare; treat that as unknown rather than zero.

For ordered via points, query consecutive segments and add user-specified dwell time. Public-transit routing does not guarantee one globally optimal route across arbitrary via points.

## Fallback policy

1. Pure API script with authoritative or structured data.
2. Another official provider already available in the environment.
3. Official website or map lookup when the API path is unavailable.
4. Clearly labeled estimate or partial answer.

Do not silently substitute an old schedule, a different weekday, straight-line distance, or a generic metro fare rule for live provider data.
