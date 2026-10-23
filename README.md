# JV Measurements

[![Linux CI](https://github.com/Polimante/jv-measurements/actions/workflows/tests.yml/badge.svg)](https://github.com/Polimante/jv-measurements/actions/workflows/tests.yml)
[![Windows CI](https://github.com/Polimante/jv-measurements/actions/workflows/tests-windows.yml/badge.svg)](https://github.com/Polimante/jv-measurements/actions/workflows/tests-windows.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python >=3.9](https://img.shields.io/badge/python-%3E%3D3.9-blue.svg)](https://www.python.org/downloads/)

Solar cell J-V characterization software for perovskite photovoltaics research.
Developed by Lucas Polimante for PoloGroup at UFABC — Universidade Federal do ABC.

## Installation (lab users)

1. Download the latest installer from the [Releases page](https://github.com/Polimante/jv-measurements/releases)
2. Run `JV-Measurements-Setup-vX.X.X.exe`
3. Follow the installation wizard
4. When prompted, click **"Install Python dependencies"** — wait for it to finish
5. Double-click the **"JV Measurements"** shortcut on your desktop

## System requirements

- Windows 10 or 11
- Python 3.9 or higher ([download here](https://www.python.org/downloads/)) — add to PATH during install
- Keithley 2400 SourceMeter with KUSB-488B GPIB adapter
- KI-488 driver (Tektronix) + NI-VISA runtime

## For developers

```bash
git clone https://github.com/Polimante/jv-measurements.git
cd jv-measurements
pip install -r requirements.txt
python main.py
```

## Updates

The software checks for updates automatically at startup.
You can also check manually: **Help → Check for Updates**

## Validation

The software ships with a deterministic validation suite that generates
publication-grade evidence of correctness using synthetic solar cells.

```bash
python -m validation.run_suite --config validation/config.yaml
```

See [validation/README.md](validation/README.md) for methodology, acceptance
criteria, and how the outputs map to manuscript figures.

## License

MIT — see [LICENSE](LICENSE) for details.
