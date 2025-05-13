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
import tt_burnin
from rich.live import Live
from rich.text import Text
from rich.console import Group
from importlib.resources import path
from tt_burnin.chip import GsChip, WhChip, RemoteWhChip, BhChip
from tt_burnin.load_ttx import load_ttx_file, TtxFile, CoreId
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_burnin.utils import (
    parse_reset_input,
    mobo_reset_from_json,
    pci_indices_from_json,
    pci_board_reset,
    print_all_available_devices,
    generate_table,
)
from tt_tools_common.utils_common.tools_utils import (
    get_board_type,
    detect_chips_with_callback,
)


def reset_all_devices(devices, reset_filename=None):
    """Reset all devices"""
    print(CMD_LINE_COLOR.BLUE, "Resetting devices on host...", CMD_LINE_COLOR.ENDC)
    LOG_FOLDER = os.path.expanduser("~/.config/tenstorrent")
    log_filename = f"{LOG_FOLDER}/reset_config.json"
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


def start_burnin_gs(
    device,
    keep_trisc_under_reset: bool = False,
    stagger_start: bool = False,
    no_check: bool = False,
):
    BRISC_SOFT_RESET = 1 << 11
    TRISC_SOFT_RESETS = (1 << 12) | (1 << 13) | (1 << 14)
    NCRISC_SOFT_RESET = 1 << 18
    STAGGERED_START_ENABLE = (1 << 31) if stagger_start else 0

    device.noc_broadcast32(
        0, 0xFFB121B0, BRISC_SOFT_RESET | TRISC_SOFT_RESETS | NCRISC_SOFT_RESET
    )

    # Deassert riscv reset
    device.arc_msg(0xBA)

    # Go busy
    device.arc_msg(0x52)

    with path("tt_burnin", "") as data_path:
        load_ttx_file(
            device,
            TtxFile(str(data_path.joinpath("ttx/gspv.ttx"))),
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


def stop_burnin_gs(device):
    BRISC_SOFT_RESET = 1 << 11
    TRISC_SOFT_RESETS = (1 << 12) | (1 << 13) | (1 << 14)
    NCRISC_SOFT_RESET = 1 << 18

    # Go idle
    device.arc_msg(0x54)

    # Put tensix back under soft reset
    device.noc_broadcast32(
        0, 0xFFB121B0, BRISC_SOFT_RESET | TRISC_SOFT_RESETS | NCRISC_SOFT_RESET
    )


def start_burnin_wh(
    device,
    keep_trisc_under_reset: bool = False,
    stagger_start: bool = False,
    no_check: bool = False,
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
):
    BRISC_SOFT_RESET = 1 << 11
    TRISC_SOFT_RESETS = (1 << 12) | (1 << 13) | (1 << 14)
    NCRISC_SOFT_RESET = 1 << 18
    STAGGERED_START_ENABLE = (1 << 31) if stagger_start else 0

    # Put tensix under soft reset
    device.noc_broadcast32(
        0, 0xFFB121B0, BRISC_SOFT_RESET | TRISC_SOFT_RESETS | NCRISC_SOFT_RESET
    )

    # Go busy
    device.arc_msg(0x52)

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

    # Go idle
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
    # subparsers = parser.add_subparsers(title="command", dest="command", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    os.environ["RUST_BACKTRACE"] = "full"
    # Allow non blocking read for accepting user input before stopping burnin
    os.set_blocking(sys.stdin.fileno(), False)
    all_devices = detect_chips_with_callback()
    devs = []
    devices = []
    for device in all_devices:
        if device.as_gs() is not None:
            devs.append(GsChip(device.as_gs()))
        elif device.as_wh() is not None:
            if device.is_remote():
                devs.append(RemoteWhChip(device.as_wh()))
            else:
                devs.append(WhChip(device.as_wh()))
        elif device.as_bh() is not None:
            devs.append(BhChip(device.as_bh()))
        else:
            raise ValueError("Did not recognize board")
        devices.append(device)
    print_all_available_devices(devs)
    if not args.no_reset:
        reset_all_devices(devices, reset_filename=args.reset)

    try:
        print()
        print(
            CMD_LINE_COLOR.BLUE,
            "Starting TT-Burnin workload on all boards. WARNING: Opening SMI might cause unexpected behavior",
            CMD_LINE_COLOR.ENDC,
        )
        for device in devs:
            print(f"\tStarting on {device}")
            if isinstance(device, GsChip):
                start_burnin_gs(device, no_check=args.no_check)
            elif isinstance(device, WhChip):
                start_burnin_wh(device, no_check=args.no_check)
            elif isinstance(device, BhChip):
                start_burnin_bh(device, no_check=args.no_check)
            else:
                raise NotImplementedError(f"Don't support {device}")

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
        for device in devs:
            if isinstance(device, GsChip):
                stop_burnin_gs(device)
            elif isinstance(device, WhChip):
                stop_burnin_wh(device)
            elif isinstance(device, BhChip):
                stop_burnin_bh(device)
            else:
                raise NotImplementedError(f"Don't support {device}")

        # Final reset to restore state
        if not args.no_reset:
            reset_all_devices(devices, reset_filename=args.reset)
