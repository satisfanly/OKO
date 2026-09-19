#!/usr/bin/env python3
# Copyright 2026 Satisfanly Ltd
#
# OKO OS is a product of Satisfanly Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at:
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Loginless local status dashboard for an installed OKO server."""

from __future__ import annotations

import asyncio
import glob
import json
import math
import socket
import subprocess
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import ClassVar



VENDORED_SITE = Path("/usr/share/oko-python/site-packages")
if VENDORED_SITE.is_dir():
    sys.path.insert(0, str(VENDORED_SITE))

REQUIRED_TEXTUAL_VERSION = "8.2.8"
try:
    TEXTUAL_VERSION = package_version("textual")
except PackageNotFoundError:
    print(
        f"error: OKO Dashboard requires textual=={REQUIRED_TEXTUAL_VERSION}",
        file=sys.stderr,
    )
    raise SystemExit(2)

if TEXTUAL_VERSION != REQUIRED_TEXTUAL_VERSION:
    print(
        "error: OKO Dashboard requires "
        f"textual=={REQUIRED_TEXTUAL_VERSION}; found textual=={TEXTUAL_VERSION}",
        file=sys.stderr,
    )
    raise SystemExit(2)

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Resize
from textual.widgets import ProgressBar, Static


MONITORED_SERVICES: tuple[tuple[str, str], ...] = (
    ("proxy-openai", "oko-ai-proxy-openai.service"),
    ("proxy-stream", "oko-ai-proxy-stream.service"),
    ("proxy", "oko-ai-proxy.service"),
)

MODEL_ACTIVITY_UNIT = "oko-ai-proxy.service"
MODEL_ACTIVITY_GREP = (
    r"became active from user request|"
    r"became idle \(no user requests\)|"
    r"Preempting model"
)
MODEL_ACTIVITY_NEEDLES = (
    "became active from user request",
    "became idle (no user requests)",
    "Preempting model",
)


SMU_METRICS: tuple[tuple[str, str, str, str, str], ...] = (
    ("stapm", "STAPM", "STAPM LIMIT", "STAPM VALUE", "W"),
    ("fast", "PPT FAST", "PPT LIMIT FAST", "PPT VALUE FAST", "W"),
    ("slow", "PPT SLOW", "PPT LIMIT SLOW", "PPT VALUE SLOW", "W"),
    ("apu", "APU PPT", "PPT LIMIT APU", "PPT VALUE APU", "W"),
    ("tctl", "TCTL", "THM LIMIT CORE", "THM VALUE CORE", "°C"),
)

RYZENADJ = "/usr/bin/ryzenadj"
AXB35_EC_BASE = Path("/sys/class/ec_su_axb35")


def read_text(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return default


def cpu_sample() -> tuple[int, int]:
    lines = read_text("/proc/stat").splitlines()
    if not lines:
        return (0, 0)
    values = [int(value) for value in lines[0].split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def cpu_percent(previous: tuple[int, int], current: tuple[int, int]) -> float:
    total = current[0] - previous[0]
    idle = current[1] - previous[1]
    return 0.0 if total <= 0 else max(0.0, min(100.0, 100.0 * (total - idle) / total))


def load_average() -> str:
    parts = read_text("/proc/loadavg").split()
    if len(parts) < 3:
        return "load unavailable"
    return f"load {parts[0]} / {parts[1]} / {parts[2]}"


def memory_status() -> tuple[float, str]:
    info: dict[str, int] = {}
    for line in read_text("/proc/meminfo").splitlines():
        key, _, value = line.partition(":")
        if value:
            info[key] = int(value.split()[0]) * 1024
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    used = max(0, total - available)
    percent = (used * 100.0 / total) if total else 0.0
    gib = 1024 ** 3
    return percent, f"{used / gib:.1f} / {total / gib:.1f} GiB"


def gpu_status() -> str:
    values: list[int] = []
    for path in glob.glob("/sys/class/drm/card*/device/gpu_busy_percent"):
        try:
            values.append(int(read_text(path)))
        except ValueError:
            continue
    if not values:
        return "N/A"
    return f"{sum(values) / len(values):.0f}%"


def gpu_memory_status() -> str:
    used_total = 0
    capacity_total = 0
    seen_devices: set[str] = set()

    for path in glob.glob("/sys/class/drm/card*/device/mem_info_vram_total"):
        device = str(Path(path).parent.resolve())
        if device in seen_devices:
            continue
        seen_devices.add(device)

        try:
            capacity = int(read_text(path))
            used = int(read_text(str(Path(path).with_name("mem_info_vram_used"))))
        except ValueError:
            continue
        if capacity <= 0:
            continue
        capacity_total += capacity
        used_total += max(0, used)

    if capacity_total <= 0:
        return "VRAM unavailable"

    gib = 1024 ** 3
    return f"{used_total / gib:.1f} / {capacity_total / gib:.1f} GiB VRAM"


def run_json(*command: str) -> list[dict]:
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=2
        )
        value = json.loads(result.stdout)
        return value if isinstance(value, list) else []
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []


def network_status() -> tuple[list[str], str | None]:
    lines: list[str] = []
    preferred: str | None = None
    ipv6_fallback: str | None = None
    for link in run_json("ip", "-j", "address", "show", "up"):
        name = str(link.get("ifname", "?"))
        if name == "lo":
            continue
        addresses: list[str] = []
        for addr in link.get("addr_info", []):
            local = addr.get("local")
            prefix = addr.get("prefixlen")
            if not local or addr.get("scope") == "host":
                continue
            addresses.append(f"{local}/{prefix}")
            if preferred is None and addr.get("family") == "inet":
                preferred = str(local)
            elif ipv6_fallback is None and addr.get("family") == "inet6":
                ipv6_fallback = str(local)
        state = str(link.get("operstate", "UNKNOWN")).lower()
        lines.append(f"{name}: {state}  {', '.join(addresses) if addresses else 'no address'}")
    if not lines:
        lines.append("No active network interface")
    return lines, preferred or ipv6_fallback


def default_gateway() -> str:
    for route in run_json("ip", "-j", "route", "show", "default"):
        via = route.get("gateway")
        dev = route.get("dev")
        if via:
            return f"{via} via {dev}"
    return "none"


def dns_servers() -> str:
    servers: list[str] = []
    for line in read_text("/run/systemd/resolve/resolv.conf").splitlines():
        if line.startswith("nameserver "):
            servers.append(line.split(maxsplit=1)[1])
    return ", ".join(servers) if servers else "none"



def cpu_model_name() -> str:
    for line in read_text("/proc/cpuinfo").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() == "model name":
            return value.strip()
    return ""


def is_ryzen_cpu() -> bool:
    return "ryzen" in cpu_model_name().lower()


def _parse_ryzenadj_table(output: str) -> dict[str, float | None]:
    """Parse the human-readable `ryzenadj -i` table by row name."""
    values: dict[str, float | None] = {}

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue

        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 2:
            continue

        name = columns[0]
        raw_value = columns[1]
        if not name or name == "Name":
            continue

        try:
            value = float(raw_value)
        except ValueError:
            values[name] = None
            continue

        values[name] = value if math.isfinite(value) else None

    return values


def ryzenadj_status() -> tuple[
    str,
    str,
    dict[str, tuple[float | None, float | None]],
]:
    """Return state, summary and (current, limit) values for tuned SMU controls."""
    if not is_ryzen_cpu():
        return (
            "unsupported",
            "Overclocking info is not supported on this system.",
            {},
        )

    try:
        result = subprocess.run(
            [RYZENADJ, "-i"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except FileNotFoundError:
        return "unavailable", "Ryzen detected, but ryzenadj is not installed.", {}
    except (OSError, subprocess.SubprocessError):
        return "error", "Unable to query Ryzen SMU telemetry.", {}

    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        return "error", "ryzenadj could not read SMU telemetry.", {}

    rows = _parse_ryzenadj_table(output)
    metrics: dict[str, tuple[float | None, float | None]] = {}
    for key, _label, limit_name, value_name, _unit in SMU_METRICS:
        limit = rows.get(limit_name)
        current = rows.get(value_name)
        if limit is not None or current is not None:
            metrics[key] = (current, limit)

    if not metrics:
        return (
            "unsupported",
            "Ryzen detected, but SMU limit telemetry is not supported here.",
            {},
        )

    family = "Ryzen"
    version = ""
    for line in output.splitlines():
        if line.startswith("CPU Family:"):
            family = line.partition(":")[2].strip() or family
        elif line.startswith("Version:"):
            version = line.partition(":")[2].strip()

    summary = family
    if version:
        summary += f"  •  ryzenadj {version}"

    return "supported", summary, metrics


def fan_status_lines() -> list[str]:
    """Return mode, level and RPM for fans exposed by ec_su_axb35."""
    fan_paths = sorted(
        (path for path in AXB35_EC_BASE.glob("fan[0-9]*") if path.is_dir()),
        key=lambda path: path.name,
    )
    if not fan_paths:
        return ["ec_su_axb35 fan telemetry unavailable"]

    lines: list[str] = []
    for fan in fan_paths:
        mode = read_text(str(fan / "mode"), "?")
        level = read_text(str(fan / "level"), "?")
        rpm = read_text(str(fan / "rpm"), "?")

        level_text = f"L{level}/5" if level not in ("", "?") else "L?"
        rpm_text = f"{rpm} RPM" if rpm not in ("", "?") else "RPM ?"
        lines.append(f"{fan.name:<5}  {mode:<6}  {level_text:<5}  {rpm_text}")

    return lines

def service_statuses() -> list[tuple[str, str, str, str, str]]:
    """Return label, unit, load state, active state and sub-state for monitored units."""
    units = [unit for _, unit in MONITORED_SERVICES]
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                "--no-pager",
                "--property=Id",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                *units,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return [
            (label, unit, "unknown", "unknown", "unknown")
            for label, unit in MONITORED_SERVICES
        ]

    records: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}

    def commit() -> None:
        unit_id = current.get("Id")
        if unit_id:
            records[unit_id] = dict(current)
        current.clear()

    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            commit()
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key == "Id" and current.get("Id"):
            commit()
        current[key] = value
    commit()

    return [
        (
            label,
            unit,
            records.get(unit, {}).get("LoadState", "unknown"),
            records.get(unit, {}).get("ActiveState", "unknown"),
            records.get(unit, {}).get("SubState", "unknown"),
        )
        for label, unit in MONITORED_SERVICES
    ]


def render_services(
    services: list[tuple[str, str, str, str, str]],
) -> tuple[str, int]:
    lines: list[str] = []
    running = 0

    for label, _unit, load_state, active_state, sub_state in services:
        if active_state == "active" and sub_state == "running":
            icon = "●"
            state = "RUNNING"
            color = "#5eead4"
            running += 1
        elif load_state == "not-found":
            icon = "×"
            state = "MISSING"
            color = "#f87171"
        elif active_state == "failed" or sub_state == "failed":
            icon = "×"
            state = "FAILED"
            color = "#f87171"
        elif active_state == "activating":
            icon = "◐"
            state = "STARTING"
            color = "#fbbf24"
        elif active_state == "deactivating":
            icon = "◐"
            state = "STOPPING"
            color = "#fbbf24"
        elif active_state == "inactive":
            icon = "○"
            state = "INACTIVE"
            color = "#fbbf24"
        else:
            icon = "?"
            state = (sub_state or active_state or "unknown").upper()[:8]
            color = "#94a3b8"

        lines.append(
            f"[{color}]{icon}[/{color}] "
            f"{label:<20} "
            f"[{color}]{state:>8}[/{color}]"
        )

    return "\n".join(lines), running


def model_activity_lines() -> list[str]:
    """Return the latest three model scheduler events from oko-ai-proxy."""
    base_command = [
        "journalctl",
        "-u",
        MODEL_ACTIVITY_UNIT,
        "--no-pager",
        "--quiet",
        "-o",
        "json",
    ]

    try:
        # Prefer journalctl's native MESSAGE filter so we only read the three
        # events we need.  Fall back to filtering a recent window in Python
        # if --grep is unavailable in the target systemd build.
        result = subprocess.run(
            [*base_command, "--grep", MODEL_ACTIVITY_GREP, "-n", "3"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )

        if result.returncode == 0:
            raw_records = result.stdout.splitlines()
        else:
            fallback = subprocess.run(
                [*base_command, "-n", "500"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if fallback.returncode != 0:
                return ["journalctl unavailable"]
            raw_records = fallback.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return ["journalctl unavailable"]

    events: list[str] = []
    for raw_record in raw_records:
        try:
            record = json.loads(raw_record)
        except json.JSONDecodeError:
            continue

        message = record.get("MESSAGE")
        if not isinstance(message, str):
            continue
        if not any(needle in message for needle in MODEL_ACTIVITY_NEEDLES):
            continue

        stamp = "-- -- --:--:--"
        realtime = record.get("__REALTIME_TIMESTAMP")
        try:
            if realtime is not None:
                stamp = datetime.fromtimestamp(
                    int(str(realtime)) / 1_000_000
                ).strftime("%b %d %H:%M:%S")
        except (TypeError, ValueError, OSError, OverflowError):
            pass

        events.append(f"{stamp}  {message}")

    return events[-3:] or ["No matching model activity yet."]


class OKODashboard(App[None]):
    """Loginless local status surface for an installed OKO server."""

    TITLE = "OKO Server Dashboard"
    SUB_TITLE = socket.gethostname()

    CSS = """
    Screen {
        width: 100%;
        height: 100%;
        align: center middle;
        background: #030914;
        color: #e8f0fb;
    }

    #shell {
        width: 96%;
        max-width: 140;
        height: auto;
        min-height: 24;
        max-height: 36;
        background: #081321;
        border: round #25415c;
    }

    #topbar {
        width: 100%;
        height: 3;
        padding: 0 1;
        background: #0b1a2b;
        border-bottom: solid #203850;
        content-align: left middle;
    }

    #brand {
        width: auto;
        height: 3;
        padding: 0 1;
        color: #f8fafc;
        text-style: bold;
        content-align: left middle;
    }

    #product-name {
        width: 1fr;
        height: 3;
        padding: 0 1;
        color: #8ea4bc;
        content-align: left middle;
    }

    #host-name {
        width: auto;
        height: 3;
        padding: 0 1;
        color: #a8b8ca;
        content-align: center middle;
    }

    #refresh-label {
        width: 11;
        height: 3;
        color: #5b8db8;
        text-align: center;
        content-align: center middle;
    }

    #link-badge {
        width: 10;
        height: 3;
        padding: 0 1;
        color: #061018;
        background: #fbbf24;
        text-style: bold;
        text-align: center;
        content-align: center middle;
    }

    #workspace {
        width: 100%;
        height: 20;
        padding: 1 2;
    }

    #left-column {
        width: 1fr;
        height: 100%;
        margin-right: 1;
    }

    #right-column {
        width: 42;
        min-width: 42;
        height: 100%;
    }

    #health-grid {
        width: 100%;
        height: 6;
        margin-bottom: 1;
    }

    .metric-card {
        width: 1fr;
        height: 6;
        margin-right: 1;
        padding: 0 1;
        background: #0c1c2e;
        border: round #24435e;
    }

    .metric-card.last {
        margin-right: 0;
    }

    .metric-label {
        width: 100%;
        height: 1;
        color: #71889f;
        text-style: bold;
    }

    .metric-value {
        width: 100%;
        height: 1;
        color: #f8fafc;
        text-style: bold;
    }

    .metric-detail {
        width: 100%;
        height: 1;
        color: #8fa4ba;
    }

    .metric-bar {
        width: 100%;
        height: 1;
    }

    ProgressBar > .bar--bar,
    ProgressBar > .bar--complete,
    ProgressBar > .bar--indeterminate {
        color: #5eead4;
    }

    #lower-grid {
        width: 100%;
        height: 1fr;
    }

    #network-card, #services-card, #overclock-card, #activity-card {
        height: 100%;
        padding: 1;
        background: #0a1929;
        border: round #29455f;
    }

    #network-card {
        width: 3fr;
        margin-right: 1;
    }

    #services-card {
        width: 2fr;
    }

    .card-kicker {
        width: 100%;
        height: 1;
        color: #5eead4;
        text-style: bold;
    }

    .card-title {
        width: 100%;
        height: 1;
        color: #f8fafc;
        text-style: bold;
    }

    #network-lines, #service-lines {
        width: 100%;
        height: 1fr;
        padding: 0 1;
        color: #b9c8d9;
        background: #07131f;
    }

    #network-lines {
        border-left: thick #8b7cf6;
    }

    #service-lines {
        border-left: thick #5eead4;
    }

    #overclock-card {
        width: 100%;
    }

    #oc-summary {
        width: 100%;
        height: 1;
        color: #9fb1c5;
        margin-bottom: 1;
    }

    .oc-metric {
        width: 100%;
        height: 1;
    }

    .oc-value {
        width: 24;
        height: 1;
        color: #d8e5f2;
    }

    .oc-bar {
        width: 1fr;
        height: 1;
    }

    /* Textual ProgressBar contains an inner Bar widget.  Force that child
       to consume the full remaining row width, otherwise its intrinsic
       width can make short percentages look incorrectly scaled. */
    .oc-bar Bar {
        width: 100%;
    }

    .oc-bar Bar > .bar--bar,
    .oc-bar Bar > .bar--complete,
    .oc-bar Bar > .bar--indeterminate {
        color: #8b7cf6;
    }

    #fan-title {
        width: 100%;
        height: 1;
        margin-top: 1;
        color: #5eead4;
        text-style: bold;
    }

    #fan-lines {
        width: 100%;
        height: 1fr;
        padding: 0 1;
        color: #b9c8d9;
        background: #07131f;
        border-left: thick #8b7cf6;
    }

    #activity-card {
        width: 1fr;
        height: 6;
        margin: 0 2 1 2;
        padding: 0 1;
    }

    #activity-lines {
        width: 100%;
        height: 3;
        padding: 0 1;
        color: #b9c8d9;
        background: #07131f;
        border-left: thick #fbbf24;
    }

    #footer {
        width: 100%;
        height: 2;
        padding: 0 2;
        color: #71869d;
        background: #050d17;
        border-top: solid #172a3e;
        text-align: center;
        text-style: bold;
        content-align: center middle;
    }

    #shell.compact #host-name {
        display: none;
    }

    #shell.compact #right-column {
        width: 36;
        min-width: 36;
    }

    #shell.short #workspace {
        height: 16;
        padding-top: 0;
        padding-bottom: 0;
    }

    #shell.short #health-grid {
        height: 5;
    }

    #shell.short .metric-card {
        height: 5;
    }

    #shell.short .metric-detail {
        display: none;
    }

    #shell.short #oc-summary {
        margin-bottom: 0;
    }

    #shell.short #activity-card {
        height: 5;
        margin-bottom: 0;
    }

    #shell.short #activity-lines {
        height: 2;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f10", "exit_dashboard", "Close", priority=True, show=False),
        Binding("ctrl+c", "noop", "Dashboard is managed by systemd", priority=True, show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.previous_cpu = cpu_sample()

    def compose(self) -> ComposeResult:
        with Container(id="shell"):
            with Horizontal(id="topbar"):
                yield Static("[#5eead4]OKO[/#5eead4]", id="brand")
                yield Static("SERVER DASHBOARD", id="product-name")
                yield Static(socket.gethostname(), id="host-name", markup=False)
                yield Static("LIVE / 1s", id="refresh-label")
                yield Static("STARTING", id="link-badge")

            with Horizontal(id="workspace"):
                with Vertical(id="left-column"):
                    with Horizontal(id="health-grid"):
                        with Vertical(classes="metric-card"):
                            yield Static("PROCESSOR", classes="metric-label")
                            yield Static("0.0%", id="cpu-value", classes="metric-value", markup=False)
                            yield ProgressBar(
                                total=100,
                                show_percentage=False,
                                show_eta=False,
                                id="cpu-bar",
                                classes="metric-bar",
                            )
                            yield Static("load —", id="cpu-detail", classes="metric-detail", markup=False)

                        with Vertical(classes="metric-card"):
                            yield Static("MEMORY", classes="metric-label")
                            yield Static("0.0%", id="ram-value", classes="metric-value", markup=False)
                            yield ProgressBar(
                                total=100,
                                show_percentage=False,
                                show_eta=False,
                                id="ram-bar",
                                classes="metric-bar",
                            )
                            yield Static("reading totals", id="ram-detail", classes="metric-detail", markup=False)

                        with Vertical(classes="metric-card last"):
                            yield Static("GRAPHICS", classes="metric-label")
                            yield Static("N/A", id="gpu-value", classes="metric-value", markup=False)
                            yield ProgressBar(
                                total=100,
                                show_percentage=False,
                                show_eta=False,
                                id="gpu-bar",
                                classes="metric-bar",
                            )
                            yield Static("VRAM —", id="gpu-detail", classes="metric-detail", markup=False)

                    with Horizontal(id="lower-grid"):
                        with Vertical(id="network-card"):
                            yield Static("NETWORK", classes="card-kicker")
                            yield Static("Interfaces / routing", classes="card-title")
                            yield Static("Waiting for an active interface…", id="network-lines", markup=False)

                        with Vertical(id="services-card"):
                            yield Static("OKO AI SERVICES", classes="card-kicker")
                            yield Static("Checking units…", id="service-summary", classes="card-title", markup=False)
                            yield Static("Waiting for systemd…", id="service-lines")

                with Vertical(id="right-column"):
                    with Vertical(id="overclock-card"):
                        yield Static("OVERCLOCKING", classes="card-kicker")
                        yield Static(
                            "Checking Ryzen SMU…",
                            id="oc-summary",
                            markup=False,
                        )

                        for key, label, _limit_name, _value_name, _unit in SMU_METRICS:
                            with Horizontal(classes="oc-metric"):
                                yield Static(
                                    f"{label:<8}  — / —",
                                    id=f"oc-{key}-value",
                                    classes="oc-value",
                                    markup=False,
                                )
                                yield ProgressBar(
                                    total=100,
                                    show_percentage=False,
                                    show_eta=False,
                                    id=f"oc-{key}-bar",
                                    classes="oc-bar",
                                )

                        yield Static("FANS", id="fan-title")
                        yield Static(
                            "Checking AXB35 EC…",
                            id="fan-lines",
                            markup=False,
                        )

            with Vertical(id="activity-card"):
                yield Static(
                    "MODEL ACTIVITY   latest 3 scheduler events",
                    classes="card-kicker",
                )
                yield Static(
                    "Waiting for journal…",
                    id="activity-lines",
                    markup=False,
                )

            yield Static(
                "F10  CLOSE    •    ALT+F2  LOCAL LOGIN    •    SSH USES THE INSTALLED ADMINISTRATOR ACCOUNT",
                id="footer",
            )

    async def on_mount(self) -> None:
        await self.refresh_status()
        await self.refresh_activity()
        self.set_interval(1.0, self.refresh_status)
        self.set_interval(2.0, self.refresh_activity)

    def on_resize(self, event: Resize) -> None:
        shell = self.query_one("#shell", Container)
        shell.set_class(event.size.width < 108, "compact")
        shell.set_class(event.size.height < 28, "short")

    def _sample_status(
        self,
    ) -> tuple[
        float,
        str,
        float,
        str,
        str,
        str,
        list[str],
        str | None,
        str,
        str,
        list[tuple[str, str, str, str, str]],
        str,
        str,
        dict[str, tuple[float | None, float | None]],
        list[str],
    ]:
        current = cpu_sample()
        current_cpu = cpu_percent(self.previous_cpu, current)
        self.previous_cpu = current
        cpu_load = load_average()
        ram_percent, ram_text = memory_status()
        gpu_text = gpu_status()
        gpu_memory = gpu_memory_status()
        network, address = network_status()
        services = service_statuses()
        oc_state, oc_summary, oc_metrics = ryzenadj_status()
        fans = fan_status_lines()
        return (
            current_cpu,
            cpu_load,
            ram_percent,
            ram_text,
            gpu_text,
            gpu_memory,
            network,
            address,
            default_gateway(),
            dns_servers(),
            services,
            oc_state,
            oc_summary,
            oc_metrics,
            fans,
        )

    async def refresh_status(self) -> None:
        (
            current_cpu,
            cpu_load,
            ram_percent,
            ram_text,
            gpu_text,
            gpu_memory,
            network,
            address,
            gateway,
            dns,
            services,
            oc_state,
            oc_summary,
            oc_metrics,
            fans,
        ) = await asyncio.to_thread(self._sample_status)

        self.query_one("#cpu-value", Static).update(f"{current_cpu:.1f}%")
        self.query_one("#cpu-bar", ProgressBar).progress = current_cpu
        self.query_one("#cpu-detail", Static).update(cpu_load)

        self.query_one("#ram-value", Static).update(f"{ram_percent:.1f}%")
        self.query_one("#ram-bar", ProgressBar).progress = ram_percent
        self.query_one("#ram-detail", Static).update(ram_text)

        gpu_percent_value = 0.0
        if gpu_text.endswith("%"):
            try:
                gpu_percent_value = float(gpu_text[:-1])
            except ValueError:
                gpu_percent_value = 0.0
        self.query_one("#gpu-value", Static).update(
            gpu_text if gpu_text.endswith("%") else "N/A"
        )
        self.query_one("#gpu-bar", ProgressBar).progress = gpu_percent_value
        self.query_one("#gpu-detail", Static).update(gpu_memory)

        network_text = "\n".join(
            [*network[:4], f"gateway  {gateway}", f"dns      {dns}"]
        )
        self.query_one("#network-lines", Static).update(network_text)

        service_text, running = render_services(services)
        service_summary = self.query_one("#service-summary", Static)
        service_summary.update(f"{running}/{len(MONITORED_SERVICES)} running")
        service_summary.styles.color = (
            "#f8fafc" if running == len(MONITORED_SERVICES) else "#fbbf24"
        )
        self.query_one("#service-lines", Static).update(service_text)

        badge = self.query_one("#link-badge", Static)
        if address:
            badge.update("ONLINE")
            badge.styles.background = "#5eead4"
        else:
            badge.update("OFFLINE")
            badge.styles.background = "#fbbf24"

        oc_summary_widget = self.query_one("#oc-summary", Static)
        oc_summary_widget.update(oc_summary)
        if oc_state == "supported":
            oc_summary_widget.styles.color = "#9fb1c5"
        elif oc_state == "unsupported":
            oc_summary_widget.styles.color = "#fbbf24"
        else:
            oc_summary_widget.styles.color = "#f87171"

        for key, label, _limit_name, _value_name, unit in SMU_METRICS:
            current_value, limit_value = oc_metrics.get(key, (None, None))
            value_widget = self.query_one(f"#oc-{key}-value", Static)
            bar = self.query_one(f"#oc-{key}-bar", ProgressBar)

            if oc_state != "supported" or limit_value is None:
                value_widget.update(f"{label:<8}  not available")
                bar.update(total=1.0, progress=0.0)
                continue

            current_text = "N/A" if current_value is None else f"{current_value:.1f}"
            value_widget.update(
                f"{label:<8}  {current_text} / {limit_value:.1f} {unit}"
            )

            if current_value is None or limit_value <= 0:
                bar.update(total=1.0, progress=0.0)
            else:
                # Give Textual the real range instead of pre-scaling to 0..100.
                # This keeps every bar proportional to current / limit and lets
                # the widget perform the fraction calculation itself.
                bar.update(
                    total=limit_value,
                    progress=max(0.0, min(current_value, limit_value)),
                )

        fan_text = "\n".join(fans)
        if oc_state == "unsupported":
            fan_text = "Fan telemetry is not supported on this system."
        self.query_one("#fan-lines", Static).update(fan_text)

    async def refresh_activity(self) -> None:
        lines = await asyncio.to_thread(model_activity_lines)
        self.query_one("#activity-lines", Static).update("\n".join(lines[-3:]))

    def action_noop(self) -> None:
        pass

    def action_exit_dashboard(self) -> None:
        self.exit()


if __name__ == "__main__":
    OKODashboard().run()


