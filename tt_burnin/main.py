# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
"""
Tenstorrent Burnin (TT-Burnin) is a command line utility
to run a high power consumption workload on TT devices.
"""
from __future__ import annotations

import os
import sys
import time
import argparse
import threading
import tt_burnin
from rich.live import Live
from rich.text import Text
from rich.console import Group
from importlib.resources import path
from tt_burnin.chip import WhChip, RemoteWhChip, BhChip
from tt_burnin.load_ttx import load_ttx_file, TtxFile, CoreId
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_burnin.utils import (
    parse_reset_input,
    mobo_reset_from_json,
    pci_indices_from_json,
    pci_board_reset,
    print_all_available_devices,
    generate_table,
    get_board_type,
    reset_6u_glx,

)
from tt_tools_common.utils_common.system_utils import (
    get_driver_version,
    is_driver_version_at_least,
)
from tt_tools_common.utils_common.tools_utils import (
    detect_chips_with_callback,
)


def reset_all_devices(devices, reset_filename=None):
    """Reset all devices"""
    print(CMD_LINE_COLOR.BLUE, "Resetting devices on host...", CMD_LINE_COLOR.ENDC)
    LOG_FOLDER = os.path.expanduser("~/.config/tenstorrent")
    log_filename = f"{LOG_FOLDER}/reset_config.json"
    if not devices:
        print(
            CMD_LINE_COLOR.RED,
            "No devices detected. Exiting...",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)
    # Check board type and reset accordingly
    board_id = hex(devices[0].board_id()).replace("0x", "")
    board_type = get_board_type(board_id)
    if board_type == "tt-galaxy-wh" or board_type == "tt-galaxy-bh":
        # Perform a full galaxy reset and detect chips post reset
        reset_6u_glx()
        return

    # If input is just reset board
    if not reset_filename:
        log_filename = reset_filename
    data = parse_reset_input(log_filename)
    if data:
        # reset using the json file
        parsed_dict = mobo_reset_from_json(data)
        pci_indices, reinit = pci_indices_from_json(parsed_dict)
        if pci_indices:
            pci_board_reset(pci_indices, reinit)
    else:
        # reset all boards
        dev_ids = []
        for device in devices:
            if not device.is_remote():
                dev_ids.append(device.get_pci_interface_id())
        pci_board_reset(dev_ids, reinit=True)


def start_burnin_wh(
    device,
    keep_trisc_under_reset: bool = False,
    stagger_start: bool = False,
    no_check: bool = False,
    idle: bool = False
):
    BRISC_SOFT_RESET = 1 << 11
    TRISC_SOFT_RESETS = (1 << 12) | (1 << 13) | (1 << 14)
    NCRISC_SOFT_RESET = 1 << 18
    STAGGERED_START_ENABLE = (1 << 31) if stagger_start else 0

    # Put tensix under soft reset
    device.noc_broadcast32(
        0, 0xFFB121B0, BRISC_SOFT_RESET | TRISC_SOFT_RESETS | NCRISC_SOFT_RESET
    )

    # Deassert riscv reset
    device.arc_msg(0xBA)

    # Go busy
    device.arc_msg(0x52)

    if not idle:
        with path("tt_burnin", "") as data_path:
            load_ttx_file(
                device,
                TtxFile(str(data_path.joinpath("ttx/whpv.ttx"))),
                {CoreId(0, 0): device.get_tensix_locations()},
                no_check,
            )

    if keep_trisc_under_reset:
        soft_reset_value = (
            NCRISC_SOFT_RESET | TRISC_SOFT_RESETS | STAGGERED_START_ENABLE
        )
    else:
        soft_reset_value = NCRISC_SOFT_RESET | STAGGERED_START_ENABLE

    # Take cores out of reset
    device.noc_broadcast32(0, 0xFFB121B0, soft_reset_value)


def stop_burnin_wh(device):
    BRISC_SOFT_RESET = 1 << 11
    TRISC_SOFT_RESETS = (1 << 12) | (1 << 13) | (1 << 14)
    NCRISC_SOFT_RESET = 1 << 18

    # Go idle
    device.arc_msg(0x54)

    # Put tensix back under soft reset
    device.noc_broadcast32(
        0, 0xFFB121B0, BRISC_SOFT_RESET | TRISC_SOFT_RESETS | NCRISC_SOFT_RESET
    )


def start_burnin_bh(
    device,
    keep_trisc_under_reset: bool = False,
    stagger_start: bool = False,
    no_check: bool = False,
    idle: bool = False
):
    BRISC_SOFT_RESET = 1 << 11
    TRISC_SOFT_RESETS = (1 << 12) | (1 << 13) | (1 << 14)
    NCRISC_SOFT_RESET = 1 << 18
    STAGGERED_START_ENABLE = (1 << 31) if stagger_start else 0

    # Put tensix under soft reset
    device.noc_broadcast32(
        0, 0xFFB121B0, BRISC_SOFT_RESET | TRISC_SOFT_RESETS | NCRISC_SOFT_RESET
    )

    # We only send GO_BUSY/GO_IDLE on BH if kmd < 2.6.0
    driver = get_driver_version()
    if not is_driver_version_at_least(driver, "2.6.0"):
        # GO_BUSY message (power management interface prior to KMD v2.6.0, FW v18.12.0)
        device.arc_msg(0x52)

    if not idle:
        with path("tt_burnin", "") as data_path:
            load_ttx_file(
                device,
                TtxFile(str(data_path.joinpath("ttx/bhpv.ttx"))),
                {CoreId(0, 0): device.get_tensix_locations()},
                no_check
            )

    if keep_trisc_under_reset:
        soft_reset_value = (
            NCRISC_SOFT_RESET | TRISC_SOFT_RESETS | STAGGERED_START_ENABLE
        )
    else:
        soft_reset_value = NCRISC_SOFT_RESET | STAGGERED_START_ENABLE

    # Take cores out of reset
    device.noc_broadcast32(0, 0xFFB121B0, soft_reset_value)


