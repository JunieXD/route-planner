# Route Planner

Route Planner 是面向中国公共交通的 Codex Skill。它组合铁路、地铁、公交和步行数据，生成可核验的门到门路线，并比较出门时间、班次时刻、最终到达时间、票价、总耗时、换乘和风险。

## 主要能力

- 查询 12306 官网公开的车次、票价、余票和车站起售时间。
- 查询上海地铁官方路线、票价和预计耗时。
- 通过高德 Web Service 补充地址解析、公交、地铁和步行接驳。
- 支持起点、终点、按顺序途经点、人数、预算、到达期限和出行偏好。
- 输出推荐、最快、最省和少换乘等有效方案，并显示逐段价格与门到门时间。

## 安装

将仓库放入 Codex 的 skills 目录：

```bash
git clone https://github.com/JunieXD/route-planner.git "${CODEX_HOME:-$HOME/.codex}/skills/route-planner"
```

随后可在 Codex 中直接使用 `$route-planner`，也可以让 Codex 根据请求自动调用。

## 高德 Key

12306 与上海地铁查询不需要账号或 Key。全国地址和公共交通接驳使用高德 Web Service，需要在[高德开放平台](https://console.amap.com/dev/key/app)创建 **Web Service** Key。

推荐使用系统凭据管理器：

```bash
python3 scripts/amap_credentials.py set
python3 scripts/amap_credentials.py status
```

macOS 使用登录钥匙串，Windows 使用凭据管理器。Linux、CI 和容器可设置 `AMAP_MAPS_API_KEY`。脚本不会输出已保存的 Key，也不应把 Key 写入仓库、日志或对话。

## 使用示例

```text
$route-planner 9月11日从杭州西湖区出发，经杭州东站前往华东师范大学普陀校区。比较最快、最省和少换乘方案，两人出行，最晚11:30到达。
```

默认结果先给出紧凑对比表，再展开推荐路线的时间线。每个方案包含建议出门时间、核心班次发车时间、最终到达时间、总耗时、价格、换乘和步行负担。

## 数据来源与限制

- 12306 脚本使用官网公开接口，不是具有稳定性承诺的正式开发者 API，接口和字段可能调整。
- 上海地铁接口仅覆盖上海站到站路线，不代表全国统一地铁 API。
- 高德票价可能缺失；缺失价格按未知处理，不记为 0 元。
- 本项目只查询与规划，不执行登录、购票、候补、改签、退票或支付。
- 本项目与中国铁路、上海地铁及高德地图不存在隶属或授权关系。使用时应遵守各数据提供方的服务条款、访问频率和配额限制，不用于批量抓取。

## 开发检查

```bash
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests -v
```

## 许可证

[MIT](LICENSE)
