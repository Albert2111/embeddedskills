"""net skill 私有运行时工具。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_NAME = "net"
STATE_DIR_NAME = ".embeddedskills"
STATE_FILE_NAME = "state.json"
PROJECT_CONFIG_FILE = "config.json"

WINDOWS_TOOL_DIRS = [
    Path(r"C:\Program Files\Wireshark"),
    Path(r"C:\Program Files (x86)\Wireshark"),
]

WSL_WIRESHARK_DIRS = [
    Path("/mnt/c/Program Files/Wireshark"),
    Path("/mnt/c/Program Files (x86)/Wireshark"),
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def is_wsl() -> bool:
    """检测是否运行在 WSL 环境下"""
    if sys.platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def decode_text(data: bytes | str | None) -> str:
    """以稳健方式解码命令输出，兼容 Windows 下工具的混合编码。"""
    if data is None:
        return ""
    if isinstance(data, str):
        return data

    for encoding in ("utf-8", "gbk", "cp1252", sys.getdefaultencoding()):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def looks_like_ipv4(value: str) -> bool:
    return bool(re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value))


def looks_like_ip(value: str) -> bool:
    return looks_like_ipv4(value) or (":" in value and bool(re.fullmatch(r"[0-9a-fA-F:]+(?:%\d+)?", value)))


def resolve_tool_path(configured: str | None, default_name: str) -> str:
    """解析工具路径，优先使用配置，其次 PATH，最后尝试常见安装目录。"""
    candidates: list[str] = []
    if configured and configured.strip():
        candidates.append(configured.strip())
    candidates.append(default_name)

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)

        expanded = str(Path(candidate).expanduser())
        if Path(expanded).exists():
            return expanded

        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    # WSL 环境下优先搜索 Windows 挂载路径，再搜索 Windows 路径
    search_dirs = WSL_WIRESHARK_DIRS + WINDOWS_TOOL_DIRS if is_wsl() else WINDOWS_TOOL_DIRS
    # WSL 下同时尝试 .exe 变体
    name_variants = [default_name]
    if is_wsl() and not default_name.endswith(".exe"):
        name_variants.append(default_name + ".exe")
    for base_dir in search_dirs:
        for name in name_variants:
            candidate_path = base_dir / name
            if candidate_path.exists():
                return str(candidate_path)

    return configured.strip() if configured and configured.strip() else default_name


def load_json_file(path: str | Path) -> dict:
    """加载 JSON 文件，不存在返回空字典"""
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_json_file(path: str | Path, data: dict) -> None:
    """保存 JSON 文件，自动创建目录"""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_local_config() -> dict:
    """加载 skill/config.json（环境级配置）"""
    return load_json_file(SKILL_DIR / "config.json")


def save_local_config(data: dict) -> None:
    """保存环境级配置到 skill/config.json"""
    save_json_file(SKILL_DIR / "config.json", data)


def workspace_root(workspace: str | None = None) -> Path:
    if not is_missing(workspace):
        return Path(str(workspace)).expanduser().resolve()
    return Path.cwd().resolve()


def load_project_config(workspace: str | None = None) -> dict:
    """从 workspace/.embeddedskills/config.json 读取本 skill 的工程级配置"""
    proj_config = load_json_file(workspace_root(workspace) / STATE_DIR_NAME / PROJECT_CONFIG_FILE)
    return proj_config.get(SKILL_NAME, {})


def save_project_config(workspace: str | None = None, values: dict | None = None) -> None:
    """写回工程级配置，只更新本 skill 的部分"""
    if values is None:
        return
    proj_path = workspace_root(workspace) / STATE_DIR_NAME / PROJECT_CONFIG_FILE
    proj_config = load_json_file(proj_path)
    proj_config[SKILL_NAME] = {**proj_config.get(SKILL_NAME, {}), **values}
    save_json_file(proj_path, proj_config)


def load_workspace_state(workspace: str | None = None) -> dict:
    """从 workspace/.embeddedskills/state.json 读取状态"""
    return load_json_file(workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME)


def save_workspace_state(state: dict, workspace: str | None = None) -> Path:
    """保存状态"""
    file_path = workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME
    save_json_file(file_path, state)
    return file_path


def update_state_entry(category: str, record: dict, workspace: str | None = None) -> dict:
    """更新状态条目"""
    state = load_workspace_state(workspace)
    state[category] = {**record, "timestamp": record.get("timestamp") or now_iso()}
    file_path = save_workspace_state(state, workspace)
    return {
        "workspace": str(workspace_root(workspace)),
        "file": str(file_path),
        "updated_keys": [category],
        category: state[category],
    }


def normalize_path(value: str | None, base: str | Path | None = None) -> str:
    """路径规范化"""
    if is_missing(value):
        return ""
    path = Path(str(value)).expanduser()
    if base and not path.is_absolute():
        path = Path(base) / path
    return str(path.resolve()) if path.is_absolute() else str(path)


def _first_resolved(mapping: dict, keys: list[str]) -> tuple[Any, str | None]:
    for key in keys:
        value = mapping.get(key)
        if not is_missing(value):
            return value, key
    return None, None


def resolve_param(
    name: str,
    cli_value: Any = None,
    local_config: dict | None = None,
    local_keys: list[str] | None = None,
    project_config: dict | None = None,
    project_keys: list[str] | None = None,
    state: dict | None = None,
    state_keys: list[str] | None = None,
    default: Any = None,
) -> tuple[Any, str]:
    """统一参数解析，优先级: CLI > 环境级 > 工程级 > state > default"""
    if not is_missing(cli_value):
        return cli_value, "cli"

    if local_config and local_keys:
        value, key = _first_resolved(local_config, local_keys)
        if not is_missing(value):
            return value, f"local:{key}"

    if project_config and project_keys:
        value, key = _first_resolved(project_config, project_keys)
        if not is_missing(value):
            return value, f"project:{key}"

    if state and state_keys:
        value, key = _first_resolved(state, state_keys)
        if not is_missing(value):
            return value, f"state:{key}"

    if not is_missing(default):
        return default, "default"

    return None, ""


def parameter_context(name: str, value: Any, source: str) -> dict:
    """记录参数来源"""
    return {"name": name, "value": value, "source": source}


def make_result(
    success: bool = True,
    action: str = "",
    summary: str = "",
    details: dict | None = None,
    error: dict | None = None,
) -> dict:
    """统一结果格式"""
    result = {
        "status": "ok" if success else "error",
        "action": action,
        "summary": summary,
    }
    if details:
        result["details"] = details
    if error:
        result["error"] = error
    return result


def make_timing(start_time: float) -> dict:
    """执行时间记录"""
    elapsed = datetime.now().timestamp() - start_time
    return {
        "started_at": datetime.fromtimestamp(start_time).astimezone().isoformat(timespec="seconds"),
        "finished_at": now_iso(),
        "elapsed_ms": int(elapsed * 1000),
    }


def _default_tshark_name() -> str:
    """获取当前平台对应的 tshark 可执行文件名"""
    return "tshark.exe" if sys.platform == "win32" else "tshark"


def check_tshark(exe: str = "tshark") -> bool:
    """检查 tshark 是否可用"""
    resolved_exe = resolve_tool_path(exe, _default_tshark_name())
    try:
        result = subprocess.run([resolved_exe, "--version"], capture_output=True, text=False, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def parse_tshark_interfaces(tshark_exe: str = "tshark") -> list[dict] | None:
    """解析 tshark -D 获取抓包接口列表"""
    resolved_exe = resolve_tool_path(tshark_exe, _default_tshark_name())
    try:
        result = subprocess.run(
            [resolved_exe, "-D"], capture_output=True, text=False, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    interfaces = []
    for line in decode_text(result.stdout).splitlines():
        line = line.strip()
        if not line:
            continue
        # 格式: 1. \Device\NPF_{...} (描述)
        m = re.match(r"(\d+)\.\s+(.+?)(?:\s+\((.+?)\))?\s*$", line)
        if m:
            interfaces.append({
                "index": int(m.group(1)),
                "device": m.group(2).strip(),
                "description": m.group(3).strip() if m.group(3) else "",
            })
    return interfaces


def _prefix_to_mask(prefix: int) -> str:
    """将 CIDR 前缀长度转换为点分十进制子网掩码"""
    mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return ".".join(str((mask >> i) & 0xFF) for i in [24, 16, 8, 0])


def parse_ip_addr() -> list[dict]:
    """解析 ip addr show / ip route show 获取网络接口信息（Linux/WSL）"""
    import json as _json

    try:
        addr_result = subprocess.run(
            ["ip", "-j", "addr", "show"],
            capture_output=True, text=True, timeout=10,
        )
        if addr_result.returncode != 0:
            return []
        ifaces_raw = _json.loads(addr_result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, _json.JSONDecodeError, OSError):
        return []

    # 获取默认网关
    gateways: dict[str, str] = {}
    try:
        route_result = subprocess.run(
            ["ip", "-j", "route", "show"],
            capture_output=True, text=True, timeout=5,
        )
        if route_result.returncode == 0:
            for route in _json.loads(route_result.stdout):
                dev = route.get("dev", "")
                gw = route.get("gateway", "")
                if gw and dev and dev not in gateways:
                    gateways[dev] = gw
    except Exception:
        pass

    interfaces = []
    for iface in ifaces_raw:
        ifname = iface.get("ifname", "")
        operstate = iface.get("operstate", "unknown").lower()
        status = "up" if operstate in ("up", "unknown") else "down"
        mac = iface.get("address", "")

        ipv4_list: list[str] = []
        subnet_list: list[str] = []
        for addr_info in iface.get("addr_info", []):
            if addr_info.get("family") == "inet":
                ip = addr_info.get("local", "")
                prefix = addr_info.get("prefixlen", 24)
                if ip:
                    ipv4_list.append(ip)
                    subnet_list.append(_prefix_to_mask(prefix))

        gw = gateways.get(ifname, "")
        if "eth" in ifname or "en" in ifname:
            iface_type = "以太网"
        elif "wlan" in ifname or "wl" in ifname:
            iface_type = "WLAN"
        else:
            iface_type = "本地连接"

        interfaces.append({
            "type": iface_type,
            "name": ifname,
            "description": iface.get("qdisc", ""),
            "mac": mac,
            "ipv4": ipv4_list[0] if ipv4_list else "",
            "ipv4_list": ipv4_list,
            "subnet": subnet_list[0] if subnet_list else "",
            "subnet_list": subnet_list,
            "gateway": gw,
            "gateway_list": [gw] if gw else [],
            "dhcp": "",
            "status": status,
        })

    return interfaces


def parse_ipconfig() -> list[dict]:
    """解析网络接口信息：Linux/WSL 使用 ip addr，Windows 使用 ipconfig /all"""
    if sys.platform != "win32":
        return parse_ip_addr()

    try:
        result = subprocess.run(
            ["ipconfig", "/all"], capture_output=True, text=True, encoding="gbk", errors="replace"
        )
    except FileNotFoundError:
        return []

    interfaces = []
    current = None
    current_label = ""

    for line in result.stdout.splitlines():
        # 适配器标题行
        adapter_match = re.match(r"^(\S.*?)\s*适配器\s+(.+?)\s*[:：]", line)
        if not adapter_match:
            adapter_match = re.match(r"^(\S.*?)\s+adapter\s+(.+?)\s*[:：]", line, re.IGNORECASE)
        if adapter_match:
            if current:
                interfaces.append(current)
            current = {
                "type": adapter_match.group(1).strip(),
                "name": adapter_match.group(2).strip(),
                "description": "",
                "mac": "",
                "ipv4": "",
                "ipv4_list": [],
                "subnet": "",
                "subnet_list": [],
                "gateway": "",
                "gateway_list": [],
                "dhcp": "",
                "status": "up",
            }
            current_label = ""
            continue

        if current is None:
            continue

        line_stripped = line.strip()
        key, sep, value = line_stripped.partition(":")
        if not sep:
            key, sep, value = line_stripped.partition("：")
        key = key.strip()
        value = value.strip()
        continuation_value = value if sep else line_stripped

        if re.match(r"(媒体状态|Media State)", line_stripped, re.IGNORECASE):
            if "断开" in line_stripped or "disconnected" in line_stripped.lower():
                current["status"] = "down"
            current_label = ""
        elif re.match(r"(描述|Description)", line_stripped, re.IGNORECASE):
            current["description"] = value
            current_label = ""
        elif re.match(r"(物理地址|Physical Address)", line_stripped, re.IGNORECASE):
            current["mac"] = value
            current_label = ""
        elif re.match(r"(IPv4 地址|IPv4 Address)", line_stripped, re.IGNORECASE):
            ipv4 = re.sub(r"\(.*?\)", "", value).strip()
            if looks_like_ipv4(ipv4):
                current["ipv4_list"].append(ipv4)
                current["ipv4"] = current["ipv4_list"][0]
            current_label = "ipv4"
        elif re.match(r"(子网掩码|Subnet Mask)", line_stripped, re.IGNORECASE):
            if looks_like_ipv4(value):
                current["subnet_list"].append(value)
                current["subnet"] = current["subnet_list"][0]
            current_label = "subnet"
        elif re.match(r"(默认网关|Default Gateway)", line_stripped, re.IGNORECASE):
            if looks_like_ip(value):
                current["gateway_list"].append(value)
                current["gateway"] = current["gateway_list"][0]
            current_label = "gateway"
        elif re.match(r"DHCP", line_stripped, re.IGNORECASE) and ("已启用" in line_stripped or "Yes" in line_stripped):
            current["dhcp"] = "enabled"
            current_label = ""
        elif current_label == "gateway" and line.startswith(" ") and looks_like_ip(continuation_value):
            current["gateway_list"].append(continuation_value)
        elif current_label == "ipv4" and line.startswith(" ") and looks_like_ipv4(continuation_value):
            ipv4 = re.sub(r"\(.*?\)", "", continuation_value).strip()
            if ipv4:
                current["ipv4_list"].append(ipv4)
        elif current_label == "subnet" and line.startswith(" ") and looks_like_ipv4(continuation_value):
            current["subnet_list"].append(continuation_value)
        elif current_label in {"ipv4", "subnet", "gateway"} and value == "" and key:
            # 避免误判下一行标题
            current_label = ""

    for iface in interfaces + ([current] if current else []):
        if iface["ipv4_list"] and not iface["ipv4"]:
            iface["ipv4"] = iface["ipv4_list"][0]
        if iface["subnet_list"] and not iface["subnet"]:
            iface["subnet"] = iface["subnet_list"][0]
        if iface["gateway_list"] and not iface["gateway"]:
            iface["gateway"] = iface["gateway_list"][0]

    if current:
        interfaces.append(current)

    return interfaces


def get_net_config(
    cli_interface: str | None = None,
    cli_target: str | None = None,
    cli_capture_filter: str | None = None,
    cli_display_filter: str | None = None,
    cli_duration: int | None = None,
    cli_timeout_ms: int | None = None,
    cli_scan_ports: str | None = None,
    cli_capture_format: str | None = None,
    workspace: str | None = None,
) -> tuple[dict, dict]:
    """
    获取网络配置，按优先级解析参数。
    返回 (config_dict, sources_dict)
    """
    local_cfg = load_local_config()
    proj_cfg = load_project_config(workspace)
    state = load_workspace_state(workspace)

    sources = {}

    # 解析各个参数
    interface, src = resolve_param(
        "interface", cli_interface,
        project_config=proj_cfg, project_keys=["interface"],
        state=state, state_keys=["last_net_interface"],
    )
    sources["interface"] = src or "unknown"

    target, src = resolve_param(
        "target", cli_target,
        project_config=proj_cfg, project_keys=["target"],
        state=state, state_keys=["last_net_target"],
    )
    sources["target"] = src or "unknown"

    capture_filter, src = resolve_param(
        "capture_filter", cli_capture_filter,
        project_config=proj_cfg, project_keys=["capture_filter"],
        state=state, state_keys=["last_capture_filter"],
        default="",
    )
    sources["capture_filter"] = src or "default"

    display_filter, src = resolve_param(
        "display_filter", cli_display_filter,
        project_config=proj_cfg, project_keys=["display_filter"],
        state=state, state_keys=["last_display_filter"],
        default="",
    )
    sources["display_filter"] = src or "default"

    duration, src = resolve_param(
        "duration", cli_duration,
        project_config=proj_cfg, project_keys=["duration"],
        state=state, state_keys=["last_duration"],
        default=30,
    )
    sources["duration"] = src or "default"

    timeout_ms, src = resolve_param(
        "timeout_ms", cli_timeout_ms,
        project_config=proj_cfg, project_keys=["timeout_ms"],
        state=state, state_keys=["last_timeout_ms"],
        default=1000,
    )
    sources["timeout_ms"] = src or "default"

    scan_ports, src = resolve_param(
        "scan_ports", cli_scan_ports,
        project_config=proj_cfg, project_keys=["scan_ports"],
        state=state, state_keys=["last_scan_ports"],
        default="",
    )
    sources["scan_ports"] = src or "default"

    capture_format, src = resolve_param(
        "capture_format", cli_capture_format,
        project_config=proj_cfg, project_keys=["capture_format"],
        state=state, state_keys=["last_capture_format"],
        default="pcapng",
    )
    sources["capture_format"] = src or "default"

    log_dir, src = resolve_param(
        "log_dir", None,
        project_config=proj_cfg, project_keys=["log_dir"],
        default=".embeddedskills/logs/net",
    )
    sources["log_dir"] = src or "default"

    # 获取工具路径（环境级配置）
    default_tshark = _default_tshark_name()
    default_capinfos = "capinfos.exe" if sys.platform == "win32" else "capinfos"
    tshark_exe = resolve_tool_path(local_cfg.get("tshark_exe"), default_tshark)
    capinfos_exe = resolve_tool_path(local_cfg.get("capinfos_exe"), default_capinfos)

    config = {
        "interface": interface,
        "target": target,
        "capture_filter": capture_filter,
        "display_filter": display_filter,
        "duration": duration,
        "timeout_ms": timeout_ms,
        "scan_ports": scan_ports,
        "capture_format": capture_format,
        "log_dir": log_dir,
        "tshark_exe": tshark_exe,
        "capinfos_exe": capinfos_exe,
    }

    return config, sources


def output_json(data: dict, *, indent: int = 2) -> None:
    """输出 JSON 到 stdout"""
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=indent), flush=True)
