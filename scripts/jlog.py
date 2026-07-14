import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import pexpect
from jms_client import (
    resolve_service, connect, run_command, disconnect,
    count_zip_files, zgrep_log, get_today_str, is_date_str,
)

GREP_BEFORE = 2
GREP_AFTER = 10
ZGREP_BEFORE = 3
ZGREP_AFTER = 15


def _run_cmd_one(name, ip, path, cmd_template):
    cmd = cmd_template.replace("{path}", path)
    try:
        child = connect(ip)
        result = run_command(child, cmd)
        disconnect(child)
        return name, ip, result, None
    except pexpect.TIMEOUT:
        return name, ip, "", "[TIMEOUT] SSH 命令超时（>30秒），文件可能过大，建议缩小搜索范围"
    except Exception as e:
        return name, ip, "", str(e)


def _run_group(instances, cmd_template):
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_run_cmd_one, n, ip, p, cmd_template) for n, ip, p in instances]
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


def _run_zgrep_one(name, ip, path, file_keyword, content_keyword, context_before=20, context_after=20):
    try:
        child = connect(ip)
        result = zgrep_log(child, path, file_keyword, content_keyword, context_before, context_after)
        disconnect(child)
        return name, ip, result, None
    except Exception as e:
        return name, ip, "", str(e)


def _run_zgrep_group(instances, file_keyword, content_keyword, context_before=20, context_after=20):
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_run_zgrep_one, n, ip, p, file_keyword, content_keyword, context_before, context_after)
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


def _count_all(instances, file_keyword):
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


def _confirm_zgrep(instances, file_keyword, yes_flag=False):
    counts = _count_all(instances, file_keyword)
    total = sum(counts.values())
    if yes_flag or total <= 10:
        return True
    details = "  ".join(f"{n}={c}" for n, c in sorted(counts.items()))
    print(f"[!] 匹配到 {total} 个 zip 文件 ({details})，", end="")
    try:
        ans = input("输出可能很大，继续？(y/N): ").strip().lower()
        return ans == "y"
    except (EOFError, KeyboardInterrupt):
        return False


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage:")
        print("  jlog <env> <service> [lines]")
        print("  jlog <env> <service> grep [-A N] [-B N] [keyword]")
        print("  jlog <env> <service> zgrep [-f file] [-c keyword] [-A N] [-B N] [-y]")
        print("  jlog <env> <service> zgrep [<file>] [<keyword>] [-A N] [-B N]")
        print()
        print("例如:")
        print("  jlog dev order 200")
        print('  jlog dev order grep "NullPointerException"')
        print('  jlog dev order grep -A 10 -B 3 "Timeout"       # 自定义上下文行数')
        print('  jlog dev order grep ""                        # 全文输出')
        print('  jlog dev order zgrep              # 今天 + Exception|ERROR')
        print('  jlog dev order zgrep "Timeout"    # 今天 + Timeout')
        print('  jlog prod qygcli zgrep 2026-06-18 "ERROR"')
        print('  jlog prod qygcli zgrep -c "ERROR" -A 10 -B 5  # 自定义上下文')
        print('  jlog prod qygcli zgrep -f 2026-06-18')
        print('  jlog prod qygcli zgrep -c "ERROR" -y > out.txt   # 跳过确认')
        print('  jlog dev order zgrep 2026-06-18 -A 5 -B 5      # 混合：日期+上下文')
        print('  jlog dev order zgrep -c ""                     # 全文输出（不过滤）')
        print('  jlog dev order zgrep -f "2026-07-07|2026-07-09"  # -f 支持正则表达式')
        print('  jlog dev order zgrep -f "2026-07-0[1-9]"         # 字符类也有效')
        sys.exit(1)

    env = sys.argv[1]
    service = sys.argv[2]
    raw_mode = sys.argv[3] if len(sys.argv) > 3 else "tail"

    try:
        instances = resolve_service(env, service)
        is_group = len(instances) > 1

        # ── zgrep 模式 ──
        if raw_mode == "zgrep":
            extra = sys.argv[4:]
            yes_flag = '-y' in extra or '--yes' in extra
            extra = [a for a in extra if a not in ('-y', '--yes')]
            file_keyword = get_today_str()
            content_keyword = "Exception|ERROR"
            context_before = ZGREP_BEFORE
            context_after = ZGREP_AFTER

            positionals = []
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
                    positionals.append(extra[i])
                    i += 1
            if len(positionals) == 1:
                arg = positionals[0]
                if is_date_str(arg):
                    file_keyword = arg
                else:
                    content_keyword = arg
            elif len(positionals) >= 2:
                file_keyword, content_keyword = positionals[0], positionals[1]
            kw_display = content_keyword if content_keyword else "(全文)"
            label = f"zgrep 文件={file_keyword or '*'} 关键词={kw_display}"

            if is_group:
                print(f"[*] 环境={env} 服务={service} ({len(instances)}个实例) 模式={label}")
                if not _confirm_zgrep(instances, file_keyword, yes_flag):
                    print("已取消")
                    sys.exit(0)
                _run_zgrep_group(instances, file_keyword, content_keyword, context_before, context_after)
            else:
                name, ip, path = instances[0]
                print(f"[*] 环境={env} 服务={name} IP={ip} 模式={label}")
                child = connect(ip)
                c = count_zip_files(child, path, file_keyword)
                disconnect(child)
                if c > 10 and not yes_flag:
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
            sys.exit(0)

        # ── grep / tail 模式 ──
        if raw_mode == "grep":
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
                elif extra[i] in ('-c', '--content') and i + 1 < len(extra):
                    keyword = extra[i + 1]
                    i += 2
                elif extra[i].startswith('-'):
                    print(f"[WARN] grep 模式不支持 flag '{extra[i]}'，已忽略")
                    i += 1
                else:
                    positionals.append(extra[i])
                    i += 1
            if positionals:
                keyword = positionals[0]
            if keyword:
                cmd_template = f"grep -B {context_before} -A {context_after} -E '{keyword}' {{path}}"
                label = f"grep {keyword}"
            else:
                cmd_template = f"cat {{path}} 2>/dev/null"
                label = "grep (全文)"
        elif raw_mode.isdigit():
            cmd_template = f"tail -n {min(int(raw_mode), 500)} {{path}}"
            label = f"tail {raw_mode}行"
        else:
            cmd_template = "tail -n 100 {path}"
            label = "tail 100行"

        if is_group:
            print(f"[*] 环境={env} 服务={service} ({len(instances)}个实例) 模式={label}")
            _run_group(instances, cmd_template)
        else:
            name, ip, path = instances[0]
            print(f"[*] 环境={env} 服务={name} IP={ip} 模式={label}")
            child = connect(ip)
            result = run_command(child, cmd_template.replace("{path}", path))
            disconnect(child)
            print("\n" + "=" * 50)
            print(result if result else "未找到匹配内容")

    except pexpect.TIMEOUT:
        print("[TIMEOUT] SSH 命令执行超时（>300秒），可能是 zip 文件过多，建议缩小日期范围或限单节点")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
