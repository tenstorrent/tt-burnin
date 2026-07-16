# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
from typing import List
import sys
import os
import fcntl
import struct
import json
import jsons
import time
import random
from concurrent.futures import ThreadPoolExecutor
from rich.table import Table

from rich import get_console
from pyluwen import PciChip
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_tools_common.reset_common.bh_reset import BHChipReset
from tt_tools_common.reset_common.wh_reset import WHChipReset
from tt_tools_common.reset_common.galaxy_reset import GalaxyReset
from tt_tools_common.utils_common.tools_utils import (
    detect_chips_with_callback,
)
from tt_tools_common.utils_common.system_utils import (
    get_driver_version,
    is_driver_version_at_least,
)
from pyluwen import (
    detect_chips_fallible,
    run_wh_ubb_ipmi_reset,
    run_ubb_wait_for_driver_load
)
from tt_burnin.chip import RemoteWhChip, WhChip


def pci_board_reset(list_of_boards: List[int], reinit=False):
    """Given a list of pci index's init the pci chip and call reset on it"""

    reset_wh_pci_idx = []
    reset_bh_pci_idx = []
    for pci_idx in list_of_boards:
        try:
            chip = PciChip(pci_interface=pci_idx)
        except Exception as e:
            print(
                CMD_LINE_COLOR.RED,
                f"Error accessing board at pci index {pci_idx}! Use -ls to see all devices available to reset",
                CMD_LINE_COLOR.ENDC,
            )
        if chip.as_wh():
            reset_wh_pci_idx.append(pci_idx)
        elif chip.as_bh():
            reset_bh_pci_idx.append(pci_idx)
        else:
            print(f"{CMD_LINE_COLOR.RED}Unknown chip!!{CMD_LINE_COLOR.ENDC}")
            sys.exit(1)

    # reset wh devices with pci indices
    if len(reset_wh_pci_idx) > 0:
        WHChipReset().full_lds_reset(pci_interfaces=reset_wh_pci_idx, silent=True)

    if len(reset_bh_pci_idx) > 0:
        BHChipReset().full_lds_reset(pci_interfaces=reset_bh_pci_idx, silent=True)

    if reinit:
        print(
            CMD_LINE_COLOR.PURPLE,
            f"Re-initializing boards after reset....",
            CMD_LINE_COLOR.ENDC,
        )
        try:
            chips = detect_chips_with_callback()
        except Exception as e:
            print(
                CMD_LINE_COLOR.RED,
                f"Error when re-initializing chips!\n {e}",
                CMD_LINE_COLOR.ENDC,
            )
            sys.exit(1)


def pci_indices_from_json(json_dict):
    """Parse pci_list from reset json"""
    pci_indices = []
    reinit = False
    if "wh_link_reset" in json_dict.keys():
        pci_indices.extend(json_dict["wh_link_reset"]["pci_index"])
    if "re_init_devices" in json_dict.keys():
        reinit = json_dict["re_init_devices"]
    return pci_indices, reinit


def mobo_reset_from_json(json_dict) -> dict:
    """Parse pci_list from reset json and init mobo reset"""
    if "wh_mobo_reset" in json_dict.keys():
        mobo_dict_list = []
        for mobo_dict in json_dict["wh_mobo_reset"]:
            # Only add the mobos that have a name
            if "MOBO NAME" not in mobo_dict["mobo"]:
                mobo_dict_list.append(mobo_dict)
        # If any mobos - do the reset
        if mobo_dict_list:
            GalaxyReset().warm_reset_mobo(mobo_dict_list)
            # If there are mobos to reset, remove link reset pci index's from the json
            try:
                wh_link_pci_indices = json_dict["wh_link_reset"]["pci_index"]
                for entry in mobo_dict_list:
                    if "nb_host_pci_idx" in entry.keys() and entry["nb_host_pci_idx"]:
                        # remove the list of WH pcie index's from the reset list
                        wh_link_pci_indices = list(
                            set(wh_link_pci_indices) - set(entry["nb_host_pci_idx"])
                        )
                json_dict["wh_link_reset"]["pci_index"] = wh_link_pci_indices
            except Exception as e:
                print(
                    CMD_LINE_COLOR.RED,
                    f"Error! {e}",
                    CMD_LINE_COLOR.ENDC,
                )

    return json_dict


def parse_reset_input(value):
    """Validate the reset inputs - either list of int pci IDs or a json config file"""
    if not value:
        return None
    try:
        # Attempt to parse as a JSON file
        with open(value, "r") as json_file:
            data = json.load(json_file)
            return data
    except json.JSONDecodeError as e:
        print(
            CMD_LINE_COLOR.RED,
            f"Please check the format of the json file.\n {e}",
            CMD_LINE_COLOR.ENDC,
        )
        sys.exit(1)
    except FileNotFoundError:
        # If no file found, attempt to parse as a list of comma separated integers
        print(
            CMD_LINE_COLOR.YELLOW,
            "File not found!\n To generate a reset json config file run tt-smi -g",
            CMD_LINE_COLOR.ENDC,
        )
        return None


