# Route Planner

Route Planner 遵循 [Agent Skills 开放标准](https://agentskills.io)，用于规划中国境内的公共交通行程。Claude Code、Codex、Cursor、OpenCode、Gemini CLI、GitHub Copilot 等支持该标准的智能体均可使用。

## 安装

```bash
npx skills add JunieXD/route-planner -g -y
```

安装器会自动识别本机支持的智能体，并将技能安装到相应的用户目录。移除 `-g` 后只在当前项目中安装，也可用 `-a <agent>` 指定智能体。

没有 Node.js 时，可从 [Releases](https://github.com/JunieXD/route-planner/releases) 下载 `route-planner-v*.zip`，解压到智能体的技能目录。

## 功能

- 组合铁路、地铁、公交、出租车和步行，规划从出发地到目的地的完整行程。
- 比较建议出发时间、班次时刻、全程耗时、各段费用、换乘、步行和夜间乘车情况。
- 按最快、最省、少换乘或均衡偏好筛选，并将当前有票的方案与仅供参考的无票方案分开显示。
- 核对跨日行程的换乘时间、跨站接驳、首末班和余票，并说明查询范围和尚未确认的信息。

## 使用

向智能体描述起点、终点、日期和偏好即可，例如：

> 9 月 11 日上午从杭州出发去上海，比较最快和最省的完整行程方案。两人出行，最晚 11:30 到达。

结果默认以简洁表格列出建议出发时间、主要班次、最终到达时间、全程耗时、费用和重要提醒。

## 数据与权限

- 12306 和上海地铁查询使用公开接口，无需账号或浏览器；接口可能发生变化，稳定性不作保证。
- 其他城市的地址与市内交通可使用高德 Web 服务，设置方式见[说明](references/amap-key-setup.md)。
- 本技能只提供查询和路线规划，不执行购票、候补、改签、退票或支付。

## 许可证

[MIT](LICENSE)
