# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from abc import abstractmethod
import time
from typing import Union
import itertools
import sys

from pyluwen import PciChip, Telemetry
from pyluwen import detect_chips as luwen_detect_chips
from pyluwen import detect_chips_fallible as luwen_detect_chips_fallible


class TTChip:
    def __init__(self, chip: PciChip):
        self.luwen_chip = chip
        self.interface_id = chip.pci_interface_id()

        self._harvesting_bits = None

        self.telmetry_cache = None

        self.is_remote = False

    @abstractmethod
    def arch(self) -> str: ...

    def reinit(self, callback=None):
        self.luwen_chip = PciChip(self.interface_id)
        self.telmetry_cache = None

        chip_count = 0
        block_count = 0
        last_draw = time.time()

        def chip_detect_callback(status):
            nonlocal chip_count, last_draw, block_count

            if status.new_chip():
                chip_count += 1
            elif status.correct_down():
                chip_count -= 1
            chip_count = max(chip_count, 0)

            if sys.stdout.isatty():
                current_time = time.time()
                if current_time - last_draw > 0.1:
                    last_draw = current_time

                    if block_count > 0:
                        print(f"\033[{block_count}A", end="", flush=True)
                        print("\033[J", end="", flush=True)

                    print(f"\rDetected Chips: {chip_count}\n", end="", flush=True)
                    block_count = 1

                    status_string = status.status_string()
                    if status_string is not None:
                        for line in status_string.splitlines():
                            block_count += 1
                            print(f"\r{line}", flush=True)
            else:
                time.sleep(0.01)

        self.luwen_chip.init(
            callback=chip_detect_callback if callback is None else callback
        )

    def get_telemetry(self) -> Telemetry:
        self.telmetry_cache = self.luwen_chip.get_telemetry()
        return self.telmetry_cache

    def get_telemetry_unchanged(self) -> Telemetry:
        if self.telmetry_cache is None:
            self.telmetry_cache = self.luwen_chip.get_telemetry()

        return self.telmetry_cache

    def get_harvest_bits(self) -> int:
        if self._harvesting_bits is None:
            # Magic value to get harvesting info
            (bad_row_bits, _) = self.arc_msg(0x57)
            self._harvesting_bits = bad_row_bits
        return self._harvesting_bits

    def __vnum_to_version(self, version: int) -> tuple[int, int, int, int]:
        return (
            (version >> 24) & 0xFF,
            (version >> 16) & 0xFF,
            (version >> 8) & 0xFF,
            version & 0xFF,
        )

    def m3_fw_app_version(self):
        telem = self.get_telemetry_unchanged()
        return self.__vnum_to_version(telem.smbus_tx_m3_app_fw_version)

    def smbus_fw_version(self):
        telem = self.get_telemetry_unchanged()
        return self.__vnum_to_version(telem.smbus_tx_arc1_fw_version)

    def arc_l2_fw_version(self):
        telem = self.get_telemetry_unchanged()
        return self.__vnum_to_version(telem.smbus_tx_arc0_fw_version)

    def board_type(self):
        return self.luwen_chip.pci_board_type()

    def board_id(self):
        telem = self.get_telemetry_unchanged()
        return telem.board_id

    def noc_read(self, noc: int, x: int, y: int, addr: int, data: bytes):
        self.luwen_chip.noc_read(noc, x, y, addr, data)

    def noc_read32(self, noc: int, x: int, y: int, addr: int):
        return self.luwen_chip.noc_read32(noc, x, y, addr)

    def noc_write(self, noc: int, x: int, y: int, addr: int, data: bytes):
        self.luwen_chip.noc_write(noc, x, y, addr, data)

    def noc_write32(self, noc: int, x: int, y: int, addr: int, data: int):
        self.luwen_chip.noc_write32(noc, x, y, addr, data)

    def noc_broadcast(self, noc: int, addr: int, data: bytes):
        self.luwen_chip.noc_broadcast(noc, addr, data)

    def noc_broadcast32(self, noc: int, addr: int, data: int):
        self.luwen_chip.noc_broadcast32(noc, addr, data)

    def axi_write32(self, addr: int, value: int):
        self.luwen_chip.axi_write32(addr, value)

    def axi_write(self, addr: int, data: bytes):
        self.luwen_chip.axi_write(addr, data)

    def axi_read32(self, addr: int) -> int:
        return self.luwen_chip.axi_read32(addr)

    def axi_read(self, addr: int, size: int) -> bytes:
        data = bytearray(size)
        self.luwen_chip.axi_read(addr, data)

        return bytes(data)

    def spi_write(self, addr: int, data: bytes):
        self.luwen_chip.spi_write(addr, data)

    def spi_read(self, addr: int, size: int) -> bytes:
        data = bytearray(size)
        self.luwen_chip.spi_read(addr, data)

        return bytes(data)

    def arc_msg(self, *args, **kwargs):
        return self.luwen_chip.arc_msg(*args, **kwargs)

    # Given non-negative integer x, return an iterable containing the bits set in x, in increasing order.
    def _int_to_bits(self, x):
        return list(filter(lambda b: x & (1 << b), range(x.bit_length())))


