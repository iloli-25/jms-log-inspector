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


# ── 入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python main.py <env> <service> [lines]")
        print("例如:  python main.py dev order 200")
        sys.exit(1)

    env = sys.argv[1]
    service = sys.argv[2]
    lines = int(sys.argv[3]) if len(sys.argv) > 3 else 200

    try:
        ip, log_path = resolve_service(env, service)
        print(f"[*] 环境={env} 服务={service} IP={ip} 行数={lines}")
        child = connect(ip)
        result = run_command(child, f"tail -n {lines} {log_path}")
        disconnect(child)
        print("\n" + "=" * 50)
        print(result)
    except pexpect.TIMEOUT as e:
        print(f"[TIMEOUT] SSH 会话超时: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)