def print_all_available_devices(devices):
    """Print all available boards on host"""
    console = get_console()
    table = Table()
    table.add_column("Pci Dev ID")
    table.add_column("Board Type")
    table.add_column("Device Series")
    table.add_column("Board Number")
    table.add_column("Coordinates")
    for i, device in enumerate(devices):
        chip = device.luwen_chip
        board_id = hex(device.board_id()).replace("0x", "")
        board_type = get_board_type(board_id)
        device_series = device.arch()
        pci_dev_id = device.interface_id if not device.is_remote else "N/A"
        coords = device.coord()
        if isinstance(chip, WhChip):
            suffix = " R" if device.is_remote else " L"
            board_type = board_type + suffix

        table.add_row(
            f"{pci_dev_id}",
            f"{device_series}",
            f"{board_type}",
            f"{board_id}",
            f"{coords}",
        )
    console.print(table)

def get_board_type(board_id: str) -> str:
    """
    Get board type from board ID string.
    Ex:
        Board ID: AA-BBBBB-C-D-EE-FF-XXX
                   ^     ^ ^ ^  ^  ^   ^
                   |     | | |  |  |   +- XXX
                   |     | | |  |  +----- FF
                   |     | | |  +-------- EE
                   |     | | +----------- D
                   |     | +------------- C = Revision
                   |     +--------------- BBBBB = Unique Part Identifier (UPI)
                   +--------------------- AA
    """
    if board_id == "N/A":
        return "N/A"
    serial_num = int(f"0x{board_id}", base=16)
    upi = (serial_num >> 36) & 0xFFFFF

    # Grayskull cards
    if upi == 0x3:
        return "e150"
    elif upi == 0xA:
        return "e300"
    elif upi == 0x7:
        return "e75"

    # Wormhole cards
    elif upi == 0x8:
        return "nb_cb"
    elif upi == 0xB:
        return "wh_4u"
    elif upi == 0x14:
        return "n300"
    elif upi == 0x18:
        return "n150"
    elif upi == 0x35:
        return "tt-galaxy-wh"

    # Blackhole cards
    elif upi == 0x36:
        return "bh-scrappy"
    elif upi == 0x43:
        return "p100a"
    elif upi == 0x40:
        return "p150a"
    elif upi == 0x41:
        return "p150b"
    elif upi == 0x42:
        return "p150c"
    elif upi == 0x44:
        return "p300b"
    elif upi == 0x45:
        return "p300a"
    elif upi == 0x46:
        return "p300c"
    elif upi == 0x47:
        return "tt-galaxy-bh"
    else:
        return "N/A"

def prefix_color_picker(current_value, max_value):
    if current_value < max_value * 0.85:
        return "[green]"
    else:
        return "[orange3]"

def asic_temperature_parser(temp, dev):
    """ASIC temperature is reported with different schema for BH vs other chips"""
    if dev.as_bh():
        # BH temp is reported as signed 16_16 integer that needs to be split into two 16 bit values
        return (temp >> 16) + (temp & 0xFFFF) / 65536.0
    else:
        return (temp & 0xFFFF) / 16

def timed_wait(seconds):
    """Wait for a specified number of seconds, printing the progress."""
    print("\033[93mWaiting for {} seconds: 0\033[0m".format(seconds), end='')
    sys.stdout.flush()

    for i in range(1, seconds + 1):
        time.sleep(1)
        # Move cursor back and overwrite the number
        print("\r\033[93mWaiting for {} seconds: {}\033[0m".format(seconds, i), end='')
        sys.stdout.flush()
    print()

# KMD reset ioctl (TENSTORRENT_IOCTL_RESET_DEVICE). The Galaxy IPMI tray reset on
# its own leaves the ASICs with ARC uninitialized (detection then fails with
# "ARC Status: 0 out of 1 initialized"); waiting longer never recovers them. The
# KMD reset handshake tt-smi performs is required: USER_RESET before the tray
# reset and POST_RESET once the chips reappear.
_TT_IOCTL_MAGIC = 0xFA
_TT_IOCTL_RESET_DEVICE = (_TT_IOCTL_MAGIC << 8) | 6
_RESET_FLAG_PCIE_LINK = 1
_RESET_FLAG_USER = 3
_RESET_FLAG_POST = 6


def _tt_interface_ids() -> List[int]:
    """Interface ids of the local Tenstorrent devices (i.e. /dev/tenstorrent/N)."""
    try:
        return sorted(int(e) for e in os.listdir("/dev/tenstorrent") if e.isdigit())
    except OSError:
        return []


def _reset_device_ioctl(interface_id: int, flags: int) -> bool:
    """Issue TENSTORRENT_IOCTL_RESET_DEVICE on one device, returning True on success."""
    # O_APPEND signals to KMD >= 2.6.0 that we are power-aware.
    fd = os.open(
        f"/dev/tenstorrent/{interface_id}", os.O_RDWR | os.O_CLOEXEC | os.O_APPEND
    )
    try:
        # struct: in {output_size_bytes, flags}, out {reserved, result}
        out_size = struct.calcsize("II")
        buf = bytearray(struct.pack("IIII", out_size, flags, 0, 0))
        fcntl.ioctl(fd, _TT_IOCTL_RESET_DEVICE, buf)
        _, result = struct.unpack("II", buf[struct.calcsize("II") :])
        return result == 0
    finally:
        os.close(fd)


