# JV Measurements

Solar cell J-V characterization software for perovskite photovoltaics research.
Developed at UFABC — Universidade Federal do ABC, Santo André, SP, Brazil.

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

## License

MIT — see [LICENSE](LICENSE) for details.
