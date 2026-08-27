# 安全说明

## API Key

不要在 Issue、Pull Request、日志、截图或对话中提交高德 Key。如果 Key 可能已经泄露，应立即在高德开放平台撤销并重新创建，不要只删除 Git 记录中的可见文本。

Route Planner 优先从 `AMAP_MAPS_API_KEY` 读取临时凭据，也支持 macOS 钥匙串和 Windows 凭据管理器。凭据脚本只报告是否存在可用 Key，不输出 Key 内容。

## 报告安全问题

请使用 GitHub 的私有漏洞报告功能，不要通过公开 Issue 提交漏洞细节或有效凭据。报告应包含影响范围、复现条件和建议修复方式，但不应包含真实用户数据。
