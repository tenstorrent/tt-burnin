# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

__all__ = ["CoreId", "TtxFile", "load_ttx_file", "TtxCompletionChecks"]

from dataclasses import dataclass
import itertools
import re
import struct
import zipfile
from typing import (
    Optional,
    Iterable,
    Tuple,
    AbstractSet,
    Mapping,
    NamedTuple,
    Dict,
    List,
    Any,
    Collection,
    AnyStr,
    IO,
    BinaryIO,
    ClassVar,
)

from yaml import safe_load

from tt_burnin.chip import BhChip, TTChip as Chip

DEFAULT_TIMEOUT_CYCLES = 100_000


# [0] = x, [1] = y
class CoreId(NamedTuple):
    x: int
    y: int

    def __str__(self) -> str:
        return f"{self.x}-{self.y}"

    @classmethod
    def parse(cls, text: AnyStr) -> "CoreId":
        m = re.fullmatch(r"(\d+)-(\d+)", str(text))
        if m is None:
            raise ValueError("Could not parse core id.")
        return cls(int(m[1]), int(m[2]))


class TtxFile(zipfile.ZipFile):
    def __init__(self, file, mode: str = "r"):
        super().__init__(file, mode)
        self._test_yaml = None

    def testdef(self) -> Any:
        if self._test_yaml is None:
            self._test_yaml = safe_load(self.open("test.yaml"))

        return self._test_yaml


BIN_FILE_V1_MAGIC = 0x9704266B
BIN_HEADER_STRUCT = struct.Struct("<IIIIIIII")


@dataclass
class ChunkHeader:
    address: int
    length: int
    reserved_mbz: int = 0

    CHUNK_HEADER_STRUCT: ClassVar = struct.Struct("<QII")

    def write(self, f: BinaryIO) -> None:
        f.write(
            ChunkHeader.CHUNK_HEADER_STRUCT.pack(
                self.address, self.length, self.reserved_mbz
            )
        )

    @staticmethod
    def read(f: IO[bytes]) -> Optional["ChunkHeader"]:
        b = f.read(ChunkHeader.CHUNK_HEADER_STRUCT.size)
        if len(b) == 0:
            return None
        if len(b) != ChunkHeader.CHUNK_HEADER_STRUCT.size:
            raise RuntimeError("Image file is truncated within chunk header.")

        ch = ChunkHeader.CHUNK_HEADER_STRUCT.unpack(b)
        if ch[2] != 0:
            raise RuntimeError("Chunk header contains nonzero in MBZ field.")

        return ChunkHeader(*ch)


def check_bin_header(f: IO[bytes]) -> None:
    b = f.read(BIN_HEADER_STRUCT.size)
    if len(b) != BIN_HEADER_STRUCT.size:
        raise RuntimeError("Image file is truncated within binary file header.")

    h = BIN_HEADER_STRUCT.unpack(b)

    if h[0] != BIN_FILE_V1_MAGIC:
        raise RuntimeError("Image file does not start with expected magic.")

    if any(map(lambda x: x != 0, h[1:])):
        raise RuntimeError("Image file header contains nonzero in MBZ field.")


def read_bin_image_chunks(f: IO[bytes]) -> Iterable[Tuple[int, bytes]]:
    check_bin_header(f)

    while True:
        chunk_header = ChunkHeader.read(f)
        if chunk_header is None:
            break

        chunk_data = f.read(chunk_header.length)
        if len(chunk_data) != chunk_header.length:
            raise RuntimeError("Image file is truncated within data chunk.")

        yield (chunk_header.address, chunk_data)


def read_hex_image_chunks(f: IO[bytes]) -> Iterable[Tuple[int, bytes]]:
    buffer = bytearray()
    addr = None
    for line in f.readlines():
        if len(line.strip()) > 0:
            if line.startswith(b"@"):
                if len(buffer) > 0:
                    assert addr is not None
                    yield addr, buffer
                # Word addr, so multiply it by 4 to turn it into byte address
                addr = int(f"0x{line[1:].strip().decode()}", 0) * 4
                buffer = bytearray()
            else:
                buffer.extend(
                    int(f"0x{line.strip().decode()}", 0).to_bytes(4, "little")
                )

    if len(buffer) > 0:
        assert addr is not None
        yield addr, buffer


def load_hex(chip: Chip, cores: Optional[Collection[CoreId]], bin: IO[bytes]) -> None:
    for address, data in read_hex_image_chunks(bin):
        if cores is None:
            chip.noc_broadcast(0, address, data)
        else:
            for core in cores:
                chip.noc_write(0, *core, address, data)


