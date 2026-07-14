import pexpect
import pyotp
import re
import time
import json
import random
from pathlib import Path

def _mk_marker():
    """生成一个不会出现在远程 echo 中的标记（用 printf 构造 hex，避免字面出现）"""
    m = "%08x" % random.randrange(16**8)
    hex_str = "".join(f"\\x{ord(c):02x}" for c in m)
    return m, hex_str

SCRIPT_DIR = Path(__file__).parent

CONFIG_LOCATIONS = [
    SCRIPT_DIR / "config.json",
    SCRIPT_DIR.parent / "config.json",
    SCRIPT_DIR.parent / "references" / "config.json",
]

SERVICES_LOCATIONS = [
    SCRIPT_DIR / "services.json",
    SCRIPT_DIR.parent / "services.json",
    SCRIPT_DIR.parent / "references" / "services.json",
]


def _load_json(locations, name):
    for path in locations:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    raise FileNotFoundError(f"找不到 {name}，已尝试: {[str(p) for p in locations]}")


def load_config():
    return _load_json(CONFIG_LOCATIONS, "config.json")


def load_services():
    return _load_json(SERVICES_LOCATIONS, "services.json")


def resolve_service(env, service):
    """返回 list[(name, ip, path)]，单个节点也是单元素列表"""
    services = load_services()
    if env not in services:
        raise ValueError(f"未知环境 '{env}'，可选: {list(services.keys())}")
    if service not in services[env]:
        raise ValueError(f"'{env}' 下找不到服务 '{service}'，可选: {list(services[env].keys())}")
    cfg = services[env][service]
    if "_group" in cfg:
        names = cfg["_group"]
        instances = []
        for name in names:
            if name not in services[env]:
                raise ValueError(f"组 '{service}' 引用了不存在的实例 '{name}'")
            entry = services[env][name]
            instances.append((name, entry["ip"], entry["path"]))
        return instances
    return [(service, cfg["ip"], cfg["path"])]


def get_today_str():
    """返回今天日期字符串 YYYY-MM-DD"""
    from datetime import date
    return date.today().strftime("%Y-%m-%d")


def is_date_str(s):
    """判断是否是 YYYY-MM-DD 或 YYYY-MM 格式"""
    import re
    return bool(re.match(r'^\d{4}-\d{2}(?:-\d{2})?$', s))


def clean_ansi(text):
    if not text:
        return ""
    return re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])').sub('', text)


def connect(target_ip, dimensions=(50, 220)):
    """登录堡垒机并跳转到目标机，返回 pexpect child"""
    cfg = load_config()
    otp = pyotp.TOTP(cfg["mfa_secret"]).now()
    child = pexpect.spawn(
        f"ssh -p {cfg['port']} {cfg['user']}@{cfg['host']}",
        encoding='utf-8', codec_errors='replace', timeout=300,
        dimensions=dimensions, echo=False,
    )
    child.expect("[Pp]assword:", timeout=15)
    child.sendline(cfg["password"])
    child.expect("OTP Code", timeout=15)
    child.sendline(otp)
    child.expect(r"Opt>", timeout=30)
    time.sleep(0.5)
    child.send(f"{target_ip}\r")
    child.expect([r"❯", r"in ~", r"\$", r"#"], timeout=30)
    return child


def run_command(child, cmd):
    """执行单条命令，返回清理后的输出"""
    m, hex_str = _mk_marker()
    child.sendcontrol('u')
    time.sleep(0.2)
    child.sendline(f"m=$(printf '{hex_str}'); {cmd}; echo \"$m\"")
    child.expect(m, timeout=30)
    raw = clean_ansi(child.before).strip()
    idx = raw.rfind(m)
    if idx >= 0:
        raw = raw[:idx].strip()
    return raw


def disconnect(child):
    try:
        child.sendline("exit")
        child.sendline("q")
    except Exception:
        pass


def count_zip_files(child, log_path, file_keyword):
    """返回匹配 file_keyword 的 .zip 文件数量（file_keyword 为正则表达式）"""
    from pathlib import Path as P
    log_dir = str(P(log_path).parent)
    file_stem = P(log_path).stem
    m, hex_str = _mk_marker()
    child.sendcontrol('u')
    time.sleep(0.1)
    child.sendline(f"m=$(printf '{hex_str}'); ls {log_dir}/{file_stem}*.zip 2>/dev/null | grep -E '{file_keyword}' | wc -l; echo \"$m\"")
    child.expect(m, timeout=15)
    raw = clean_ansi(child.before).strip()
    idx = raw.rfind(m)
    if idx >= 0:
        raw = raw[:idx].strip()
    parts = raw.split("\n")
    try:
        return int(parts[-1].strip()) if parts else 0
    except ValueError:
        return 0


def zgrep_log(child, log_path, file_keyword, content_keyword, context_before=20, context_after=20):
    """聚合grep：搜索文件名匹配 file_keyword（正则）的 .zip + 旋转 .log + 当前 .log

    搜索范围：
      - .zip 分片  文件名匹配 file_keyword（grep -E 正则）
      - .log 分片  文件名匹配 file_keyword（grep -E 正则）
      - 当前 .log  仅 file_keyword 匹配今天时包含

    若 content_keyword 为空/None 则输出全文（跳过 grep 过滤）。
    """
    from pathlib import Path as P
    import os
    log_dir = str(P(log_path).parent)
    file_stem = P(log_path).stem
    current_name = os.path.basename(log_path)

    today = get_today_str()
    if not file_keyword:
        include_current = True
    else:
        try:
            include_current = bool(re.search(file_keyword, today))
        except re.error:
            include_current = False

    if content_keyword:
        zip_pipe = f"zcat '{{}}' | grep -B {context_before} -A {context_after} -E '{content_keyword}'"
        log_pipe = f"cat '{{}}' | grep -B {context_before} -A {context_after} -E '{content_keyword}'"
        cur_pipe = f"grep -B {context_before} -A {context_after} -E '{content_keyword}' {log_path} 2>/dev/null"
    else:
        zip_pipe = "zcat '{}'"
        log_pipe = "cat '{}'"
        cur_pipe = f"cat {log_path} 2>/dev/null"

    zip_cmd = (
        f"ls {log_dir}/{file_stem}*.zip 2>/dev/null "
        f"| grep -E '{file_keyword}' "
        f"| sort "
        f"| xargs -P 4 -I{{}} sh -c \"echo '=== {{}} ===' && {zip_pipe}\""
    )
    hist_cmd = (
        f"ls {log_dir}/{file_stem}*.log 2>/dev/null "
        f"| grep -v '^{current_name}$' "
        f"| grep -E '{file_keyword}' "
        f"| sort "
        f"| xargs -P 4 -I{{}} sh -c \"echo '=== {{}} ===' && {log_pipe}\""
    )

    cmd_parts = [zip_cmd, hist_cmd]
    if include_current:
        cmd_parts.append(f"echo '=== current log ===' && {cur_pipe}")

    m, hex_str = _mk_marker()
    full_cmd = "; ".join(cmd_parts)
    child.sendcontrol('u')
    time.sleep(0.2)
    child.sendline(
        f"m=$(printf '{hex_str}'); "
        f"echo '===START==='; "
        f"{{ {full_cmd}; }} 2>/dev/null; "
        f"echo \"$m\""
    )
    child.expect(m, timeout=300)
    raw = clean_ansi(child.before).strip()
    idx = raw.rfind("===START===")
    if idx >= 0:
        raw = raw[idx + len("===START==="):].strip()
    return raw