def reverse_mapping_list(l):
    ret = [0] * len(l)
    for idx, val in enumerate(l):
        ret[val] = idx
    return ret


class BhChip(TTChip):
    def noc_coord_flip(self, coord: tuple[int, int]) -> tuple[int, int]:
        return (self.GRID_SIZE_X - coord[0] - 1, self.GRID_SIZE_Y - coord[1] - 1)

    # Physical rows & columns are defined in Blackhole - NOC Co-ordinates
    def phys_to_noc(self, coord: tuple[int, int], noc_id: int) -> tuple[int, int]:
        noc0 = (self.PHYS_X_TO_NOC_0_X[coord[0]], self.PHYS_Y_TO_NOC_0_y[coord[1]])
        return noc0 if noc_id == 0 else self.noc_coord_flip(noc0)

    def __init__(self, *args, **kwargs):
        self.GRID_SIZE_X = 17
        self.GRID_SIZE_Y = 12

        self.NUM_TENSIX_X = 14
        self.NUM_TENSIX_Y = 10

        self.TENSIX_LOCATIONS = []
        for y in range(2, 12):
            for x in range(1, 8):
                self.TENSIX_LOCATIONS.append((x, y))
            for x in range(10, 17):
                self.TENSIX_LOCATIONS.append((x, y))

        super().__init__(*args, **kwargs)

    @property
    def noc_translation_enabled(self) -> bool:
        telemetry = self.get_telemetry_unchanged()
        return telemetry.noc_translation_enabled

    @property
    def enabled_tensix_columns(self) -> int:
        telemetry = self.get_telemetry_unchanged()
        return telemetry.tensix_enabled_col

    def get_tensix_locations(self):
        if self.noc_translation_enabled:
            # When translated NOC 0 looks roughly the same as without translation
            # with the minor difference that the harvested tensix are moved to the
            # end (right side) of the grid
            NUM_TENSIX_X = 0
            enabled_tensix_columns_bitmask = self.enabled_tensix_columns
            while enabled_tensix_columns_bitmask != 0:
                if enabled_tensix_columns_bitmask & 0x1 != 0:
                    NUM_TENSIX_X += 1
                enabled_tensix_columns_bitmask >>= 1

            good_cores = []
            for core in self.TENSIX_LOCATIONS:
                if (core[0] <= 7 and core[0] < NUM_TENSIX_X) or (core[0] >= 10 and (core[0] - 2) < NUM_TENSIX_X):
                    good_cores.append(core)
        else:
            enabled_tensix_columns_bitmask = self.enabled_tensix_columns
            enabled_tensix_columns = []
            TENSIX_COLS = [1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 15, 16]
            for col in range(self.NUM_TENSIX_X):
                if enabled_tensix_columns_bitmask & 0x1 == 1:
                    enabled_tensix_columns.append(TENSIX_COLS[col])
                enabled_tensix_columns_bitmask >>= 1

            enabled_tensix_columns = set(enabled_tensix_columns)

            good_cores = []
            for core in self.TENSIX_LOCATIONS:
                if core[0] in enabled_tensix_columns:
                    good_cores.append(core)

        return set(good_cores)

    def coord(self):
        coord = self.luwen_chip.get_local_coord()
        return (coord.shelf_x, coord.shelf_y, coord.rack_x, coord.rack_y)

    def arch(self):
        return "Blackhole"

    def __repr__(self):
        return f"Blackhole[{self.interface_id}]"


