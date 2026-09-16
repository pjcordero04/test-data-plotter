# TEST DATA PLOTTER

A unified Streamlit web application for visualizing and analyzing test data from two sources: **Signal Integrity (SI)** testing and **Electrical Testing (ETest)**. Select the test type via a radio button and the app adapts its data loading, plotting, and export features accordingly.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Streamlit](https://img.shields.io/badge/Streamlit-1.30%2B-red)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)

---

## Features

### Common (Both Modes)
- **Interactive Scatter Plot** — X=unit sequence, Y=measured value, with mean/±3σ/limit lines in legend
- **Interactive Histogram** — Distribution with normal curve overlay and limit markers
- **Statistics Panel** — Count, Mean, Median, Std Dev, Min, Max, Lower Limit, Upper Limit, Yield, Cpk
- **Custom Limits** — Toggle to override spec limits; text boxes are disabled (grayed out) when off, reset to originals automatically
- **Download Summary CSV** — All parameters summarized in wide format (rows=stats, columns=parameters)
- **Delete Mode** — Drag-select outlier points to exclude from analysis (per parameter, with Restore All)
- **Compact UI** — Optimized padding and font sizes for maximum data density
- **Royal blue data points** — Consistent color across both modes
- **Legend** — Upper-right legend showing LL, UL, Mean, ±3σ values (no on-plot text labels)

### SI Test Mode
- Scans a directory (local or UNC network share) for ZIP files
- Selects the **latest ZIP per DUT folder** (by modification time)
- Reads `summary.txt` from ZIPs and parses SI test parameters
- **Auto-caching** — Scan results cached until you press Enter on the folder path to re-scan
- **Waveform Plot** — Overlay CSV traces across all DUTs; auto-detects domain:
  - S-parameters (SDD, SCD, S2_1, etc.) → X-axis in **GHz**
  - Z-parameters (ZDD, ZSS, etc.) → X-axis in **nanoseconds**
- **Waveform hover highlight** — Hover over a trace to highlight it (others fade)
- **Dropdown switches** — When Waveform is selected, the Parameter dropdown becomes a CSV File selector
- **Download CSV (Long)** — One row per SN per parameter
- **Download CSV (Wide)** — Pivot table, one row per SN
- **Download Summary** — Stats per parameter in wide format

### ETest Mode
- Loads a folder of individual **ETEST CSV files** (one per cable tested)
- Parses WIRE and 4WIRE resistance measurements from the "Measured Values" section
- Each wire pair is a separate parameter (e.g., `4WIRE_END1_1_END2_1`)
- **Unit# column** — Assigned by earliest file timestamp for sequencing
- **Normal curve overlay** on histogram
- **Download CSV (Long)** — Full measurement data with Unit#
- **Download CSV (Wide)** — One row per cable with Unit# as first column
- **Download Summary** — Stats per parameter in wide format

---

## Requirements

### Python Dependencies

```
streamlit>=1.30.0
pandas>=2.0.0
numpy
plotly>=5.18.0
scipy
```

### Install

```bash
pip install streamlit pandas numpy plotly scipy
```

---

## Usage

### Launch the App

```bash
cd "C:\Users\patric54\OneDrive - kochind.com\Documents\2026\automation apps\TEST_DATA_PLOTTER"
streamlit run test_data_plotter.py
```

The app opens in your browser at `http://localhost:5002`.

### SI Test Mode

1. Select **SI Test** at the top
2. Enter the folder path (local or UNC) containing ZIP files with `summary.txt`
3. The app auto-scans for ZIPs, selects the latest per DUT folder, and parses results
4. Choose a parameter from the dropdown (or CSV file when in Waveform mode)
5. Toggle between Scatter Plot / Histogram / Waveform
6. Toggle Custom Limits ON to edit LL/UL; toggle OFF to reset to originals
7. Download data in Long, Wide, or Summary format

### ETest Mode

1. Select **ETest** at the top
2. Enter the folder path containing ETEST CSV files (e.g., `2175420047_500V_LINK_TestReport_*.csv`)
3. The app loads and parses all CSV files in the folder
4. Choose a parameter (wire pair) from the dropdown
5. Toggle between Scatter Plot and Histogram
6. Toggle Custom Limits ON to edit LL/UL; toggle OFF to reset to originals
7. Download data in Long, Wide, or Summary format

---

## Custom Limits Behavior

| State | Text Boxes | Values Used | Cpk Calculated Against |
|-------|-----------|-------------|------------------------|
| Toggle OFF | Grayed out, show original limits | Original from data | Original LL/UL |
| Toggle ON | Editable | User-entered values | Custom LL/UL |
| ON → OFF | Reset to originals, grayed out | Original from data | Original LL/UL |

---

## Cpk Computation

```
Cpu = (UL - Mean) / (3 × σ)
Cpl = (Mean - LL) / (3 × σ)
Cpk = min(Cpu, Cpl)    — when both limits exist
Cpk = Cpu              — upper limit only
Cpk = Cpl              — lower limit only
```

| Cpk | Interpretation |
|-----|---------------|
| ≥ 1.33 | Capable |
| 1.00 – 1.33 | Marginal |
| < 1.00 | Not capable |

---

## ETEST CSV File Format

Each CSV file represents one cable tested. The app parses the "Measured Values" section:

```
 #, Instruction Type, From Points, To Points, Value Measured, Value Expected, Measured Detail
 1, WIRE, END1_1, END2_1, 0.8 Ohm, 2.6 Ohm, Measured 0.8 Ohm
 ...
 8, 4WIRE, END1_1, END2_1, 193 mOhm, 320 mOhm, Measured 193 mOhm
```

**Parameters are named as:** `{Type}_{FromPoint}_{ToPoint}`
- Example: `WIRE_END1_1_END2_1`, `4WIRE_END1_S_END2_S`

**Limits:**
- WIRE: Upper limit from "Value Expected" (e.g., 2.6 Ohm), no lower limit
- 4WIRE: Upper limit from "Value Expected" (e.g., 320 mOhm), no lower limit

---

## Download Formats

### Long Format
One row per measurement per unit:

| Unit# | SN | CableNumber | Parameter | Value | Unit | UpperLimit | ... |
|-------|-----|-------------|-----------|-------|------|------------|-----|
| 1 | 0 | 1 | 4WIRE_END1_1_END2_1 | 193 | mOhm | 320 | ... |
| 1 | 0 | 1 | WIRE_END1_1_END2_1 | 0.8 | Ohm | 2.6 | ... |

### Wide Format
One row per unit, each parameter as a column:

| Unit# | CableNumber | SN | 4WIRE_END1_1_END2_1 | WIRE_END1_1_END2_1 | ... |
|-------|-------------|-----|---------------------|--------------------|----|
| 1 | 1 | 0 | 193 | 0.8 | ... |
| 2 | 2 | 213 | 194 | 0.7 | ... |

### Summary Format
One row per statistic, each parameter as a column:

| | 4WIRE_END1_1_END2_1 | WIRE_END1_1_END2_1 | ... |
|---|---------------------|--------------------|----|
| Unit | mOhm | Ohm | ... |
| Count | 321 | 321 | ... |
| Mean | 194.349 | 0.700 | ... |
| Std Dev | 2.050 | 0.006 | ... |
| Min | 190.0 | 0.700 | ... |
| Max | 203.0 | 0.800 | ... |
| Range | 13.0 | 0.100 | ... |
| Lower Limit | | | ... |
| Upper Limit | 320.0 | 2.6 | ... |
| Cpk (lower) | | | ... |
| Cpk (upper) | 20.43 | 113.45 | ... |

---

## SI Test Data Structure

```
<root_folder>\
└── <part_number>_NS<serial>\
    └── <timestamp>.zip
        ├── summary.txt          # Parsed for parameter results
        └── *.csv                # S-parameter waveform data (frequency in Hz)
```

The app identifies DUT folders by matching the `_NS\d+` pattern in directory names.

---

## Configuration

| File | Purpose |
|------|---------|
| `.streamlit/config.toml` | Streamlit server port (default: 5002) |

---

## Directory Structure

```
TEST_DATA_PLOTTER/
├── test_data_plotter.py               # Main Streamlit application
├── TEST_DATA_PLOTTER.md               # User documentation (this file)
├── TEST_DATA_PLOTTER_Architecture.md  # Technical architecture document
├── .streamlit/
│   └── config.toml                    # Port configuration
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No zip files found" (SI mode) | Verify the folder contains subfolders with `_NS<number>` in their names |
| "No valid measurement data" (ETest) | Ensure the folder has `.csv` files with a "Measured Values" section |
| Data not refreshing (SI mode) | Press Enter on the folder path to force a re-scan (cache refreshes) |
| All values identical (ETest) | WIRE measurements at 0.1 Ohm resolution may show zero std dev — this is normal |
| Cpk shows "N/A" | Requires at least 2 data points and non-zero standard deviation |
| Waveform legend too crowded | Click a legend entry to isolate that trace; double-click to toggle |

---

## Changelog

| Date | Change |
|------|--------|
| 2026-09-16 | Added **Median** to statistics panel, summary CSV export, and `compute_mean_std_min_max()` (both SI and ETest modes) |
| 2026-09-16 | Added **cached data extraction** — parsed summary data is now cached so switching parameters is instant |
| 2026-09-16 | Added **Background Limit parameters** — parameters after "Background Limit Results" are now included with a `BG` prefix |
| 2026-09-16 | Added **duplicate parameter disambiguation** — parameters with the same name but different limits are distinguished (e.g., `[UL:0.1]` vs `[UL:None]`) |

---

## License

Internal use only — Koch Industries / Molex.