def check_hex(chip: Chip, cores: Optional[Collection[CoreId]], bin: IO[bytes]) -> None:
    for address, data in read_hex_image_chunks(bin):
        if cores is None:
            cores = chip.get_tensix_locations()
        for core in cores:
            buffer = bytearray(len(data))
            chip.noc_read(0, *core, address, buffer)
            if buffer != data:
                for b, d in zip(buffer, data):
                    assert (
                        b == d
                    ), f"Failed to write to core {address} {core} ({b} != {d})"


def load_bin(chip: Chip, cores: Optional[Collection[CoreId]], bin: IO[bytes]) -> None:
    for address, data in read_bin_image_chunks(bin):
        if cores is None:
            chip.noc_broadcast(0, address, data)
        else:
            for core in cores:
                chip.noc_write(0, *core, address, data)

def check_bin(chip: Chip, cores: Optional[Collection[CoreId]], bin: IO[bytes]) -> None:
    for address, data in read_bin_image_chunks(bin):
        if cores is None:
            cores = chip.get_tensix_locations()
        for core in cores:
            buffer = bytearray(len(data))
            chip.noc_read(0, *core, address, buffer)
            if buffer != data:
                for b, d in zip(buffer, data):
                    assert (
                        b == d
                    ), f"Failed to write to core {core} at address {address} ({b} != {d})"

# core_mapping: use logical (source) to physical (target) mapping
# Returns the physical cores that it loaded an image on to.
def load_ttx_file(
    chip: Chip,
    ttx: TtxFile,
    core_mapping: Mapping[CoreId, Collection[CoreId]],
    no_check: bool,
) -> AbstractSet[CoreId]:
    all_tensix_cores = set(CoreId(*c) for c in chip.get_tensix_locations())

    all_source_cores = set(core_mapping.keys())
    all_target_cores = set(itertools.chain.from_iterable(core_mapping.values()))

    if len(all_target_cores - all_tensix_cores) > 0:
        details = ", ".join(map(str, sorted(all_target_cores - all_tensix_cores)))
        raise RuntimeError(f"core_mapping targets cores that do not exist. ({details})")

    broadcast = (
        set(core_mapping.keys()) == set([CoreId(0, 0)])
        and set(core_mapping[CoreId(0, 0)]) == all_tensix_cores
    )

    # find all X-Y/(image|ckernels).(bin|hex)
    # If non image.bin, fail.
    # If any hex, fail.
    # If any core has ckernels.bin but not image.bin, fail.
    # If broadcast or single-core, and any cores other than 0-0, fail.
    # If neither broadcast nor single-core verify that all cores are in chip.get_tensix_locations()

    infolist = {info.filename: info for info in ttx.infolist()}

    files: Dict[str, set] = {
        "image.bin": set(),
        "image.hex": set(),
        "ckernels.bin": set(),
        "ckernels.hex": set(),
    }

    for info in infolist.values():
        m = re.fullmatch(r"(\d+)-(\d+)/((?:image|ckernels)\.(bin|hex))", info.filename)
        if m:
            x = int(m[1])
            y = int(m[2])

            image_type = m[4]

            # ignore empty images, they may exist for non-tensix cores
            if (image_type == "bin" and info.file_size > BIN_HEADER_STRUCT.size) or (
                image_type == "hex" and info.file_size > 0
            ):
                files[m[3]].add(CoreId(x, y))

    # Ignore hexs that are shadowed by bins. These existed for back-compat with old loaders.
    files["image.hex"] -= files["image.bin"]
    files["ckernels.hex"] -= files["ckernels.bin"]

    image_bins = files["image.bin"]
    image_hex = files["image.hex"]
    ckernels_bins = files["ckernels.bin"]
    ckernels_hex = files["ckernels.hex"]

    del files

    if not image_bins and not image_hex:
        raise RuntimeError("TTX is empty.")

    if len(ckernels_bins.union(ckernels_hex) - image_bins.union(image_hex)) > 0:
        details = ", ".join(map(str, sorted(ckernels_bins - image_bins)))
        raise RuntimeError(f"TTX has cores with ckernels but no image. ({details})")

    if len(image_bins.union(image_hex) - all_source_cores) > 0:
        details = ", ".join(
            map(str, sorted(image_bins.union(image_hex) - all_source_cores))
        )
        raise RuntimeError(
            f"TTX has images for cores with no physical mapping. ({details})"
        )

    def load_core(
        load_core: CoreId, target_cores: Optional[Collection[CoreId]], no_check: bool
    ) -> None:
        image_bin = f"{load_core}/image.bin"
        ckernels_bin = f"{load_core}/ckernels.bin"

        image_hex = f"{load_core}/image.hex"
        ckernels_hex = f"{load_core}/ckernels.hex"

        if image_hex in infolist:
            load_hex(chip, target_cores, ttx.open(infolist[image_hex], mode="r"))
            if not no_check:
                check_hex(chip, target_cores, ttx.open(infolist[image_hex], mode="r"))

        if ckernels_hex in infolist:
            load_hex(chip, target_cores, ttx.open(infolist[ckernels_hex], mode="r"))
            if not no_check:
                check_hex(
                    chip, target_cores, ttx.open(infolist[ckernels_hex], mode="r")
                )

        if image_bin in infolist:
            load_bin(chip, target_cores, ttx.open(infolist[image_bin], mode="r"))
            if not no_check:
                check_bin(chip, target_cores, ttx.open(infolist[image_bin], mode="r"))

        if ckernels_bin in infolist:
            load_bin(chip, target_cores, ttx.open(infolist[ckernels_bin], mode="r"))
            if not no_check:
                check_bin(
                    chip, target_cores, ttx.open(infolist[ckernels_bin], mode="r")
                )

    if broadcast:
        load_core(CoreId(0, 0), None, no_check)
        return all_tensix_cores

    else:
        for source_core in image_bins:
            load_core(source_core, core_mapping[source_core], no_check)
        return image_bins


