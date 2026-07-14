import sys
import pexpect
from pathlib import Path
from jms_client import resolve_service, connect


def _select_instance(service, instances):
    if len(instances) == 1:
        return instances[0]
    print(f"[*] 服务 {service} 有 {len(instances)} 个实例:")
    for i, (name, ip, path) in enumerate(instances, 1):
        port = path.split("/")[-2] if "/" in path else "?"
        print(f"  {i}) {name} ({ip}:{port})")
    while True:
        try:
            choice = input(f"选择实例 (1-{len(instances)}): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(instances):
                return instances[idx]
        except ValueError:
            pass
        print(f"输入无效，请输入 1-{len(instances)}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: reach <env> <service>")
        print("例如:  reach dev order")
        sys.exit(1)

    env = sys.argv[1]
    service = sys.argv[2]

    try:
        instances = resolve_service(env, service)
        name, ip, path = _select_instance(service, instances)
        log_dir = str(Path(path).parent)
        print(f"[*] 连接 {env}/{name} -> {ip}")
        print(f"[*] 进入目录 {log_dir}")

        child = connect(ip, dimensions=(50, 220))
        child.sendline(f"cd {log_dir}")
        child.expect([r"\u276f", r"\$", r"#"], timeout=10)
        print("[*] 已就位，开始交互\n")

        child.interact()

    except KeyboardInterrupt:
        print("\n[*] 已退出")
    except pexpect.TIMEOUT:
        print("[TIMEOUT] 连接目标服务器超时，可能网络不稳定或服务器暂时不可达，稍后重试即可")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
