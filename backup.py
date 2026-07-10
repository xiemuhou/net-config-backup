#!/usr/bin/env python3
"""Network device configuration backup tool.

Supports Huawei, Cisco IOS, and Fortinet devices through Netmiko.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException


DEVICE_TYPE_ALIASES = {
    "cisco": "cisco_ios",
    "cisco_ac": "cisco_wlc",
    "h3c": "hp_comware",
    "huawei_ac": "huawei",
    "sonicwall": "generic",
    "sundray": "generic",
}

DEVICE_TYPE_TRANSPORT_ALIASES = {
    ("cisco_ac", "telnet"): "generic_telnet",
    ("sonicwall", "telnet"): "generic_telnet",
    ("sundray", "telnet"): "generic_telnet",
}

DEFAULT_COMMANDS = {
    "huawei": "display current-configuration",
    "huawei_ac": "display current-configuration",
    "cisco": "show running-config",
    "cisco_ac": "show run-config",
    "fortinet": "show full-configuration",
    "h3c": "display current-configuration",
    "sonicwall": "show current-config",
    "sundray": "list running_config",
}

DEFAULT_PRE_COMMANDS = {
    "cisco_ac": ["config paging disable"],
    "huawei": ["screen-length 0 temporary"],
    "huawei_ac": ["screen-length 0 temporary"],
    "h3c": ["screen-length disable"],
    "sonicwall": ["no cli pager session"],
}

DEFAULT_COMMAND_METHODS = {
    "sundray": "timing",
}

DEFAULT_MORE_PATTERNS = {
}

DEFAULT_MORE_RESPONSES = {
}

DEFAULT_CONFIRM_PATTERNS = {
    "cisco_ac": r"Press Enter to continue(?: or <ctrl-z> to abort)?",
}

DEFAULT_CONFIRM_RESPONSES = {
    "cisco_ac": "\n",
}

DEFAULT_READ_TIMEOUTS = {
    "cisco_ac": 600,
    "sonicwall": 300,
}


def netmiko_device_type(device_type: str, transport: str) -> str:
    exact_type = DEVICE_TYPE_TRANSPORT_ALIASES.get((device_type, transport))
    if exact_type:
        return exact_type
    base_type = DEVICE_TYPE_ALIASES.get(device_type, device_type)
    if transport == "telnet" and not base_type.endswith("_telnet"):
        return f"{base_type}_telnet"
    return base_type


def default_port(transport: str) -> int:
    return 23 if transport == "telnet" else 22


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up Huawei, Cisco, and Fortinet device configurations."
    )
    parser.add_argument(
        "-c",
        "--config",
        default="devices.yaml",
        help="Path to devices.yaml. Default: devices.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and show target devices without connecting.",
    )
    return parser.parse_args()


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        raise ValueError("Top-level YAML value must be a mapping.")

    return data


def load_devices(config: dict[str, Any]) -> list[dict[str, Any]]:
    devices = config.get("devices", [])

    if isinstance(devices, list):
        if not devices:
            raise ValueError("devices must be a non-empty list or mapping.")
        if not all(isinstance(device, dict) for device in devices):
            raise ValueError("Each item in devices must be a mapping.")
        return devices

    if isinstance(devices, dict):
        expanded: list[dict[str, Any]] = []
        for region, region_devices in devices.items():
            if not isinstance(region_devices, list) or not region_devices:
                raise ValueError(f"devices.{region} must be a non-empty list.")
            for device in region_devices:
                if not isinstance(device, dict):
                    raise ValueError(f"Each item in devices.{region} must be a mapping.")
                expanded_device = {"region": str(region)}
                expanded_device.update(device)
                expanded.append(expanded_device)
        return expanded

    raise ValueError("devices must be a non-empty list or mapping.")


def setup_logging(log_dir: Path, log_level: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"backup-{datetime.now():%Y-%m-%d}.log"

    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def sanitize_filename(value: str) -> str:
    value = value.strip().replace(" ", "_")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def env_or_value(device: dict[str, Any], key: str) -> Any:
    env_key = f"{key}_env"
    if env_key in device:
        env_name = str(device[env_key])
        value = os.getenv(env_name)
        if not value:
            raise ValueError(f"Environment variable {env_name} is not set.")
        return value
    return device.get(key)


def build_connection_params(device: dict[str, Any]) -> dict[str, Any]:
    required = ["host", "device_type", "username"]
    missing = [key for key in required if not device.get(key)]
    if missing:
        raise ValueError(f"Device is missing required fields: {', '.join(missing)}")

    transport = str(device.get("transport", "ssh")).lower()
    if transport not in {"ssh", "telnet"}:
        raise ValueError("transport must be ssh or telnet.")

    params: dict[str, Any] = {
        "device_type": netmiko_device_type(str(device["device_type"]), transport),
        "host": str(device["host"]),
        "username": str(device["username"]),
        "port": int(device.get("port", default_port(transport))),
        "timeout": int(device.get("timeout", 30)),
        "conn_timeout": int(device.get("conn_timeout", 20)),
        "banner_timeout": int(device.get("banner_timeout", 20)),
        "auth_timeout": int(device.get("auth_timeout", 20)),
        "fast_cli": bool(device.get("fast_cli", False)),
    }

    password = env_or_value(device, "password")
    if password:
        params["password"] = str(password)

    secret = env_or_value(device, "secret")
    if secret:
        params["secret"] = str(secret)

    if device.get("use_keys"):
        params["use_keys"] = True
        params["key_file"] = str(device.get("key_file", ""))

    return params


def get_backup_commands(device: dict[str, Any]) -> list[str]:
    if device.get("backup_commands"):
        commands = device["backup_commands"]
    elif device.get("backup_command"):
        commands = [device["backup_command"]]
    else:
        command = DEFAULT_COMMANDS.get(str(device["device_type"]))
        if not command:
            raise ValueError(
                f"No default backup command for device_type={device['device_type']}. "
                "Set backup_command or backup_commands for this device."
            )
        commands = [command]

    if isinstance(commands, str):
        return [commands]
    if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
        raise ValueError("backup_commands must be a string list.")
    return commands


def get_pre_commands(device: dict[str, Any]) -> list[str]:
    if "pre_backup_commands" in device:
        commands = device["pre_backup_commands"]
    else:
        commands = DEFAULT_PRE_COMMANDS.get(str(device["device_type"]), [])

    if isinstance(commands, str):
        return [commands]
    if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
        raise ValueError("pre_backup_commands must be a string list.")
    return commands


def command_method(device: dict[str, Any]) -> str:
    method = str(
        device.get(
            "command_method",
            DEFAULT_COMMAND_METHODS.get(str(device["device_type"]), "pattern"),
        )
    ).lower()
    if method not in {"pattern", "timing"}:
        raise ValueError("command_method must be pattern or timing.")
    return method


def confirm_pattern(device: dict[str, Any]) -> str | None:
    pattern = device.get(
        "confirm_pattern",
        DEFAULT_CONFIRM_PATTERNS.get(str(device["device_type"])),
    )
    return str(pattern) if pattern else None


def run_show_command(connection: Any, command: str, device: dict[str, Any]) -> str:
    method = command_method(device)
    read_timeout = int(
        device.get(
            "read_timeout",
            DEFAULT_READ_TIMEOUTS.get(str(device["device_type"]), 120),
        )
    )
    command_confirm_pattern = confirm_pattern(device)

    if method == "pattern" and command_confirm_pattern:
        output = connection.send_command_timing(
            command,
            read_timeout=read_timeout,
            last_read=float(device.get("confirm_last_read", 1.0)),
        )
        if re.search(command_confirm_pattern, output):
            confirm_response = str(
                device.get(
                    "confirm_response",
                    DEFAULT_CONFIRM_RESPONSES.get(str(device["device_type"]), "\n"),
                )
            )
            logging.info(
                "Confirmation prompt detected on %s, sending response",
                device.get("host", "?"),
            )
            logging.info(
                "Reading command output from %s after confirmation, timeout=%ss",
                device.get("host", "?"),
                read_timeout,
            )
            output += connection.send_command_timing(
                confirm_response,
                strip_prompt=False,
                strip_command=False,
                read_timeout=read_timeout,
                last_read=float(device.get("last_read", 2.0)),
            )
            logging.info(
                "Command output read completed from %s after confirmation",
                device.get("host", "?"),
            )
        return re.sub(command_confirm_pattern, "", output)

    if method == "timing":
        output = connection.send_command_timing(
            command,
            read_timeout=read_timeout,
            last_read=float(device.get("last_read", 2.0)),
        )
        more_pattern = device.get(
            "more_pattern",
            DEFAULT_MORE_PATTERNS.get(str(device["device_type"])),
        )
        if not more_pattern:
            return output

        more_response = str(
            device.get(
                "more_response",
                DEFAULT_MORE_RESPONSES.get(str(device["device_type"]), " "),
            )
        )
        max_more_pages = int(device.get("max_more_pages", 500))
        more_last_read = float(device.get("more_last_read", 0.5))
        page_count = 0

        while re.search(str(more_pattern), output) and page_count < max_more_pages:
            page_count += 1
            logging.info(
                "Pagination prompt detected on %s, sending response page=%s",
                device.get("host", "?"),
                page_count,
            )
            more_output = connection.send_command_timing(
                more_response,
                strip_prompt=False,
                strip_command=False,
                read_timeout=read_timeout,
                last_read=more_last_read,
            )
            output += more_output

        if page_count >= max_more_pages and re.search(str(more_pattern), output):
            raise RuntimeError(
                f"Pagination did not finish after {max_more_pages} pages for command: {command}"
            )

        return re.sub(str(more_pattern), "", output)

    return connection.send_command(
        command,
        read_timeout=read_timeout,
        expect_string=device.get("expect_string"),
    )


def read_telnet_until_pattern(
    connection: Any,
    pattern: str,
    timeout: int,
    encoding: str,
) -> str:
    deadline = time.time() + timeout
    output = ""
    compiled = re.compile(pattern)

    while time.time() < deadline:
        chunk = connection.read_very_eager().decode(encoding, "ignore")
        if chunk:
            output += chunk
            if compiled.search(output):
                return output
        time.sleep(0.2)

    raise TimeoutError(f"Timed out waiting for telnet pattern: {pattern}")


def read_sundray_telnet_command(
    connection: Any,
    command: str,
    device: dict[str, Any],
) -> str:
    encoding = str(device.get("encoding", "utf-8"))
    prompt_pattern = str(device.get("expect_string", r"Switch#"))
    more_pattern = str(device.get("more_pattern", r"--MORE--|--More--|--more--"))
    more_response = str(device.get("more_response", " "))
    read_timeout = int(device.get("read_timeout", 180))
    last_read = float(device.get("last_read", 8))
    max_more_pages = int(device.get("max_more_pages", 1000))

    connection.write(command.encode(encoding) + b"\n")
    deadline = time.time() + read_timeout
    last_data_time = time.time()
    output = ""
    page_count = 0
    prompt_re = re.compile(prompt_pattern)
    more_re = re.compile(more_pattern)

    while time.time() < deadline:
        chunk = connection.read_very_eager().decode(encoding, "ignore")
        if chunk:
            output += chunk
            last_data_time = time.time()

            if more_re.search(output):
                if page_count >= max_more_pages:
                    raise RuntimeError(
                        f"Pagination did not finish after {max_more_pages} pages for command: {command}"
                    )
                connection.write(more_response.encode(encoding))
                output = more_re.sub("", output)
                page_count += 1
                continue

            if prompt_re.search(output):
                return output
        elif output and time.time() - last_data_time >= last_read:
            return output

        time.sleep(0.2)

    raise TimeoutError(f"Timed out reading telnet command output: {command}")


def backup_sundray_telnet(device: dict[str, Any], commands: list[str], pre_commands: list[str]) -> str:
    import telnetlib

    host = str(device["host"])
    username = str(device["username"])
    password = env_or_value(device, "password")
    if not password:
        raise ValueError("Sundray Telnet device requires password or password_env.")

    port = int(device.get("port", default_port("telnet")))
    timeout = int(device.get("timeout", 30))
    encoding = str(device.get("encoding", "utf-8"))
    username_pattern = str(
        device.get("username_pattern", r"(?:\(none\)\s*)?(?:login|Login|Username|username):")
    )
    password_pattern = str(device.get("password_pattern", r"(?:Password|password):"))
    prompt_pattern = str(device.get("expect_string", r"Switch#"))

    with telnetlib.Telnet(host, port, timeout) as connection:
        read_telnet_until_pattern(connection, username_pattern, timeout, encoding)
        connection.write(username.encode(encoding) + b"\n")
        read_telnet_until_pattern(connection, password_pattern, timeout, encoding)
        connection.write(str(password).encode(encoding) + b"\n")
        read_telnet_until_pattern(connection, prompt_pattern, timeout, encoding)

        outputs = []
        for command in pre_commands:
            read_sundray_telnet_command(connection, command, device)

        for command in commands:
            logging.info("Running command on %s: %s", host, command)
            output = read_sundray_telnet_command(connection, command, device)
            outputs.append(f"! ===== COMMAND: {command} =====\n{output}\n")

    return "\n".join(outputs)


def save_backup(
    backup_root: Path,
    region: str,
    host: str,
    device_type: str,
    role: str,
    content: str,
    extension: str = "cfg",
) -> Path:
    now = datetime.now()
    safe_region = sanitize_filename(region)
    safe_host = sanitize_filename(host)
    safe_type = sanitize_filename(device_type)
    safe_role = sanitize_filename(role)
    region_dir = backup_root / safe_region
    region_dir.mkdir(parents=True, exist_ok=True)

    file_name = f"{now:%Y-%m-%d_%H%M%S}_{safe_type}_{safe_role}_{safe_host}.{extension}"
    target = region_dir / file_name
    target.write_text(content, encoding="utf-8", newline="\n")
    return target


def backup_device(device: dict[str, Any], backup_root: Path) -> Path:
    host = str(device["host"])
    device_type = str(device["device_type"])
    transport = str(device.get("transport", "ssh")).lower()
    region = str(device.get("region", "default"))
    role = str(device.get("role", "device"))
    logging.info(
        "Starting backup: region=%s host=%s type=%s transport=%s role=%s",
        region,
        host,
        device_type,
        transport,
        role,
    )

    params = build_connection_params(device)
    commands = get_backup_commands(device)
    pre_commands = get_pre_commands(device)
    extension = str(device.get("file_extension", "cfg"))

    if device_type == "sundray" and transport == "telnet":
        command_output = backup_sundray_telnet(device, commands, pre_commands)
    else:
        with ConnectHandler(**params) as connection:
            if params.get("secret"):
                connection.enable()

            for command in pre_commands:
                connection.send_command(command, expect_string=device.get("expect_string"))

            outputs = []
            for command in commands:
                logging.info("Running command on %s: %s", host, command)
                output = run_show_command(connection, command, device)
                outputs.append(f"! ===== COMMAND: {command} =====\n{output}\n")
        command_output = "\n".join(outputs)

    header = (
        f"! Host: {host}\n"
        f"! Type: {device_type}\n"
        f"! Transport: {transport}\n"
        f"! Region: {region}\n"
        f"! Role: {role}\n"
        f"! Backup time: {datetime.now().isoformat(timespec='seconds')}\n\n"
    )
    backup_file = save_backup(
        backup_root,
        region,
        host,
        device_type,
        role,
        header + command_output,
        extension,
    )

    if backup_file.stat().st_size < int(device.get("min_backup_bytes", 100)):
        raise RuntimeError(f"Backup file is suspiciously small: {backup_file}")

    logging.info("Backup saved: %s", backup_file)
    return backup_file


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()
    config = load_yaml(config_path)

    base_dir = config_path.parent
    backup_root = (base_dir / config.get("backup_dir", "backups")).resolve()
    log_dir = (base_dir / config.get("log_dir", "logs")).resolve()
    log_level = str(config.get("log_level", "INFO"))
    devices = load_devices(config)

    setup_logging(log_dir, log_level)
    logging.info("Loaded config: %s", config_path)
    logging.info("Backup directory: %s", backup_root)

    if args.dry_run:
        for device in devices:
            params = build_connection_params(device)
            commands = get_backup_commands(device)
            logging.info(
                "DRY RUN region=%s host=%s type=%s transport=%s netmiko_type=%s method=%s commands=%s",
                device.get("region", "default"),
                params["host"],
                device["device_type"],
                device.get("transport", "ssh"),
                params["device_type"],
                command_method(device),
                commands,
            )
        return 0

    success = 0
    failed = 0

    for device in devices:
        try:
            backup_device(device, backup_root)
            success += 1
        except (NetmikoAuthenticationException, NetmikoTimeoutException) as exc:
            failed += 1
            logging.exception("Connection failed for %s: %s", device.get("host", "?"), exc)
        except Exception as exc:
            failed += 1
            logging.exception("Backup failed for %s: %s", device.get("host", "?"), exc)

    logging.info("Completed. success=%s failed=%s", success, failed)

    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.exception("Fatal error: %s", exc)
        raise SystemExit(2)
