import pexpect
import pyotp
import re
import time
import sys
import json
import random
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _mk_marker():
    m = "%08x" % random.randrange(16**8)
    hex_str = "".join(f"\\x{ord(c):02x}" for c in m)
    return m, hex_str

SCRIPT_DIR = Path(__file__).parent

CONFIG_LOCATIONS = [
    SCRIPT_DIR / "config.json",
    SCRIPT_DIR.parent / "references" / "config.json",
]

SERVICES_LOCATIONS = [
    SCRIPT_DIR / "services.json",
    SCRIPT_DIR.parent / "references" / "services.json",
]

GREP_BEFORE = 2
GREP_AFTER = 20
ZGREP_BEFORE = 20
ZGREP_AFTER = 20
TAIL_MAX_LINES = 500


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
        encoding='utf-8', timeout=60, dimensions=dimensions,
        echo=False,
    )
    child.expect("[Pp]assword:", timeout=15)
    child.sendline(cfg["password"])
    child.expect("OTP Code", timeout=15)
    child.sendline(otp)
    child.expect(r"Opt>", timeout=30)
    time.sleep(0.5)
    child.send(f"{target_ip}\r")
    child.expect([r"❯", r"in ~", r"\$", r"#"], timeout=30)
    child.sendline("stty -echo 2>/dev/null")
    child.expect([r"❯", r"\$", r"#"], timeout=10)
    time.sleep(0.5)
    return child

def run_command(child, cmd):
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


def get_today_str():
    return date.today().strftime("%Y-%m-%d")


def is_date_str(s):
    return bool(re.match(r'^\d{4}-\d{2}(?:-\d{2})?$', s))


# ── 日志操作 ──────────────────────────────────────────

def tail_log(child, log_path, lines):
    lines = min(lines, TAIL_MAX_LINES)
    return run_command(child, f"tail -n {lines} {log_path}")

def grep_log(child, log_path, keyword, context_before=2, context_after=20):
    cmd = f"grep -B {context_before} -A {context_after} -E '{keyword}' {log_path}"
    return run_command(child, cmd)

def count_zip_files(child, log_path, file_keyword):
    log_dir = str(Path(log_path).parent)
    file_stem = Path(log_path).stem
    m, hex_str = _mk_marker()
    child.sendcontrol('u')
    time.sleep(0.1)
    child.sendline(f"m=$(printf '{hex_str}'); ls {log_dir}/{file_stem}*{file_keyword}*.zip 2>/dev/null | wc -l; echo \"$m\"")
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
    log_dir = str(Path(log_path).parent)
    file_stem = Path(log_path).stem

    zip_cmd = (
        f"for f in {log_dir}/{file_stem}*{file_keyword}*.zip; do "
        f"[ -f \"$f\" ] && echo \"=== $f ===\" && zcat \"$f\" | "
        f"grep -B {context_before} -A {context_after} -E '{content_keyword}'; "
        f"done 2>/dev/null"
    )
    log_cmd = (
        f"echo '=== current log ===' && "
        f"grep -B {context_before} -A {context_after} -E '{content_keyword}' {log_path} 2>/dev/null"
    )

    m, hex_str = _mk_marker()
    child.sendcontrol('u')
    time.sleep(0.2)
    child.sendline(f"m=$(printf '{hex_str}'); {zip_cmd}; {log_cmd}; echo \"$m\"")
    child.expect(m, timeout=120)
    raw = clean_ansi(child.before).strip()
    idx = raw.rfind(m)
    if idx >= 0:
        raw = raw[:idx].strip()
    return raw


# ── 多节点并行执行 ───────────────────────────────────

def _run_one(mode, name, ip, path, keyword, file_keyword=None,
              context_before=20, context_after=20):
    try:
        child = connect(ip)
        if mode == "grep":
            result = grep_log(child, path, keyword, context_before, context_after)
        elif mode == "zgrep":
            result = zgrep_log(child, path, file_keyword or "", keyword or "Exception|ERROR",
                               context_before, context_after)
        else:
            lines = int(mode) if mode.isdigit() else 200
            result = tail_log(child, path, lines)
        disconnect(child)
        return name, ip, result, None
    except Exception as e:
        return name, ip, "", str(e)

