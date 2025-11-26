# TT-BURNIN

Tenstorrent Burnin (TT-Burnin) is a command line utility to run a high power consumption workload on TT devices.

## Official Repository

[https://github.com/tenstorrent/tt-burnin/](https://github.com/tenstorrent/tt-burnin/)

## Getting started
Build and editing instruction are as follows -

### Building from Git

After cloning the repo, install and source rust for the luwen library
```
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```
Upgrade pip to the latest and install tt-burnin
```
pip3 install --upgrade pip
pip3 install .
```
### Optional - for TT-Tools developers

Generate and source a python3 environment
```
python3 -m venv .venv
source .venv/bin/activate
pip3 install --upgrade pip
```
For users who would like to edit the code without re-building, install burnin in editable mode.
```
pip3 install --editable .
```

# Usage

Command line arguments
```
usage: tt-burnin [-h] [-v] [--reset_file reset_config.json]
```

## Getting Help!

Running tt-burnin with the ```-h, --help``` flag should bring up something that looks like this

```
usage: tt-burnin [-h] [-v] [--reset_file reset_config.json]

Tenstorrent Burnin (TT-Burnin) is a command line utility to run a high power consumption workload on TT devices.

optional arguments:
  -h, --help            show this help message and exit
  -v, --version         show program's version number and exit
  --reset_file reset_config.json
                        Provide a custom reset json file for the host.Generate a default reset json file with the -g option with tt-smi.
```

## Running tt-burnin

After building run `tt-burnin` to run the program. 

TT-Burnin performs the following steps when running:
1. Reset the boards on the host to get them into a known good state
2. Start the power hungry workload on all boards
3. Output a realtime telemetry command line widget to monitor the devices
4. After user hits "enter" to stop the workload, another reset is performed to bring the boards back to known good state

A full run of burnin should look like - 

```
$ tt-burnin

 Detected Chips: 3
┏━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Pci Dev ID ┃ Board Type ┃ Device Series ┃ Board Number    ┃ Coordinates  ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ 0          │ grayskull  │ e75           │ 100007311523010 │ N/A          │
│ 1          │ wormhole   │ n300 L        │ 10001451170801d │ [0, 0, 0, 0] │
│ N/A        │ wormhole   │ n300 R        │ 10001451170801d │ [1, 0, 0, 0] │
└────────────┴────────────┴───────────────┴─────────────────┴──────────────┘
 Resetting devices on host... 
 Re-initializing boards after reset.... 
 Detected Chips: 3

 Starting TT-Burnin workload on all boards. WARNING: Opening SMI might cause unexpected behavior 
                                                                                                                                                               
┏━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ ID ┃ Core Voltage (V) ┃ Core Current (A) ┃ AICLK (MHz) ┃ Power (W)     ┃ Core Temp (°C) ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ 0  │ 0.74 / 0.84      │  73.0 / 170.0    │  653 / 1000 │  54.0 /  56.0 │ 41.3 / 75.0    │
│ 1  │ 0.75 / 0.95      │ 110.0 / 160.0    │  872 / 1000 │  84.0 /  85.0 │ 37.9 / 75.0    │
│ 2  │ 0.75 / 0.95      │ 110.0 / 160.0    │  885 / 1000 │  85.0 /  85.0 │ 33.4 / 75.0    │
└────┴──────────────────┴──────────────────┴─────────────┴───────────────┴────────────────┘
 Press Enter to STOP TT-Burnin on all boards...

 Stopping TT-Burnin workload on all boards. 

 Resetting devices on host... 
 Re-initializing boards after reset.... 
 Detected Chips: 3
```

## Supported products

tt-burnin can be used with Wormhole and Blackhole products. The last version that supported Grayskull products was [v0.2.5](https://github.com/tenstorrent/tt-burnin/releases/tag/v0.2.5).

## License

Apache 2.0 - https://www.apache.org/licenses/LICENSE-2.0.txt
