#!/usr/bin/env python3
"""UI smoke test for oko-dashboard with injected dummy data.

Mounts the dashboard, mocks all system data sources, and renders
the terminal output so you can visually verify the layout.

Usage:
    python3 test_dashboard_ui.py          # print rendered UI to stdout
    python3 test_dashboard_ui.py --screenshot out.png  # save PNG (needs textual[pilot])
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

# Load oko-dashboard.py (hyphenated name) via importlib
SRC = Path(__file__).resolve().parent.parent / "files" / "oko-dashboard.py"
spec = importlib.util.spec_from_file_location("oko_dashboard", SRC)
dash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dash)


def make_dummy_data():
    """Return a dict of dummy values for every data source."""
    return {
        "cpu_percent": 42.7,
        "cpu_load": "load 1.23 / 0.98 / 0.76",
        "ram_percent": 67.3,
        "ram_text": "10.8 / 16.0 GiB",
        "gpu_text": "78%",
        "gpu_memory": "4.2 / 8.0 GiB VRAM",
        "network": [
            "eth0: UP  192.168.1.42/24",
            "wlan0: UP  10.0.0.5/24",
            "lo: UP  127.0.0.1/8",
        ],
        "address": "192.168.1.42",
        "gateway": "192.168.1.1 via eth0",
        "dns": "1.1.1.1, 8.8.8.8",
        "services": [
            ("proxy-openai", "oko-ai-proxy-openai.service", "loaded", "active", "running"),
            ("proxy-stream", "oko-ai-proxy-stream.service", "loaded", "active", "running"),
            ("proxy", "oko-ai-proxy.service", "loaded", "failed", "failed"),
        ],
        "oc_state": "supported",
        "oc_summary": "Ryzen 7 5800X  •  ryzenadj 0.12.1",
        "oc_metrics": {
            "stapm": (65.2, 105.0),
            "fast": (88.1, 142.0),
            "slow": (95.0, 142.0),
            "apu": (45.0, 88.0),
            "tctl": (72.3, 95.0),
        },
        "fans": [
            "fan0   auto    L3/5    2450 RPM",
            "fan1   auto    L2/5    1800 RPM",
        ],
        "activity": [
            "Sep 21 19:05:12  Model became active from user request",
            "Sep 21 19:04:58  Preempting model: llama-7b -> mistral-7b",
            "Sep 21 19:03:41  Model became idle (no user requests)",
        ],
    }


def patch_all(data):
    """Patch every data-source function in the dashboard module."""
    patches = [
        patch.object(dash, "cpu_sample", return_value=(1000, 573)),
        patch.object(dash, "cpu_percent", return_value=data["cpu_percent"]),
        patch.object(dash, "load_average", return_value=data["cpu_load"]),
        patch.object(dash, "memory_status", return_value=(data["ram_percent"], data["ram_text"])),
        patch.object(dash, "gpu_status", return_value=data["gpu_text"]),
        patch.object(dash, "gpu_memory_status", return_value=data["gpu_memory"]),
        patch.object(dash, "network_status", return_value=(data["network"], data["address"])),
        patch.object(dash, "default_gateway", return_value=data["gateway"]),
        patch.object(dash, "dns_servers", return_value=data["dns"]),
        patch.object(dash, "service_statuses", return_value=data["services"]),
        patch.object(dash, "ryzenadj_status", return_value=(data["oc_state"], data["oc_summary"], data["oc_metrics"])),
        patch.object(dash, "fan_status_lines", return_value=data["fans"]),
        patch.object(dash, "model_activity_lines", return_value=data["activity"]),
    ]
    return patches


async def run_test(screenshot_path=None):
    data = make_dummy_data()
    patches = patch_all(data)
    for p in patches:
        p.start()

    try:
        app = dash.OKODashboard()
        async with app.run_test(size=(140, 36)) as pilot:
            # Let it mount and refresh once
            await pilot.pause()
            await pilot.pause()

            if screenshot_path:
                await app.screenshot(screenshot_path)
                print(f"Screenshot saved to {screenshot_path}")
            else:
                # Print the rendered terminal content
                print("=" * 140)
                print("OKO Dashboard UI (dummy data)")
                print("=" * 140)
                print(app.screen.render())
                print("=" * 140)
    finally:
        for p in patches:
            p.stop()


if __name__ == "__main__":
    import asyncio

    screenshot = None
    if "--screenshot" in sys.argv:
        idx = sys.argv.index("--screenshot")
        if idx + 1 < len(sys.argv):
            screenshot = sys.argv[idx + 1]

    asyncio.run(run_test(screenshot))
