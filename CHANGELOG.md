# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.2.2 - 31/07/2024

### Added
- Added glx reset support
- Threaded start and end of burnin to increase burnin speed
- Added prints to indicate which chip we are currently running on
- Added support for bh harvesting

## 0.2.1 - 16/01/2024

### Bug fix
- Fix for https://github.com/tenstorrent/tt-burnin/issues/6
- BH reports asic temperature as a signed 16_16 int unlike GS and WH
- Added missing support to report BH asic temperatre

## 0.2.0 - 29/10/2024

### Added
- BH burnin support

## 0.1.1 - 14/05/2024

### Updated

- Bumped luwen (0.3.8) and tt_tools_common (1.4.3) lib versions

## 0.1.0 - 04/04/2024

First release of opensource tt-burnin

### Added
- GS and WH burnin support
