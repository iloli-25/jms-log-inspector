---
name: log-inspector
description: 查日志、看报错、分析线上或开发环境服务异常。当用户提到"查日志"、"看报错"、"线上异常"、"error"、"exception"或某个服务出问题时触发。
---

# Log Inspector

本技能用于快速查询和分析微服务的日志，定位异常原因并提供修复建议。

## 工作流

根据用户输入判断走哪个分支：

- 用户**只描述了问题** → 走 [模式A：直接查日志]
- 用户**提供了 curl** → 走 [模式B：复现后按链路排查]

---

## 模式A：直接查日志

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

脚本支持两种模式：
```bash
# grep 模式（优先使用，精准定位异常）
python <SKILL_PATH>/scripts/main.py <env> <service> grep
python <SKILL_PATH>/scripts/main.py <env> <service> grep "NullPointerException"

# tail 模式（grep 无结果时使用）
python <SKILL_PATH>/scripts/main.py <env> <service>
python <SKILL_PATH>/scripts/main.py <env> <service> 500
```

### 3. 查询策略

按以下顺序执行，**每步有结果就停止，不要继续往下**：

1. **先用 grep 模式**搜索 `Exception|ERROR`
2. grep 无结果 → 用 tail 默认 200 行看最近日志
3. 仍无结果 → **直接告知用户当前日志无异常，等待用户进一步指示**

---

## 模式B：提供 curl 后按链路排查

### 1. 提取信息

从用户描述中提取：
- **环境**：dev / prod
- **curl 命令**：用于复现问题
- **涉及的服务链路**：由用户用自然语言描述，如"会经过 gateway、order、user"
  - 如果用户没有描述链路，**询问用户**，不要自行猜测

### 2. 执行 curl 复现

执行用户提供的 curl，记录：
- 响应状态码和返回体
- 执行时间（用于后续在日志里定位时间点）

### 3. 按用户描述的链路逐个查日志

按用户给出的服务顺序，从前往后逐个 grep，**找到异常就停止，不要继续查后面的服务**：
```bash
python <SKILL_PATH>/scripts/main.py <env> <service> grep "ERROR|Exception"
```

### 4. 输出分析结果

1. **curl 响应**：状态码、返回体摘要
2. **报错服务**：哪个服务抛出了异常
3. **异常类型和时间**：类名、消息、时间戳
4. **完整堆栈**：重点关注项目代码部分
5. **原因判断**：结合请求参数和日志上下文
6. **修复建议**：具体修改方案

---

## 安全约束

- **严禁**自行编写任何新的 Python 脚本或 shell 脚本
- 所有远程操作必须且只能通过 `scripts/main.py` 执行
- **禁止** tail 超过 500 行，脚本内部已做硬限制
- **禁止**使用管道符在本地对脚本输出进行二次处理
- **禁止**在没有明确指示的情况下查询其他服务的日志
- **禁止**执行任何写操作（rm、mv、chmod、kill、reboot 等）
- curl 命令只允许执行用户明确提供的，**禁止自行构造或修改 curl**

---

## 无异常时的标准回复

如果日志里没有 ERROR/Exception，直接回复：

> 当前 {env} 环境日志无异常。
> 如需进一步排查，请提供具体时间范围、报错关键词或接口 curl。

如果是模式B 且所有服务都无异常，回复：

> curl 返回 {状态码}，所有服务日志均无异常。
> 可能是业务逻辑层问题，请提供具体的错误描述或预期与实际的差异。

---

## 资源定位

- **脚本**：`scripts/main.py`
- **服务配置**：`references/services.json`
- **堡垒机配置**：`references/config.json`