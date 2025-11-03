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

from tt_umd import (
    TTDevice,
    TelemetryTag,
    ARCH,
    wormhole,
    SmBusArcTelemetryReader,
    SocDescriptor,
    CoreType,
)

class TTChip:
    def __init__(self, chip: Union[PciChip, TTDevice]):
        self.use_umd = isinstance(chip, TTDevice)
        if self.use_umd:
            self.umd_device = chip
            self.interface_id = chip.get_pci_device().get_device_num()
            self.soc_desc = SocDescriptor(self.umd_device)
            # For UMD: ethernet coordinates (set externally)
            self.eth_coord = None
        else:
            self.luwen_chip = chip
            self.interface_id = chip.pci_interface_id()

        self._harvesting_bits = None

        self.telmetry_cache = None

        self.is_remote = False

    @abstractmethod
    def arch(self) -> str: ...

    def reinit(self, callback=None):
        if self.use_umd:
            self.umd_device = TTDevice.create(self.interface_id)
            self.umd_device.init_tt_device()
            self.telmetry_cache = None
            self.soc_desc = SocDescriptor(self.umd_device)
        else:
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

    def get_telemetry(self):
        if self.use_umd:
            # For UMD, return a telemetry-like object with the required fields
            arch = self.umd_device.get_arch()
            
            # Create the appropriate telemetry reader based on architecture
            if arch == ARCH.WORMHOLE_B0:
                telem_reader = SmBusArcTelemetryReader(self.umd_device)
            else:
                telem_reader = self.umd_device.get_arc_telemetry_reader()
            
            # Create a simple telemetry object with the fields we need
            class UMDTelemetry:
                def __init__(self, reader, arch, device):
                    self.reader = reader
                    self.arch = arch
                    self.device = device
                    # Initialize telemetry fields
                    self.m3_app_fw_version = None
                    self.arc1_fw_version = None
                    self.arc0_fw_version = None
                    self.asic_location = None
                    self.fw_bundle_version = None
                    self.board_id = None
                    # Common telemetry fields used in utils.py
                    self.tdc = None
                    self.vcore = None
                    self.aiclk = None
                    self.tdp = None
                    self.asic_temperature = None
                    self.vdd_limits = None
                    self.thm_limits = None
                    
                def get_field(self, tag):
                    if self.reader.is_entry_available(tag):
                        return self.reader.read_entry(tag)
                    return None
            
            # Map telemetry fields based on architecture
            if arch == ARCH.WORMHOLE_B0:
                telem_obj = UMDTelemetry(telem_reader, arch, self.umd_device)
                # Map wormhole-specific fields
                telem_obj.m3_app_fw_version = telem_obj.get_field(wormhole.TelemetryTag.M3_APP_FW_VERSION)
                telem_obj.arc1_fw_version = telem_obj.get_field(wormhole.TelemetryTag.ARC1_FW_VERSION)
                telem_obj.arc0_fw_version = telem_obj.get_field(wormhole.TelemetryTag.ARC0_FW_VERSION)
                telem_obj.asic_location = telem_obj.get_field(wormhole.TelemetryTag.ASIC_RO)
                telem_obj.fw_bundle_version = telem_obj.get_field(wormhole.TelemetryTag.FW_BUNDLE_VERSION)
                # Get board_id from telemetry tags
                board_id_high = telem_obj.get_field(wormhole.TelemetryTag.BOARD_ID_HIGH)
                board_id_low = telem_obj.get_field(wormhole.TelemetryTag.BOARD_ID_LOW)
                if board_id_high is not None and board_id_low is not None:
                    telem_obj.board_id = (board_id_high << 32) | board_id_low
                else:
                    # Fallback to device method
                    telem_obj.board_id = self.umd_device.get_board_id()
                # Common telemetry fields used in utils.py
                telem_obj.tdc = telem_obj.get_field(wormhole.TelemetryTag.TDC)
                telem_obj.vcore = telem_obj.get_field(wormhole.TelemetryTag.VCORE)
                telem_obj.aiclk = telem_obj.get_field(wormhole.TelemetryTag.AICLK)
                telem_obj.tdp = telem_obj.get_field(wormhole.TelemetryTag.TDP)
                telem_obj.asic_temperature = telem_obj.get_field(wormhole.TelemetryTag.ASIC_TEMPERATURE)
                telem_obj.vdd_limits = telem_obj.get_field(wormhole.TelemetryTag.VDD_LIMITS)
                telem_obj.thm_limits = telem_obj.get_field(wormhole.TelemetryTag.THM_LIMITS)
            else:
                telem_obj = UMDTelemetry(telem_reader, arch, self.umd_device)
                # Map universal fields
                telem_obj.asic_location = telem_obj.get_field(TelemetryTag.HARVESTING_STATE)
                telem_obj.fw_bundle_version = telem_obj.get_field(TelemetryTag.FLASH_BUNDLE_VERSION)
                # Get board_id from telemetry tags
                board_id_high = telem_obj.get_field(TelemetryTag.BOARD_ID_HIGH)
                board_id_low = telem_obj.get_field(TelemetryTag.BOARD_ID_LOW)
                if board_id_high is not None and board_id_low is not None:
                    telem_obj.board_id = (board_id_high << 32) | board_id_low
                else:
                    # Fallback to device method
                    telem_obj.board_id = self.umd_device.get_board_id()
                # Common telemetry fields used in utils.py
                telem_obj.tdc = telem_obj.get_field(TelemetryTag.TDC)
                telem_obj.vcore = telem_obj.get_field(TelemetryTag.VCORE)
                telem_obj.aiclk = telem_obj.get_field(TelemetryTag.AICLK)
                telem_obj.tdp = telem_obj.get_field(TelemetryTag.TDP)
                telem_obj.asic_temperature = telem_obj.get_field(TelemetryTag.ASIC_TEMPERATURE)
                telem_obj.vdd_limits = telem_obj.get_field(TelemetryTag.VDD_LIMITS)
                telem_obj.thm_limits = telem_obj.get_field(TelemetryTag.THM_LIMITS)
                # Fields only available in new telemetry format
                telem_obj.noc_translation_enabled = telem_obj.get_field(TelemetryTag.NOC_TRANSLATION)
                telem_obj.tensix_enabled_col = telem_obj.get_field(TelemetryTag.ENABLED_TENSIX_COL)
            
            self.telmetry_cache = telem_obj
            return self.telmetry_cache
        else:
            self.telmetry_cache = self.luwen_chip.get_telemetry()
            return self.telmetry_cache

    def get_telemetry_unchanged(self) -> Telemetry:
        if self.telmetry_cache is None:
            self.get_telemetry()

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
        if self.use_umd:
            return (self.umd_device.get_board_id() >> 36) & 0xFFFFF
        else:
            return self.luwen_chip.pci_board_type()

    def board_id(self):
        telem = self.get_telemetry_unchanged()
        return telem.board_id

    def noc_read(self, noc: int, x: int, y: int, addr: int, data: bytes):
        if self.use_umd:
            if (noc != 0):
                raise ValueError("UMD NOC must be 0")
            read_data = self.umd_device.noc_read(x, y, addr, len(data))
            data[:] = read_data
        else:
            self.luwen_chip.noc_read(noc, x, y, addr, data)

    def noc_read32(self, noc: int, x: int, y: int, addr: int):
        if self.use_umd:
            if (noc != 0):
                raise ValueError("UMD NOC must be 0")
            return self.umd_device.noc_read32(x, y, addr)
        else:
            return self.luwen_chip.noc_read32(noc, x, y, addr)

    def noc_write(self, noc: int, x: int, y: int, addr: int, data: bytes):
        if self.use_umd:
            if (noc != 0):
                raise ValueError("UMD NOC must be 0")
            self.umd_device.noc_write(x, y, addr, data)
        else:
            self.luwen_chip.noc_write(noc, x, y, addr, data)

    def noc_write32(self, noc: int, x: int, y: int, addr: int, data: int):
        if self.use_umd:
            if (noc != 0):
                raise ValueError("UMD NOC must be 0")
            self.umd_device.noc_write32(x, y, addr, data)
        else:
            self.luwen_chip.noc_write32(noc, x, y, addr, data)

    def noc_broadcast(self, noc: int, addr: int, data: bytes):
        if self.use_umd:
            if (noc != 0):
                raise ValueError("UMD NOC must be 0")
            for core in self.soc_desc.get_cores(CoreType.TENSIX):
                self.umd_device.noc_write(core.x, core.y, addr, data)
        else:
            self.luwen_chip.noc_broadcast(noc, addr, data)

    def noc_broadcast32(self, noc: int, addr: int, data: int):
        if self.use_umd:
            if (noc != 0):
                raise ValueError("UMD NOC must be 0")
            for core in self.soc_desc.get_cores(CoreType.TENSIX):
                self.umd_device.noc_write32(core.x, core.y, addr, data)
        else:
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

    def arc_msg(self, *args, **kwargs):
        if self.use_umd:
            # UMD arc_msg returns a vector where first element is the exit code and the following are the results.
            # To match the pyluwen format, we return [first result, exit code]
            result = self.umd_device.arc_msg(*args, **kwargs)
            return [result[1], result[0]]
        else:
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
        if self.use_umd:
            if self.eth_coord is None:
                return "N/A"
            return (self.eth_coord.x, self.eth_coord.y, self.eth_coord.rack, self.eth_coord.shelf)
        else:
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
        if self.use_umd:
            if self.eth_coord is None:
                return "N/A"
            return (self.eth_coord.x, self.eth_coord.y, self.eth_coord.rack, self.eth_coord.shelf)
        else:
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