def _run_parallel(mode, instances, keyword=None, file_keyword=None,
                   context_before=20, context_after=20):
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_run_one, mode, n, ip, p, keyword, file_keyword,
                        context_before, context_after)
            for n, ip, p in instances
        ]
        first = True
        for f in as_completed(futures):
            name, ip, result, err = f.result()
            if not first:
                print()
            first = False
            print(f"=== {name} ({ip}) ===")
            if err:
                print(f"[ERROR] {err}")
            else:
                print(result if result else "未找到匹配内容")


# ── 聚合 zip 文件计数（并行多节点） ─────────────────

def _count_on_instances(instances, file_keyword):
    counts = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        def _count_one(name, ip, path):
            try:
                child = connect(ip)
                c = count_zip_files(child, path, file_keyword)
                disconnect(child)
                return name, c
            except Exception:
                return name, 0
        futures = [pool.submit(_count_one, n, ip, p) for n, ip, p in instances]
        for f in as_completed(futures):
            n, c = f.result()
            counts[n] = c
    return counts


# ── 入口 ──────────────────────────────────────────────

def print_usage():
    print("Usage:")
    print("  python main.py <env> <service> [lines]")
    print("  python main.py <env> <service> grep [-A N] [-B N] [keyword]")
    print("  python main.py <env> <service> zgrep [-f file] [-c keyword] [-A N] [-B N]")
    print("  python main.py <env> <service> zgrep [<file_keyword>|<content_keyword>]")
    print("  python main.py <env> <service> zgrep <file_keyword> <content_keyword>")
    print()
    print("例如:")
    print("  python main.py dev order              # tail 默认200行")
    print("  python main.py dev order 500          # tail 指定行数（上限500）")
    print("  python main.py dev order grep         # grep 默认关键词")
    print('  python main.py dev order grep "NullPointerException"')
    print('  python main.py dev order grep -A 10 -B 3 "Timeout"   # 自定义上下文')
    print('  python main.py dev order zgrep              # 今天 + Exception|ERROR')
    print('  python main.py dev order zgrep "Timeout"    # 今天 + Timeout')
    print('  python main.py prod qygcli zgrep 2026-06-18 "ERROR"')
    print('  python main.py prod qygcli zgrep -c "ERROR" -A 10 -B 5')
    print()
    print("多节点服务自动并行查所有实例")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print_usage()
        sys.exit(1)

    env = sys.argv[1]
    service = sys.argv[2]
    mode = sys.argv[3] if len(sys.argv) > 3 else "tail"

    try:
        instances = resolve_service(env, service)

        # ── zgrep 聚合模式 ──
        if mode == "zgrep":
            extra = sys.argv[4:]
            file_keyword = get_today_str()
            content_keyword = "Exception|ERROR"
            context_before = ZGREP_BEFORE
            context_after = ZGREP_AFTER

            if len(extra) == 0:
                pass
            elif any(a.startswith('-') for a in extra):
                i = 0
                while i < len(extra):
                    if extra[i] in ('-f', '--file') and i + 1 < len(extra):
                        file_keyword = extra[i + 1]
                        i += 2
                    elif extra[i] in ('-c', '--content') and i + 1 < len(extra):
                        content_keyword = extra[i + 1]
                        i += 2
                    elif extra[i] == '-A' and i + 1 < len(extra):
                        context_after = int(extra[i + 1])
                        i += 2
                    elif extra[i] == '-B' and i + 1 < len(extra):
                        context_before = int(extra[i + 1])
                        i += 2
                    else:
                        i += 1
            elif len(extra) == 1:
                arg = extra[0]
                if is_date_str(arg):
                    file_keyword = arg
                else:
                    content_keyword = arg
            else:
                file_keyword, content_keyword = extra[0], extra[1]
            label = f"zgrep 文件={file_keyword or '*'} 关键词={content_keyword}"

            if len(instances) == 1:
                name, ip, path = instances[0]
                print(f"[*] 环境={env} 服务={name} IP={ip} 模式={label}")
                child = connect(ip)
                c = count_zip_files(child, path, file_keyword)
                disconnect(child)
                if c > 10:
                    try:
                        ans = input(f"[!] 匹配到 {c} 个 zip 文件，输出可能很大，继续？(y/N): ").strip().lower()
                        if ans != "y":
                            print("已取消")
                            sys.exit(0)
                    except (EOFError, KeyboardInterrupt):
                        print("已取消")
                        sys.exit(0)
                child = connect(ip)
                result = zgrep_log(child, path, file_keyword, content_keyword, context_before, context_after)
                disconnect(child)
                print("\n" + "=" * 50)
                print(result if result else "未找到匹配内容")
            else:
                print(f"[*] 环境={env} 服务={service} ({len(instances)}个实例) 模式={label}")
                counts = _count_on_instances(instances, file_keyword)
                total = sum(counts.values())
                if total > 10:
                    details = "  ".join(f"{n}={c}" for n, c in sorted(counts.items()))
                    print(f"[!] 匹配到 {total} 个 zip 文件 ({details})，", end="")
                    try:
                        ans = input("输出可能很大，继续？(y/N): ").strip().lower()
                        if ans != "y":
                            print("已取消")
                            sys.exit(0)
                    except (EOFError, KeyboardInterrupt):
                        print("已取消")
                        sys.exit(0)
                _run_parallel("zgrep", instances, content_keyword, file_keyword,
                             context_before, context_after)
            sys.exit(0)

        # ── grep / tail 模式 ──
        if len(instances) == 1:
            name, ip, path = instances[0]
            child = connect(ip)

            if mode == "grep":
                extra = sys.argv[4:]
                context_before = GREP_BEFORE
                context_after = GREP_AFTER
                keyword = "Exception|ERROR"
                positionals = []
                i = 0
                while i < len(extra):
                    if extra[i] == '-A' and i + 1 < len(extra):
                        context_after = int(extra[i + 1])
                        i += 2
                    elif extra[i] == '-B' and i + 1 < len(extra):
                        context_before = int(extra[i + 1])
                        i += 2
                    else:
                        positionals.append(extra[i])
                        i += 1
                if positionals:
                    keyword = positionals[0]
                print(f"[*] 环境={env} 服务={name} IP={ip} 模式=grep 关键词={keyword}")
                result = grep_log(child, path, keyword, context_before, context_after)
            else:
                lines = min(int(mode), TAIL_MAX_LINES) if mode.isdigit() else 200
                print(f"[*] 环境={env} 服务={name} IP={ip} 模式=tail 行数={lines}")
                result = tail_log(child, path, lines)

            disconnect(child)
            print("\n" + "=" * 50)
            print(result if result else "未找到匹配内容")
        else:
            print(f"[*] 环境={env} 服务={service} ({len(instances)}个实例)")
            context_before = GREP_BEFORE
            context_after = GREP_AFTER
            keyword = "Exception|ERROR" if mode == "grep" else None
            if mode == "grep":
                extra = sys.argv[4:]
                positionals = []
                i = 0
                while i < len(extra):
                    if extra[i] == '-A' and i + 1 < len(extra):
                        context_after = int(extra[i + 1])
                        i += 2
                    elif extra[i] == '-B' and i + 1 < len(extra):
                        context_before = int(extra[i + 1])
                        i += 2
                    else:
                        positionals.append(extra[i])
                        i += 1
                if positionals:
                    keyword = positionals[0]
            _run_parallel(mode, instances, keyword, context_before=context_before, context_after=context_after)

    except pexpect.TIMEOUT as e:
        print(f"[TIMEOUT] SSH 会话超时: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
