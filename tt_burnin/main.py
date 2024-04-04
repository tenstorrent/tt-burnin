# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import tt_burnin
from tt_burnin.chip import detect_chips, detect_local_chips, GsChip, WhChip
from tt_burnin.load_ttx import load_ttx_file, TtxFile, CoreId

import argparse


def start_burnin_gs(
    device, keep_trisc_under_reset: bool = False, stagger_start: bool = False
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

    load_ttx_file(
        device,
        TtxFile(
            "/mnt/motor/syseng/ttx-bank/power-virus/single-core-conv.loop.pm_enabled.20act.0wght.ttx"
        ),
        {CoreId(0, 0): device.get_tensix_locations()},
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
    device, keep_trisc_under_reset: bool = False, stagger_start: bool = False
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

    load_ttx_file(
        device,
        TtxFile(
            "/mnt/motor/syseng/ttx-bank/wh_B0/pv_workloads/build_pv_ssmodes_sync_v3_fp32acc_dcache_off/single-core-matrix-inf-loop-20act.80wght-lf8.ttx"
        ),
        {CoreId(0, 0): device.get_tensix_locations()},
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


def parse_args():
    # Parse arguments
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=tt_burnin.__version__,
    )

    subparsers = parser.add_subparsers(title="command", dest="command", required=True)


def main():
    args = parse_args()

    devices = detect_chips()

    try:
        for device in devices:
            if isinstance(device, GsChip):
                start_burnin_gs(device)
            elif isinstance(device, WhChip):
                start_burnin_wh(device)
            else:
                raise NotImplementedError(f"Don't support {device}")

        input(
            "Press Enter to STOP TT-Burnin on all boards (Please close all other processes running on the boards FIRST)"
        )
    finally:
        for device in devices:
            if isinstance(device, GsChip):
                stop_burnin_gs(device)
            elif isinstance(device, WhChip):
                stop_burnin_wh(device)
            else:
                raise NotImplementedError(f"Don't support {device}")
