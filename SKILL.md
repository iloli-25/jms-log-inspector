---
name: log-inspector
description: 查日志、看报错、分析线上或开发环境服务异常。当用户提到"查日志"、"看报错"、"线上异常"、"error"、"exception"或某个服务出问题时触发。
---

# Log Inspector

本技能用于快速查询和分析微服务的日志，定位异常原因并提供修复建议。

## 工作流

### 1. 提取环境与服务名

从用户的描述中提取以下两个关键参数：

- **环境 (env)**
    - `测试`、`dev`、`开发` → `dev`
    - `正式`、`prod`、`线上`、`生产` → `prod`
- **服务名 (service)**
    - 直接提取英文服务名，如 `order`、`gateway`、`admin`、`api-item`、`elasticsearch`、`wholesale-user`

**重要提示：**
- 如果用户询问"有哪些服务可以查"，读取 `references/services.json` 返回服务列表
- 如果无法提取 `env` 或 `service`，询问用户，不要猜测

### 2. 执行查询命令
```bash
python <SKILL_PATH>/scripts/main.py <env> <service> [lines]
```

- `lines` 可选，默认 100，没有明显报错时可增大到 500 再试

### 3. 日志分析与输出

1. **异常类型和发生时间**：具体类名、异常消息、时间戳
2. **完整堆栈**：摘录关键堆栈，重点关注项目代码部分
3. **原因判断**：根据上下文推断根源
4. **修复建议**：具体代码修改方案或排查方向

## 安全约束

- **严禁**自行编写任何新的 Python 脚本或 shell 脚本
- 所有远程操作必须且只能通过 `scripts/main.py` 执行
- 需要搜索关键词时，增大 `lines` 参数获取更多日志后自行分析
- 禁止执行任何写操作（rm、mv、chmod、kill、reboot 等）

## 资源定位

- **脚本**：`scripts/main.py`
- **服务配置**：`references/services.json`
- **堡垒机配置**：`references/config.json`