class GsChip(TTChip):
    def __init__(self, *args, **kwargs):
        self.GRID_SIZE_X = 13
        self.GRID_SIZE_Y = 12
        self.NUM_TENSIX_X = self.GRID_SIZE_X - 1
        self.NUM_TENSIX_Y = self.GRID_SIZE_Y - 2

        self.PHYS_X_TO_NOC_0_X = [0, 12, 1, 11, 2, 10, 3, 9, 4, 8, 5, 7, 6]
        self.PHYS_Y_TO_NOC_0_Y = [0, 11, 1, 10, 2, 9, 3, 8, 4, 7, 5, 6]
        self.PHYS_X_TO_NOC_1_X = [12, 0, 11, 1, 10, 2, 9, 3, 8, 4, 7, 5, 6]
        self.PHYS_Y_TO_NOC_1_Y = [11, 0, 10, 1, 9, 2, 8, 3, 7, 4, 6, 5]
        self.NOC_0_X_TO_PHYS_X = reverse_mapping_list(self.PHYS_X_TO_NOC_0_X)
        self.NOC_0_Y_TO_PHYS_Y = reverse_mapping_list(self.PHYS_Y_TO_NOC_0_Y)
        self.NOC_1_X_TO_PHYS_X = reverse_mapping_list(self.PHYS_X_TO_NOC_1_X)
        self.NOC_1_Y_TO_PHYS_Y = reverse_mapping_list(self.PHYS_Y_TO_NOC_1_Y)

        super().__init__(*args, **kwargs)

    def get_tensix_locations(self):
        bad_row_bits = self.get_harvest_bits()
        bad_row_bits = bad_row_bits << 1

        bad_physical_rows = self._int_to_bits(bad_row_bits)

        disabled_rows = frozenset(
            map(
                lambda y: self.PHYS_Y_TO_NOC_0_Y[self.GRID_SIZE_Y - y - 1],
                bad_physical_rows,
            )
        )
        good_rows = filter(
            lambda y: y not in disabled_rows, [1, 2, 3, 4, 5, 7, 8, 9, 10, 11]
        )
        good_cores = list(
            itertools.product(list(range(1, self.GRID_SIZE_X)), good_rows)
        )

        return set(good_cores)

    def coord(self):
        return "N/A"

    def arch(self):
        return "Grayskull"

    def __repr__(self):
        return f"Grayskull[{self.interface_id}]"