class WhChip(TTChip):
    def __init__(self, *args, **kwargs):
        self.GRID_SIZE_X = 10
        self.GRID_SIZE_Y = 12
        self.NUM_TENSIX_X = self.GRID_SIZE_X - 2
        self.NUM_TENSIX_Y = self.GRID_SIZE_Y - 2

        self.PHYS_X_TO_NOC_0_X = [0, 9, 1, 8, 2, 7, 3, 6, 4, 5]
        self.PHYS_Y_TO_NOC_0_Y = [0, 11, 1, 10, 2, 9, 3, 8, 4, 7, 5, 6]
        self.PHYS_X_TO_NOC_1_X = [9, 0, 8, 1, 7, 2, 6, 3, 5, 4]
        self.PHYS_Y_TO_NOC_1_Y = [11, 0, 10, 1, 9, 2, 8, 3, 7, 4, 6, 5]
        self.NOC_0_X_TO_PHYS_X = reverse_mapping_list(self.PHYS_X_TO_NOC_0_X)
        self.NOC_0_Y_TO_PHYS_Y = reverse_mapping_list(self.PHYS_Y_TO_NOC_0_Y)
        self.NOC_1_X_TO_PHYS_X = reverse_mapping_list(self.PHYS_X_TO_NOC_1_X)
        self.NOC_1_Y_TO_PHYS_Y = reverse_mapping_list(self.PHYS_Y_TO_NOC_1_Y)

        super().__init__(*args, **kwargs)

    def get_tensix_locations(self):
        all_tensix_rows = [1, 2, 3, 4, 5, 7, 8, 9, 10, 11]
        all_tensix_cols = [1, 2, 3, 4, 6, 7, 8, 9]

        # Magic value to get harvesting info
        bad_row_bits = self.get_harvest_bits()

        bad_row_bits = bad_row_bits << 1

        bad_physical_rows = self._int_to_bits(bad_row_bits)

        disabled_rows = frozenset(
            map(lambda y: self.PHYS_Y_TO_NOC_0_Y[y], bad_physical_rows)
        )

        good_rows = filter(lambda y: y not in disabled_rows, all_tensix_rows)
        good_cores = itertools.product(all_tensix_cols, good_rows)

        return set(good_cores)

    def arch(self):
        return "Wormhole"

    def __repr__(self):
        return f"Wormhole[{self.interface_id}]"

    def coord(self):
        coord = self.luwen_chip.get_local_coord()
        return (coord.shelf_x, coord.shelf_y, coord.rack_x, coord.rack_y)


class RemoteWhChip(WhChip):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.is_remote = True

    def noc_broadcast(self, noc: int, addr: int, data: bytes):
        for core in self.get_tensix_locations():
            self.luwen_chip.noc_write(noc, *core, addr, data)

    def noc_broadcast32(self, noc: int, addr: int, data: int):
        for core in self.get_tensix_locations():
            self.luwen_chip.noc_write32(noc, *core, addr, data)


def detect_local_chips(ignore_ethernet: bool = False) -> list[Union[WhChip, BhChip]]:
    """
    This will create a chip which only gaurentees that you have communication with the chip.
    """

    chip_count = 0
    block_count = 0
    last_draw = time.time()

    def chip_detect_callback(status):
        nonlocal chip_count, last_draw, block_count

        if status.new_chip():
            chip_count += 1
        elif status.correct_down():
            chip_count -= 1
        chip_count = max(chip_count, 0)

        if sys.stdout.isatty():
            current_time = time.time()
            if current_time - last_draw > 0.1:
                last_draw = current_time

                if block_count > 0:
                    print(f"\033[{block_count}A", end="", flush=True)
                    print(f"\033[J", end="", flush=True)

                print(f"\rDetected Chips: {chip_count}\n", end="", flush=True)
                block_count = 1

                status_string = status.status_string()
                if status_string is not None:
                    for line in status_string.splitlines():
                        block_count += 1
                        print(f"\r{line}", flush=True)
        else:
            time.sleep(0.01)

    output = []
    for device in luwen_detect_chips_fallible(
        local_only=True,
        continue_on_failure=False,
        callback=chip_detect_callback,
        noc_safe=ignore_ethernet,
    ):
        if not device.have_comms():
            raise Exception(
                f"Do not have communication with {device}, you should reset or remove this device from your system before continuing."
            )

        device = device.force_upgrade()

        if device.as_wh() is not None:
            output.append(WhChip(device.as_wh()))
        elif device.as_bh() is not None:
            output.append(BhChip(device.as_bh()))
        else:
            raise ValueError("Did not recognize board")

    return output


def detect_chips(local_only: bool = False) -> list[Union[WhChip, BhChip]]:
    output = []
    for device in luwen_detect_chips(local_only=local_only):
        if device.as_wh() is not None:
            if device.is_remote():
                output.append(RemoteWhChip(device.as_wh()))
            else:
                output.append(WhChip(device.as_wh()))
        elif device.as_bh() is not None:
            output.append(BhChip(device.as_bh()))
        else:
            raise ValueError("Did not recognize board")

    return output
