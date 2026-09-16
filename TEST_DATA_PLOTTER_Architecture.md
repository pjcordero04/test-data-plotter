# TEST DATA PLOTTER — Architecture & Implementation Details

## Overview

`test_data_plotter.py` is a single-file Streamlit application (~1700 lines) that provides a unified data visualization platform for two test data sources. It runs as a local web server and renders interactive Plotly charts in the browser.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Streamlit Web App                         │
│                  (http://localhost:5002)                     │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐                                            │
│  │ Mode Toggle │  [SI Test]  [ETest]                        │
│  └──────┬──────┘                                            │
│         │                                                   │
│  ┌──────▼──────────────────┐  ┌──────────────────────────┐  │
│  │   SI Test Pipeline      │  │   ETest Pipeline         │  │
│  │                         │  │                          │  │
│  │  1. Folder scan (ZIPs)  │  │  1. Folder scan (CSVs)   │  │
│  │  2. DUT folder detect   │  │  2. CSV parsing          │  │
│  │  3. summary.txt parse   │  │  3. DataFrame build      │  │
│  │  4. DataFrame build     │  │  4. Unit# assignment     │  │
│  └──────────┬──────────────┘  └────────────┬─────────────┘  │
│             │                              │                 │
│             └──────────┬───────────────────┘                 │
│                        │                                     │
│  ┌─────────────────────▼─────────────────────────────────┐   │
│  │              Shared Visualization Layer                │   │
│  │                                                       │   │
│  │  • Scatter Plot (Plotly go.Scatter, markers)          │   │
│  │  • Histogram (Plotly go.Histogram + normal curve)     │   │
│  │  • Waveform (SI only — Plotly go.Scatter, lines)     │   │
│  │  • Statistics panel (Count/Mean/Std/Min/Max/Cpk)      │   │
│  │  • Custom Limits (toggle + disabled/enabled inputs)   │   │
│  │  • Delete Mode (outlier removal via drag-select)      │   │
│  │  • Download buttons (Long / Wide / Summary CSVs)      │   │
│  └───────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Module Structure (Single File)

The app is organized into clearly-separated sections within one Python file:

```
test_data_plotter.py
│
├── Lines 1-25       Imports (streamlit, pandas, numpy, plotly, scipy)
│
├── Lines 26-400     HELPER FUNCTIONS
│   ├── safe_float(), extract_first_number(), extract_unit()
│   ├── extract_sn_from_text()
│   ├── parse_summary_text()         — SI summary.txt parser
│   ├── list_csv_files_in_zip()      — ZIP CSV enumeration
│   ├── read_csv_from_zip()          — CSV extraction from ZIP
│   ├── read_summary_text_from_zip() — summary.txt extraction
│   ├── get_dut_folder()             — DUT folder detection (_NS\d+ pattern)
│   ├── compute_yield()              — Pass/Fail count against limits
│   ├── compute_cpk()                — Process capability index
│   ├── compute_mean_std_min_max()   — Basic statistics
│   ├── auto_histogram_bins()        — Freedman-Diaconis bin calculation
│   └── extract_selected_sns()       — Delete mode selection handler
│
├── Lines 400-700    ETEST PARSER (embedded, self-contained)
│   ├── etest_normalize_value()      — "223 mOhm" → (223.0, "mOhm")
│   ├── etest_parse_csv()            — Single ETEST CSV → dict
│   ├── etest_parse_timestamp()      — Date+Time → datetime
│   └── etest_load_folder()          — Folder → combined DataFrame
│
├── Lines 700-730    NETWORK SCANNER
│   ├── select_latest_zip_per_dut_fast()  — Live scan with progress
│   └── cached_latest_zip_selection()     — @st.cache_data wrapper
│
├── Lines 730-1260   SI TEST MODE UI
│   ├── Folder input + scan
│   ├── Download buttons (Long / Wide / Summary)
│   ├── Parameter/CSV dropdown (switches on plot type)
│   ├── Scatter plot with legend
│   ├── Histogram with normal curve
│   ├── Waveform plot (auto-detect: Hz→GHz or s→ns)
│   ├── Custom limits panel (toggle/disable/reset)
│   ├── Statistics display
│   └── Delete mode
│
└── Lines 1260-1700  ETEST MODE UI
    ├── Folder input + load
    ├── Unit# assignment (by timestamp)
    ├── Download buttons (Long / Wide / Summary)
    ├── Parameter dropdown
    ├── Scatter plot with legend
    ├── Histogram with normal curve
    ├── Custom limits panel (toggle/disable/reset)
    ├── Statistics display
    └── Delete mode
```

---

## Data Flow

### SI Test Mode

```
User enters folder path
        │
        ▼
┌─────────────────────────────┐
│ os.scandir() recursive walk │  Cached until user
│ Find all .zip files         │  presses Enter to re-scan
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ Group by DUT folder         │  Pattern: _NS\d+ in parent dir name
│ Select latest ZIP per DUT   │  By st_mtime (modification time)
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ Open each selected ZIP      │
│ Read summary.txt            │  UTF-8 with latin-1 fallback
│ Parse with parse_summary_text()
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ Combined DataFrame          │
│ Columns: SN, Parameter,     │
│   Lower Limit, Result,      │
│   Upper Limit, Pass/Fail,   │
│   Unit, Source, Unit Number  │
└─────────────────────────────┘
```

### ETest Mode

```
User enters folder path
        │
        ▼
┌─────────────────────────────┐
│ os.listdir() → .csv files   │
│ Sort by modification time   │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ For each CSV file:          │
│  • Parse header metadata    │  S/N, Cable Number, Test Date/Time
│  • Parse "Measured Values"  │  WIRE and 4WIRE rows
│  • Extract value + unit     │  "223 mOhm" → 223.0
│  • Extract upper limit      │  "320 mOhm" → 320.0
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ Combined DataFrame          │
│ Columns: File, SN,          │
│   CableNumber, RunNumber,   │
│   TestDate, TestTime,       │
│   Timestamp, FinalResult,   │
│   Parameter, Type,          │
│   Value, Unit, LowerLimit,  │
│   UpperLimit, Unit#,        │
│   CableSeq                  │
└─────────────────────────────┘
```

---

## Key Design Decisions

### 1. Single-File Architecture

The entire app is one Python file (~1700 lines). This was chosen for:
- **Deployment simplicity** — single `streamlit run` command, no package installation
- **Portability** — copy one file to any machine with Python + deps
- **No import issues** — no cross-directory module resolution problems

### 2. Embedded ETest Parser

The ETEST CSV parser is embedded inline rather than imported from the WECO_SPC_app directory:
- Avoids `sys.path` manipulation
- Makes TEST_DATA_PLOTTER fully self-contained
- Can diverge from the standalone `etest_csv_parser.py` if needed

### 3. Auto-Caching (Persistent Until Re-scan)

The scan result is cached indefinitely (`@st.cache_data` with no TTL):
- Data stays in memory for the entire session — switching parameters/plots is instant
- Users don't need to understand caching
- Press Enter on folder path to force a re-scan (Streamlit detects the "change" event)
- No expiry — avoids unexpected re-scans mid-analysis

### 4. Plotly for All Charts

Both SI and ETest use Plotly (`plotly.graph_objects`) rather than matplotlib:
- **Interactive** — zoom, pan, hover with data labels
- **Consistent** — same chart library across modes
- **Selection** — enables drag-select for Delete Mode
- **Legend click** — built-in trace toggle/highlight behavior

### 5. Custom Limits with Session State Reset

When "Use custom limits" is toggled OFF:
```python
if not use_new_limits:
    st.session_state[ll_key] = str(orig_ll)  # Force reset
    st.session_state[ul_key] = str(orig_ul)  # Force reset
```
This overcomes Streamlit's behavior of persisting `text_input` values in session_state even when you pass a different `value=` parameter.

### 6. Legend Instead of On-Plot Annotations

Limit/mean/sigma lines are shown as **invisible trace entries** in the legend:
```python
fig.add_hline(y=eff_ul, line_dash="dash", line_color="red", line_width=4)
fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
    line=dict(color="red", width=3, dash="dash"), name=f"UL ({eff_ul})"))
```
This avoids overlapping text on the plot while keeping values readable in the legend.

### 7. Waveform X-Axis Auto-Detection

The app detects the domain type from the CSV filename:
```python
csv_base = selected_csv.replace(".csv", "").upper()
is_time_domain = csv_base.startswith("Z")  # ZDD, ZSS, ZSD, ZDS

if is_time_domain:
    x_display = x_data * 1e9   # seconds → nanoseconds
else:
    x_display = x_data / 1e9   # Hz → GHz
```

| CSV starts with | Domain | Conversion | X-axis label |
|---|---|---|---|
| Z (ZDD, ZSS, etc.) | Time | × 10⁹ (s → ns) | Time (ns) |
| Anything else (S2_1, SDD, SCD) | Frequency | ÷ 10⁹ (Hz → GHz) | Frequency (GHz) |

### 8. Unit# Assignment by Timestamp

ETest files are numbered sequentially by their test timestamp (earliest = Unit# 1):
```python
file_order = etest_df.groupby("File")["Timestamp"].min().sort_values().index.tolist()
file_seq_map = {f: i + 1 for i, f in enumerate(file_order)}
```
This ensures consistent ordering even when Cable Numbers repeat across runs.

---

## State Management

Streamlit re-runs the entire script on every interaction. State is preserved via `st.session_state`:

| Key Pattern | Purpose |
|-------------|---------|
| `si_removed_sns_{param}` | List of SNs excluded from SI analysis |
| `etest_removed_sns_{param}` | List of SNs excluded from ETest analysis |
| `si_use_new_limits_{param}` | Whether custom limits are active (SI) |
| `etest_use_new_limits_{param}` | Whether custom limits are active (ETest) |
| `si_new_ll_{param}` | Custom lower limit value (SI) |
| `si_new_ul_{param}` | Custom upper limit value (SI) |
| `etest_new_ll_{param}` | Custom lower limit value (ETest) |
| `etest_new_ul_{param}` | Custom upper limit value (ETest) |
| `si_delete_mode_{param}` | Delete mode toggle state (SI) |
| `etest_delete_mode_{param}` | Delete mode toggle state (ETest) |

---

## Statistics Computation

### Yield
```python
passed = count(values where LL <= value <= UL)
yield_pct = 100 * passed / total
```

### Cpk (Process Capability Index)
```python
Cpu = (UL - mean) / (3σ)          # Distance to upper limit
Cpl = (mean - LL) / (3σ)          # Distance to lower limit
Cpk = min(Cpu, Cpl)               # Worst side
```
- Uses sample standard deviation (`ddof=1`)
- Returns `None` if σ=0 or fewer than 2 data points

### Normal Curve Overlay
```python
from scipy.stats import norm
x_range = linspace(mean - 4σ, mean + 4σ, 200)
bin_width = (max - min) / n_bins
y_norm = norm.pdf(x_range, mean, σ) * n * bin_width
```
Scaled to match histogram bar heights (count, not density).

---

## Delete Mode Implementation

1. Toggle "Delete Mode" ON → chart switches `dragmode` from `"zoom"` to `"select"`
2. User draws a box/lasso selection on the chart
3. Streamlit's `on_select="rerun"` fires, returning selected point indices
4. `extract_selected_sns()` maps indices → SN values
5. SNs are appended to `st.session_state[removed_key]`
6. App re-runs with those SNs filtered out of `filtered_plot`
7. "Restore All" button clears the list

---

## Performance Considerations

| Concern | Mitigation |
|---------|-----------|
| Large network folders (100K+ entries) | `os.scandir()` (not `os.walk`), DFS with stack |
| Repeated Streamlit reruns | `@st.cache_data` (no TTL) on scan function — persists until re-scan |
| DUT folder lookup per ZIP | `@lru_cache(maxsize=50000)` on path→folder mapping |
| Many waveform traces | Traces rendered at opacity=0.6 for visual clarity |
| Large DataFrames | Pivot/download computed once, not per-rerun (Streamlit caches widget state) |

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `streamlit` | ≥1.30.0 | Web framework, UI components, session state |
| `pandas` | ≥2.0.0 | DataFrame operations, CSV generation, pivot tables |
| `numpy` | any | Numeric operations (linspace for normal curve) |
| `plotly` | ≥5.18.0 | Interactive charts (scatter, histogram, waveform) |
| `scipy` | any | `scipy.stats.norm` for normal distribution PDF |

Standard library: `io`, `os`, `re`, `zipfile`, `pathlib`, `datetime`, `functools`

---

## Extending the App

### Adding a New Test Type

1. Add a new option to the radio button: `st.radio("Test Type", ["SI Test", "ETest", "New Type"])`
2. Add an `elif test_mode == "New Type":` block after the ETest section
3. Implement: folder input → data loading → DataFrame with at least `Parameter`, `Result`/`Value`, and limit columns
4. Reuse the shared helper functions: `compute_yield`, `compute_cpk`, `compute_mean_std_min_max`, `auto_histogram_bins`
5. Follow the same plot pattern: `go.Figure()` → add traces → `update_layout()` → `st.plotly_chart()`

### Adding a New Download Format

1. Compute the DataFrame in the desired format
2. Convert to CSV: `csv_bytes = df.to_csv(index=False).encode("utf-8")`
3. Add a `st.download_button(label=..., data=csv_bytes, file_name=..., mime="text/csv")`

### Adding a New Plot Type

1. Add the option to the radio button
2. Add an `elif plot_type == "New Plot":` block in the plot section
3. Build a `go.Figure()` with appropriate traces
4. Follow existing patterns for legend placement and layout

---

## File Locations

| Item | Path |
|------|------|
| Main app | `TEST_DATA_PLOTTER/test_data_plotter.py` |
| User docs | `TEST_DATA_PLOTTER/TEST_DATA_PLOTTER.md` |
| Architecture | `TEST_DATA_PLOTTER/TEST_DATA_PLOTTER_Architecture.md` |
| Streamlit config | `TEST_DATA_PLOTTER/.streamlit/config.toml` |

| ETEST standalone parser | `WECO_SPC_app/etest_csv_parser.py` |
| ETEST standalone stats app | `WECO_SPC_app/etest_statistics_app.py` |

---

## Changelog

| Date | Change |
|------|--------|
| 2026-09-16 | `compute_mean_std_min_max()` now returns **median** (5 values instead of 4) |
| 2026-09-16 | `build_summary_wide()` includes **Median** row in summary export |
| 2026-09-16 | Added `cached_parse_all_summaries()` — `@st.cache_data` wrapper for parsed summary data, eliminates re-reading ZIPs on parameter switch |
| 2026-09-16 | `parse_summary_text()` no longer stops at "Background Limit Results" — continues parsing with `BG` prefix; disambiguates duplicate parameter names by appending `[UL:value]` when limits differ |

---

## License

Internal use only — Koch Industries / Molex.