def stop_burnin_bh(device):
    BRISC_SOFT_RESET = 1 << 11
    TRISC_SOFT_RESETS = (1 << 12) | (1 << 13) | (1 << 14)
    NCRISC_SOFT_RESET = 1 << 18

    # We only send GO_BUSY/GO_IDLE on BH if kmd < 2.6.0
    driver = get_driver_version()
    if not is_driver_version_at_least(driver, "2.6.0"):
        device.arc_msg(0x54)

    # Put tensix back under soft reset
    device.noc_broadcast32(
        0, 0xFFB121B0, BRISC_SOFT_RESET | TRISC_SOFT_RESETS | NCRISC_SOFT_RESET
    )


def parse_args():
    # Parse arguments
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=tt_burnin.__version__,
    )
    parser.add_argument(
        "--reset_file",
        type=parse_reset_input,
        metavar="reset_config.json",
        default=None,
        help=(
            "Provide a custom reset json file for the host."
            "Generate a default reset json file with the -g option with tt-smi."
        ),
        dest="reset",
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        default=False,
        help="Don't issue a reset before or after burning (WARNING: This may cause burnin or your next workload to no longer function)",
    )
    parser.add_argument(
        "--no-check",
        action="store_true",
        default=False,
        help="Don't check tensix fw after loading (WARNING: if the workload was loaded incorrectly burnin may not run at maximum load)",
    )
    parser.add_argument(
        "--idle",
        action="store_true",
        default=False,
        help="Don't load the power virus workload, just run the tensix idle",
    )
    # subparsers = parser.add_subparsers(title="command", dest="command", required=True)
    return parser.parse_args()

def detect_and_group_devices():
    all_devices = detect_chips_with_callback()
    devs = []
    devices = []
    for device in all_devices:
        if device.as_wh() is not None:
            if device.is_remote():
                devs.append(RemoteWhChip(device.as_wh()))
            else:
                devs.append(WhChip(device.as_wh()))
        elif device.as_bh() is not None:
            devs.append(BhChip(device.as_bh()))
        else:
            raise ValueError("Did not recognize board")
        devices.append(device)

    driver = get_driver_version()
    if is_driver_version_at_least(driver, "2.6.0"):
        # Raise power state to high (BH)
        try:
            device.set_power_state("high")
        except:
            print(
                CMD_LINE_COLOR.RED,
                "Failed to set power state. Your firmware version might be too old.",
                "Please update firmware to v18.12.0 or newer.",
                "Or, if you know it's already up-to-date, please try power cycling.",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)

    return devs, devices

def garbage_collect_all_devices(all_devices):
    for device in all_devices:
        del device

def main():
    args = parse_args()
    os.environ["RUST_BACKTRACE"] = "full"
    # Allow non blocking read for accepting user input before stopping burnin
    os.set_blocking(sys.stdin.fileno(), False)
    devs, devices = detect_and_group_devices()
    print_all_available_devices(devs)
    if not args.no_reset:
        reset_all_devices(devices, reset_filename=args.reset)

    # Force garbage collection on the old devices and start with new device objects after reset
    garbage_collect_all_devices(devices)
    devs, devices = detect_and_group_devices()
    try:
        print()
        print(
            CMD_LINE_COLOR.BLUE,
            "Starting TT-Burnin workload on all boards. WARNING: Opening SMI might cause unexpected behavior",
            CMD_LINE_COLOR.ENDC,
        )
        print()
        def start_burnin(device, idx, total):
                print(
                    CMD_LINE_COLOR.PURPLE,
                    f"Starting TT-Burnin workload on device {idx + 1}/{total}",
                    CMD_LINE_COLOR.ENDC,
                )
                if isinstance(device, WhChip):
                    start_burnin_wh(device)
                elif isinstance(device, BhChip):
                    start_burnin_bh(device)
                else:
                    raise NotImplementedError(f"Don't support {device}")

        # Thread the start of burnin for faster speed
        threads = []
        for i, device in enumerate(devs):
            t = threading.Thread(target=start_burnin, args=(device, i, len(devs)))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        text = Text(
            " Press Enter to STOP TT-Burnin on all boards...", style="bold yellow"
        )

        # Create a live update for telemetry widget
        with Live(Group(generate_table(devices), text), refresh_per_second=10) as live:
            while True:
                # Break if there is any user keypress
                c = sys.stdin.read(1)
                if len(c) > 0:
                    break
                live.update(Group(generate_table(devices), text))
                time.sleep(0.1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(e)
    finally:
        print()
        print(
            CMD_LINE_COLOR.GREEN,
            "Stopping TT-Burnin workload on all boards.",
            CMD_LINE_COLOR.ENDC,
        )
        print()
        # Thread the stop of burnin for faster speed
        def stop_burnin(device):
            if isinstance(device, WhChip):
                stop_burnin_wh(device)
            elif isinstance(device, BhChip):
                stop_burnin_bh(device)
            else:
                raise NotImplementedError(f"Don't support {device}")

        stop_threads = []
        for device in devs:
            t = threading.Thread(target=stop_burnin, args=(device,))
            t.start()
            stop_threads.append(t)
        for t in stop_threads:
            t.join()

        # Final reset to restore state
        if not args.no_reset:
            reset_all_devices(devices, reset_filename=args.reset)

        # Force garbage collection on the old devices and start with new device objects after reset
        garbage_collect_all_devices(devices)
