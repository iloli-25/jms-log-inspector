# **JMS Log Inspector (AI CLI Skill) 🤖**

**JMS Log Inspector (JLI)** 是CLI Skill工具。它通过 pexpect 自动化封装了跳板机（Jumpserver）登录、MFA (OTP) 校验及目标机跳转的复杂过程，使 AI 具备“自主登录服务器并排查日志”的能力。



## **📂 项目结构**

jms-log-inspector/  
├── scripts/  
│   └── main.py          \# 核心执行引擎 (AI 调用入口)  
├── references/          \# 凭证与资产清单 (GitIgnore)  
│   ├── config.json      \# 堡垒机/MFA 凭证  
│   └── services.json    \# 环境/服务/日志路径映射  
├── SKILL.md             \# 给 AI 的 System Prompt / 技能定义  
└── README.md

## **🛠️ 快速开始**

### **1\. 安装依赖**

pip install pexpect pyotp

### **2\. 初始化配置 (references/)**

在 references 目录下创建 config.json 和 services.json（参考项目内 .example 文件）。

### **3\. AI 调用模式**

* **Tail 模式** (查看最近趋势):  
  python scripts/main.py \<env\> \<service\> \[lines\]  
* **Grep 模式** (精准捕获异常):  
  python scripts/main.py \<env\> \<service\> grep \[keyword\]

## **🤖 如何在 AI Agent 中使用？**

将本项目根目录下的 SKILL.md 内容添加到你的 CLI的skill目录中。

**AI 典型工作流场景：**

1. **用户输入**：“线上 order 服务刚才报错了。”  
2. **AI 思考**：识别到 prod 环境和 order 服务，决定调用 main.py。  
3. **AI 行动**：python scripts/main.py prod order grep "Exception|ERROR"。  
4. **AI 分析**：根据返回的堆栈信息，定位到具体的 Java/Python 代码行，给出修复方案。
