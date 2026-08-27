# 高德 Web 服务密钥配置

仅当高德路线查询提示缺少密钥，或需要安装、更新、检查、删除密钥时，才需要阅读本说明。

## 获取密钥

在[高德开放平台控制台](https://console.amap.com/dev/key/app)创建应用并添加密钥，服务平台选择 **Web 服务**。用于浏览器 JavaScript API 的密钥不能替代 Web 服务密钥。

不要让用户在聊天中粘贴密钥。随技能提供的凭据管理脚本会隐藏输入内容，也不会输出密钥：

```bash
python3 scripts/amap_credentials.py set
python3 scripts/amap_credentials.py status
```

再次运行 `set` 可以更新已有密钥。程序按以下顺序读取凭据：

1. 环境变量 `AMAP_MAPS_API_KEY`；
2. 操作系统的凭据管理器；
3. 如果仍未找到，则返回缺少密钥的错误和配置提示。

环境变量的优先级最高，便于在持续集成、容器或临时运行环境中使用不同的密钥。

## macOS 钥匙串

在本技能目录中运行：

```bash
python3 scripts/amap_credentials.py set
```

脚本会将密钥作为通用密码保存到当前用户的登录钥匙串中：

- 服务名称：`route-planner.amap-api-key`
- 账户名称：当前 macOS 用户名

输入内容会被隐藏，密钥不会出现在命令历史或进程参数中。macOS 可能要求解锁登录钥匙串或批准访问。

程序仍可读取旧版服务名称 `codex.route-planner.amap-api-key` 和 `codex.amap.maps-api-key`，新写入的凭据统一使用 `route-planner.amap-api-key`。

检查凭据是否存在，但不显示密钥：

```bash
python3 scripts/amap_credentials.py status
```

仅在用户明确要求时删除凭据：

```bash
python3 scripts/amap_credentials.py delete
```

## Windows 凭据管理器

在 Windows 中，该脚本会使用系统自带的凭据管理器，无需安装额外的 PowerShell 模块或第三方 Python 软件包：

```powershell
py scripts\amap_credentials.py set
py scripts\amap_credentials.py status
```

脚本会保存一项通用凭据，目标名称为：

```text
RoutePlanner/AMAP_MAPS_API_KEY
```

程序仍可读取旧版目标名称 `Codex/route-planner/AMAP_MAPS_API_KEY` 和 `Codex/china-multimodal-route-planner/AMAP_MAPS_API_KEY`，新写入的凭据统一使用 `RoutePlanner/AMAP_MAPS_API_KEY`。

仅在用户明确要求时删除凭据：

```powershell
py scripts\amap_credentials.py delete
```

## Linux、持续集成和容器

本技能不依赖 Linux 上的特定凭据管理器。请通过运行环境或平台的密钥管理服务注入 `AMAP_MAPS_API_KEY`。不要将密钥写入技能文件、源代码仓库、测试数据、日志或命令示例。
