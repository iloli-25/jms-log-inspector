import pexpect
import pyotp
import re
import time
import sys
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

CONFIG_LOCATIONS = [
    SCRIPT_DIR / "config.json",
    SCRIPT_DIR.parent / "references" / "config.json",
]

SERVICES_LOCATIONS = [
    SCRIPT_DIR / "services.json",
    SCRIPT_DIR.parent / "references" / "services.json",
]

TAIL_MAX_LINES = 500
GREP_MAX_RESULTS = 200


# ── 配置加载 ──────────────────────────────────────────

def _load_json(locations, name):
    for path in locations:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    raise FileNotFoundError(
        f"找不到 {name}，已尝试: {[str(p) for p in locations]}"
    )

def load_config():
    return _load_json(CONFIG_LOCATIONS, "config.json")

def load_services():
    return _load_json(SERVICES_LOCATIONS, "services.json")

def resolve_service(env, service):
    services = load_services()
    if env not in services:
        raise ValueError(f"未知环境 '{env}'，可选: {list(services.keys())}")
    if service not in services[env]:
        raise ValueError(
            f"'{env}' 下找不到服务 '{service}'，"
            f"可选: {list(services[env].keys())}"
        )
    cfg = services[env][service]
    return cfg["ip"], cfg["path"]


# ── 工具函数 ──────────────────────────────────────────

def clean_ansi(text):
    if not text:
        return ""
    return re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])').sub('', text)


# ── SSH 操作 ──────────────────────────────────────────

def connect(target_ip, dimensions=(50, 220)):
    cfg = load_config()
    otp = pyotp.TOTP(cfg["mfa_secret"]).now()
    child = pexpect.spawn(
        f"ssh -p {cfg['port']} {cfg['user']}@{cfg['host']}",
        encoding='utf-8', timeout=60, dimensions=dimensions
    )
    child.expect("[Pp]assword:", timeout=15)
    child.sendline(cfg["password"])
    child.expect("OTP Code", timeout=15)
    child.sendline(otp)
    child.expect(r"Opt>", timeout=30)
    time.sleep(0.5)
    child.send(f"{target_ip}\r")
    child.expect([r"❯", r"in ~", r"\$", r"#"], timeout=30)
    time.sleep(0.5)
    return child

def run_command(child, cmd):
    child.sendcontrol('u')
    time.sleep(0.2)
    child.sendline(cmd)
    child.expect([r"❯", r"\$", r"#"], timeout=30)
    return clean_ansi(child.before).strip()

def disconnect(child):
    try:
        child.sendline("exit")
        child.sendline("q")
    except Exception:
        pass


# ── 日志操作 ──────────────────────────────────────────

def tail_log(child, log_path, lines):
    lines = min(lines, TAIL_MAX_LINES)
    return run_command(child, f"tail -n {lines} {log_path}")

def grep_log(child, log_path, keyword, context=20):
    # 只在最近5000行里搜，避免扫全文
    cmd = (
        f"tail -n 5000 {log_path} | "
        f"grep -B 2 -A {context} -E '{keyword}' | "
        f"tail -n {GREP_MAX_RESULTS}"
    )
    return run_command(child, cmd)


# ── 入口 ──────────────────────────────────────────────

def print_usage():
    print("Usage:")
    print("  python main.py <env> <service> [lines]")
    print("  python main.py <env> <service> grep [keyword]")
    print()
    print("例如:")
    print("  python main.py dev order              # tail 默认200行")
    print("  python main.py dev order 500          # tail 指定行数（上限500）")
    print("  python main.py dev order grep         # grep 默认关键词")
    print('  python main.py dev order grep "NullPointerException"  # grep 指定关键词')

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print_usage()
        sys.exit(1)

    env = sys.argv[1]
    service = sys.argv[2]
    mode = sys.argv[3] if len(sys.argv) > 3 else "tail"

    try:
        ip, log_path = resolve_service(env, service)

        child = connect(ip)

        if mode == "grep":
            keyword = sys.argv[4] if len(sys.argv) > 4 else "Exception|ERROR"
            print(f"[*] 环境={env} 服务={service} IP={ip} 模式=grep 关键词={keyword}")
            result = grep_log(child, log_path, keyword)
        else:
            lines = min(int(mode), TAIL_MAX_LINES) if mode.isdigit() else 200
            print(f"[*] 环境={env} 服务={service} IP={ip} 模式=tail 行数={lines}")
            result = tail_log(child, log_path, lines)

        disconnect(child)
        print("\n" + "=" * 50)
        print(result if result else "未找到匹配内容")

    except pexpect.TIMEOUT as e:
        print(f"[TIMEOUT] SSH 会话超时: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)