def _reset_all_ioctl(device_ids: List[int], flags: int) -> List[int]:
    """Issue a reset ioctl on every device in parallel; return the ones that failed."""
    if not device_ids:
        return []
    failed: List[int] = []

    def one(interface_id: int):
        try:
            return interface_id, _reset_device_ioctl(interface_id, flags)
        except OSError:
            return interface_id, False

    with ThreadPoolExecutor(max_workers=len(device_ids)) as pool:
        for interface_id, ok in pool.map(one, device_ids):
            if not ok:
                failed.append(interface_id)
    return failed


def reset_6u_glx():
    """Reset Galaxy trays and detect chips post reset."""
    print(
        CMD_LINE_COLOR.PURPLE,
        f"Resetting Galaxy trays with reset command...",
        CMD_LINE_COLOR.ENDC,
    )
    # Quiesce the devices through the KMD before the tray reset: a secondary bus
    # reset (KMD >= 2.7.0) followed by USER_RESET.
    device_ids = _tt_interface_ids()
    driver = get_driver_version()
    if driver is not None and is_driver_version_at_least(driver, "2.7.0"):
        _reset_all_ioctl(device_ids, _RESET_FLAG_PCIE_LINK)
    _reset_all_ioctl(device_ids, _RESET_FLAG_USER)

    run_wh_ubb_ipmi_reset(ubb_num="0xF", dev_num="0xFF", op_mode="0x0", reset_time="0xF")
    timed_wait(30)
    run_ubb_wait_for_driver_load()

    # Re-establish the devices after they reappear on the bus. Without POST_RESET
    # the ASICs stay with ARC uninitialized and detection fails.
    post_failed = _reset_all_ioctl(_tt_interface_ids(), _RESET_FLAG_POST)
    if post_failed:
        print(
            CMD_LINE_COLOR.RED,
            f"POST_RESET failed for devices: {post_failed}",
            CMD_LINE_COLOR.ENDC,
        )

    print(
        CMD_LINE_COLOR.PURPLE,
        f"Re-initializing boards after reset....",
        CMD_LINE_COLOR.ENDC,
    )
    try:
        devs = detect_chips_fallible(
            local_only=True,
            continue_on_failure=False,
            callback=None,
            noc_safe=True,
        )
        print(
            CMD_LINE_COLOR.GREEN,
            f"Re-initialized {len(devs)} chips after reset.",
            CMD_LINE_COLOR.ENDC,
        )
    except Exception as e:
        print(
            CMD_LINE_COLOR.RED,
            f"Error when re-initializing chips!\n {e}",
            CMD_LINE_COLOR.ENDC,
        )
        # Error out if chips don't initalize
    return

def generate_table(devices) -> Table:
    """Make a table to display telemetry values."""
    table = Table(
        title=" ",
    )
    table.add_column("ID")
    table.add_column("Core Voltage (V)")
    table.add_column("Core Current (A)")
    table.add_column("AICLK (MHz)")
    table.add_column("Power (W)")
    table.add_column("Core Temp (°C)")

    for i, dev in enumerate(devices):
        telem = jsons.dump(dev.get_telemetry())
        current = int(hex(telem["tdc"]), 16) & 0xFFFF
        voltage = int(hex(telem["vcore"]), 16) / 1000
        aiclk = int(hex(telem["aiclk"]), 16) & 0xFFFF
        power = int(hex(telem["tdp"]), 16) & 0xFFFF
        asic_temperature = asic_temperature_parser(int(hex(telem["asic_temperature"]), 16), dev)
        vdd_max = int(hex(telem["vdd_limits"]), 16) >> 16
        if dev.as_bh():
            curr_limit = int(hex(telem["tdc_limit_max"]), 16)
            aiclk_limit = int(hex(telem["aiclk_limit_max"]), 16)
            pwr_limit = int(hex(telem["tdp_limit_max"]), 16)
        else:
            curr_limit = int(hex(telem["tdc"]), 16) >> 16
            aiclk_limit = int(hex(telem["aiclk"]), 16) >> 16
            pwr_limit = int(hex(telem["tdp"]), 16) >> 16
        thm_limit = int(hex(telem["thm_limits"]), 16) & 0xFFFF
        table.add_row(
            f"{i}",
            f"{voltage:4.2f}[light_goldenrod1] / {vdd_max/1000:4.2f}",
            f"{prefix_color_picker(current, curr_limit)}{current:5.1f}[light_goldenrod1] / {curr_limit:5.1f}",
            f"{prefix_color_picker(aiclk, aiclk_limit)}{aiclk:4.0f}[light_goldenrod1] / {aiclk_limit:4.0f}",
            f"{prefix_color_picker(power, pwr_limit)}{power:5.1f}[light_goldenrod1] / {pwr_limit:5.1f}",
            f"{prefix_color_picker(asic_temperature, thm_limit)}{asic_temperature:4.1f}[light_goldenrod1] / {thm_limit:4.1f}",
        )

    return table
