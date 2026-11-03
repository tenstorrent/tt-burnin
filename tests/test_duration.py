# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
import sys
from unittest.mock import MagicMock
import pytest

for _mod in [
    "pyluwen",
    "jsons",
    "tt_tools_common",
    "tt_tools_common.ui_common",
    "tt_tools_common.ui_common.themes",
    "tt_tools_common.reset_common",
    "tt_tools_common.reset_common.bh_reset",
    "tt_tools_common.reset_common.wh_reset",
    "tt_tools_common.reset_common.galaxy_reset",
    "tt_tools_common.utils_common",
    "tt_tools_common.utils_common.tools_utils",
    "tt_tools_common.utils_common.system_utils",
]:
    sys.modules.setdefault(_mod, MagicMock())

from tt_burnin.main import format_duration, parse_args, parse_duration


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3600", 3600.0),
        ("45s", 45.0),
        ("30m", 1800.0),
        ("2h", 7200.0),
        ("1d", 86400.0),
        ("1h30m", 5400.0),
        ("1.5h", 5400.0),
    ],
)
def test_parse_duration_valid(raw, expected):
    assert parse_duration(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "0", "-10", "0m", "abc", "10w"])
def test_parse_duration_invalid(raw):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_duration(raw)


def test_format_duration():
    assert format_duration(0) == "00:00:00"
    assert format_duration(65) == "00:01:05"
    assert format_duration(3661) == "01:01:01"
    assert format_duration(86400) == "24:00:00"


def test_parse_args_duration(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tt-burnin", "--duration", "2h"])
    args = parse_args()
    assert args.duration == 7200.0
