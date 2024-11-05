# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
from typing import List
import sys
import os
import json
import jsons
import time
import random
from rich.table import Table

from rich import get_console
from pyluwen import PciChip
from tt_tools_common.ui_common.themes import CMD_LINE_COLOR
from tt_tools_common.reset_common.bh_reset import BHChipReset
from tt_tools_common.reset_common.wh_reset import WHChipReset
from tt_tools_common.reset_common.gs_tensix_reset import GSTensixReset
from tt_tools_common.reset_common.galaxy_reset import GalaxyReset
from tt_tools_common.utils_common.tools_utils import (
    detect_chips_with_callback,
    get_board_type,
)

from tt_burnin.chip import RemoteWhChip, WhChip


def pci_board_reset(list_of_boards: List[int], reinit=False):
    """Given a list of pci index's init the pci chip and call reset on it"""

    reset_wh_pci_idx = []
    reset_gs_devs = []
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
        elif chip.as_gs():
            reset_gs_devs.append(chip)
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

    # reset gs devices by creating a partially init backend
    if len(reset_gs_devs) > 0:
        for i, device in enumerate(reset_gs_devs):
            GSTensixReset(device).tensix_reset(silent=True)

    if reinit:
        # Enable backtrace for debugging
        os.environ["RUST_BACKTRACE"] = "full"

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
    if "gs_tensix_reset" in json_dict.keys():
        pci_indices.extend(json_dict["gs_tensix_reset"]["pci_index"])
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


def prefix_color_picker(current_value, max_value):
    if current_value < max_value * 0.85:
        return "[green]"
    else:
        return "[orange3]"


def generate_table(devices) -> Table:
    """Make a new table."""
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
        asic_temperature = (int(hex(telem["asic_temperature"]), 16) & 0xFFFF) / 16
        vdd_max = int(hex(telem["vdd_limits"]), 16) >> 16
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
