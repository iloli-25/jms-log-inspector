---
name: log-inspector
description: 查日志、看报错、分析线上或开发环境服务异常。当用户提到"查日志"、"看报错"、"线上异常"、"error"、"exception"或某个服务出问题时触发。
---

# Log Inspector

本技能用于快速查询和分析微服务的日志，定位异常原因并提供修复建议。

## 快速参考

| 场景 | 命令 |
|---|---|
| 看最近日志 | `python <SKILL_PATH>/scripts/main.py dev order` |
| 搜关键字 | `python <SKILL_PATH>/scripts/main.py dev order grep "ERROR"` |
| 多节点并行搜 | `python <SKILL_PATH>/scripts/main.py prod my-service grep "Exception"` |
| 搜历史分片日志 | `python <SKILL_PATH>/scripts/main.py prod my-service zgrep "Timeout"` |
| 查看有哪些服务 | 读 `references/services.json` |

`<SKILL_PATH>` 即本 SKILL.md 所在目录，脚本在 `scripts/main.py`。

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
  - 读取 `references/services.json` 获取完整服务列表。
  - 示例服务：`order`、`gateway`、`admin`、`api-item`、`elasticsearch`、`ax-client`、`qygcli` 等。

**重要提示：**
- 如果用户询问"有哪些服务可以查"，读取 `references/services.json` 返回服务列表
- 如果无法提取 `env` 或 `service`，询问用户，不要猜测
- 如果服务名不在配置中，脚本会报 `[ERROR] 未知服务`，此时如实告知用户

#### services.json 结构

单节点服务：
```json
{
  "dev": {
    "order": {
      "ip": "192.168.x.x",
      "path": "/root/logs/app/app.log"
    }
  }
}
```

多节点（组）服务：
```json
{
  "prod": {
    "my-service": {
      "_group": ["my-service0", "my-service1"]
    },
    "my-service0": {
      "ip": "192.168.x.x",
      "path": "/home/user/logs/8080/app.log"
    },
    "my-service1": {
      "ip": "192.168.x.x",
      "path": "/home/user/logs/8081/app.log"
    }
  }
}
```

使用 `_group` 字段定义组。传入组名会自动并行查询所有成员实例。

### 2. 执行查询命令

**grep 模式**（优先使用，精准定位异常）：
```bash
python <SKILL_PATH>/scripts/main.py <env> <service> grep
python <SKILL_PATH>/scripts/main.py <env> <service> grep "NullPointerException"
```

不传 keyword 则默认搜索 `Exception|ERROR`。

**tail 模式**（grep 无结果时使用）：
```bash
python <SKILL_PATH>/scripts/main.py <env> <service>
python <SKILL_PATH>/scripts/main.py <env> <service> 500
```
默认 tail 100 行，最大 500 行。

**zgrep 聚合模式**（搜索历史分片 `.zip` + 当前 `.log`）：
```bash
python <SKILL_PATH>/scripts/main.py <env> <service> zgrep [<file_keyword>|<content_keyword>]
python <SKILL_PATH>/scripts/main.py <env> <service> zgrep -f <file_keyword> -c <content_keyword>
python <SKILL_PATH>/scripts/main.py prod my-service zgrep              # 今天 + Exception|ERROR
python <SKILL_PATH>/scripts/main.py prod my-service zgrep "Timeout"    # 今天 + Timeout
python <SKILL_PATH>/scripts/main.py prod my-service zgrep 2026-06-18 "ERROR"  # 指定日期+内容
python <SKILL_PATH>/scripts/main.py prod my-service zgrep -c "ERROR"           # 今天 + ERROR
python <SKILL_PATH>/scripts/main.py prod my-service zgrep -f 2026-06-18        # 指定日期+默认内容
```
- **参数默认值：** `file_keyword` 默认今天日期（YYYY-MM-DD），`content_keyword` 默认 `Exception|ERROR`
- **单参数自动识别：** `"2026-06-18"` 格式的视为日期（文件筛选），其他视为内容关键词
- `-f / --file`、`-c / --content` 显式指定，可任意顺序
- 传 `-f ""` 匹配所有 `.zip`
- 匹配超过 10 个 zip 时会先询问确认
- grep 上下文：-B 20 -A 20（前后各 20 行）

**正则语法：** 脚本底层使用 `grep -E`（扩展正则），keyword 直接作为正则表达式传入。
`|` 作为**或**运算符时**不要**转义：
```bash
python <SKILL_PATH>/scripts/main.py dev order grep "ERROR|Exception|Timeout"
```

**多节点输出格式：**
```text
=== 节点名 (IP) ===
...日志内容...

=== 节点名2 (IP) ===
...日志内容...
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

## 常见错误

| 错误信息 | 原因 | 处理 |
|---|---|---|
| `[ERROR] 未知环境 'xxx'` | 环境名不对 | 告知用户可选环境，让用户确认 |
| `[ERROR] 'dev' 下找不到服务 'xxx'` | 服务名不对 | 告知用户该环境下有哪些服务 |
| `[ERROR] ...` (连接超时等) | 网络/堡垒机问题 | 如实报告错误信息 |

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