@dataclass(frozen=True)
class AddressData:
    address: int
    data: bytes

    @classmethod
    def load_from_hex_file(cls, f: IO) -> "AddressData":
        line = f.readline()
        if len(line) == 0:
            raise RuntimeError("Empty memory file.")

        if line[0] != ord("@"):
            raise RuntimeError("Memory file does not start with address line.")

        address = int(line[1:], 16) * 4
        data = b"".join(map(lambda line: int(line, 16).to_bytes(4, "little"), f))

        return cls(address, data)


class TtxCompletionChecks:
    def __init__(self, ttx: Optional[TtxFile] = None):
        if ttx is not None:
            self._checks = self._load_from_ttx(ttx)
        else:
            self._checks = {}

    def _load_from_ttx(self, ttx: TtxFile) -> Dict[CoreId, List[AddressData]]:
        completion_checks: Dict[CoreId, List[AddressData]] = {}

        for item in ttx.testdef()["completion"]["filematches"]:
            core = CoreId(int(item["node"]["x"]), int(item["node"]["y"]))
            content = AddressData.load_from_hex_file(ttx.open(item["file"]))

            completion_checks.setdefault(core, []).append(content)

        return completion_checks

    # Create TtxCompletionChecks using pre-loaded (x,y,file) tuples.
    @classmethod
    def preloaded(
        cls, ttx: TtxFile, checks: Collection[Tuple[int, int, str]]
    ) -> "TtxCompletionChecks":
        completion_checks = cls()

        for x, y, file in checks:
            core = CoreId(x, y)
            content = AddressData.load_from_hex_file(ttx.open(file))
            completion_checks._checks.setdefault(core, []).append(content)

        return completion_checks

    # This merges checks for all cores, matching the original C++ code.
    def remap_for_broadcast(self, cores: Collection[CoreId]) -> "TtxCompletionChecks":
        all_checks = list(itertools.chain.from_iterable(self._checks.values()))

        new = type(self)()
        new._checks = {core: all_checks.copy() for core in cores}
        return new

    def remap_to_physical(
        self, core_mapping: Mapping[CoreId, Collection[CoreId]]
    ) -> "TtxCompletionChecks":
        new = type(self)()
        new._checks = {
            physical: self._checks[logical].copy()
            for logical, physicals in core_mapping.items()
            for physical in physicals
            if logical in self._checks
        }
        return new

    def filter_physical(
        self, loaded_cores: AbstractSet[CoreId]
    ) -> "TtxCompletionChecks":
        new = type(self)()
        new._checks = {
            core: checks.copy()
            for core, checks in self._checks.items()
            if core in loaded_cores
        }
        return new

    def empty(self) -> bool:
        return not bool(self._checks)

    def _read_block(self, chip: Chip, core: CoreId, address: int, length: int) -> bytes:
        alignment_offset = address % 16
        read_address = address - alignment_offset

        read_length = length + alignment_offset
        read_rounding = 16 - (read_length % 16)
        read_length += read_rounding if read_rounding < 16 else 0

        buffer = bytearray(read_length)
        chip.noc_read(0, *core, read_address, buffer)

        return buffer[alignment_offset : alignment_offset + length]

    def test(self, chip: Chip) -> bool:
        for core, checks in self._checks.items():
            for check in checks:
                data = self._read_block(chip, core, check.address, len(check.data))
                if data != check.data:
                    return False
        return True
