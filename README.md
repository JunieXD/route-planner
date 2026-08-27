# Route Planner

面向 [Agent Skills 开放标准](https://agentskills.io) 的中国公共交通路线规划 Skill。适用于 Claude Code、Codex、Cursor、OpenCode、Gemini CLI、GitHub Copilot 等支持 Agent Skills 的智能体。

## 安装

```bash
npx skills add JunieXD/route-planner -g -y
```

安装器会识别本机支持的智能体并完成全局安装。移除 `-g` 可安装到当前项目，也可用 `-a <agent>` 指定目标智能体。

不使用 Node.js 时，可从 [Releases](https://github.com/JunieXD/route-planner/releases) 下载 `route-planner-v*.zip`，解压到智能体的 Skills 目录。

## 功能

- 组合铁路、地铁、公交、出租车和步行，生成门到门路线。
- 比较发车与到达时间、总耗时、逐段价格、换乘、步行和过夜负担。
- 按最快、最省、少换乘或均衡偏好筛选，并区分可售方案与仅报价方案。
- 校验跨日衔接、跨站接驳、首末班、余票和搜索覆盖，明确标注不确定信息。

## 使用

向智能体描述起点、终点、日期和偏好即可，例如：

> 9 月 11 日上午从杭州出发去上海，比较最快和最省的门到门方案，两人出行，最晚 11:30 到达。

结果默认使用紧凑表格比较建议出门时间、核心班次、最终到达时间、总耗时、费用和关键风险。

## 数据与权限

- 12306 和上海地铁查询使用公开接口，无需账号或浏览器；这些接口不提供稳定性承诺。
- 其他城市的地址与市内交通可使用高德 Web Service，设置方式见[说明](references/amap-key-setup.md)。
- 本 Skill 只查询和规划，不执行购票、候补、改签、退票或支付。

## License

[MIT](LICENSE)
