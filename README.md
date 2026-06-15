# SI Test Data Plotter

A Streamlit-based web application for visualizing and analyzing test data from compressed archives (ZIP files). Designed to process results from semiconductor/electronic device testing, aggregating data from multiple test runs with interactive statistical analysis and visualization.

![Python](https://img.shields.io/badge/Python-3.x-blue)
![Streamlit](https://img.shields.io/badge/Streamlit-latest-red)
![Plotly](https://img.shields.io/badge/Plotly-interactive-purple)

## Features

- **Network-Aware Scanning** — Efficiently scans local and UNC (network) paths for ZIP archives containing test summaries
- **Smart ZIP Selection** — Automatically identifies DUT (Device Under Test) folders and selects the most recent ZIP per folder
- **Interactive Visualization** — Scatter plots and histograms with pass/fail overlay, control limits, mean, and ±3σ boundaries
- **Statistical Analysis** — Yield percentage, Cpk (process capability index), mean, standard deviation, min/max
- **Outlier Management** — Select and remove data points interactively
- **Custom Limits** — Override spec limits with custom values and recalculate yield in real time
- **CSV Export** — Export combined test data for further analysis

## Tech Stack

| Component | Purpose |
|-----------|---------|
| **Streamlit** | Web application framework |
| **Pandas** | Data manipulation and analysis |
| **Plotly** | Interactive charting and visualization |
| **Python 3.x** | Base language |

## Installation

### Prerequisites

- Python 3.x installed on your system

### Install Dependencies

```bash
pip install streamlit pandas plotly
```

No virtual environment is required — just install the three packages above globally.

## Usage

### Running the Application

```bash
streamlit run SI_test_data_plotter.py
```

The app will open in your browser at `http://localhost:8501`.

### Sample Data

To try the app with sample data, download the `move data 1by1.zip` file from the [WECO_SPC_app repository](https://github.com/your-org/WECO_SPC_app). Extract it to a local folder and use that path as input when the app prompts for a directory.

### Workflow

1. Enter a folder path (local or UNC network path) containing your test data ZIP files
2. The app scans the directory tree for DUT folders (identified by `_NS\d+` suffix)
3. Select a test parameter from the extracted data
4. View scatter plots and histograms with statistical overlays
5. Optionally adjust limits, remove outliers, or export to CSV

### Expected Data Format

ZIP files must contain a `summary.txt` with colon-delimited records:

```
Parameter : Unit : Category : Type : Value : PassFail : Range(Unit) : LowerLimit : UpperLimit
```

## Project Structure

```
data_extraction/
├── SI_test_data_plotter.py         # Main Streamlit application
├── SI_data_plotter.mp4             # Demo/tutorial video
├── SI TestData Plotter .docx       # Documentation
├── TestDataApp documentation.docx  # User documentation
├── README.md                       # This file
└── .vscode/
    └── settings.json               # VS Code configuration
```

## Key Capabilities

### Visualization

- **Scatter Plot** — Individual test results with Unit Number on the X-axis
- **Histogram** — Distribution of results, color-coded by PASS/FAIL status
- **Overlay Lines** — Control limits (LL/UL), mean, and ±3σ sigma boundaries
- **Interactive Selection** — Click to identify or remove outlier points

### Statistics

- **Yield** — Pass/fail percentage with support for custom limit overrides
- **Cpk** — Process capability index (minimum of CPU and CPL)
- **Adaptive Binning** — Freedman-Diaconis algorithm for histogram bin calculation
- **Summary Table** — Mean, standard deviation, min, max values

### Performance

- Results caching with a 5-minute TTL to reduce network traffic
- Efficient directory scanning using `os.scandir()` for network drives
- Symlink/reparse point detection to avoid infinite loops

## Configuration

No external configuration files are required. The application accepts a folder path as user input at runtime.

**Configurable in code:**

- Cache TTL (default: 5 minutes)
- Maximum histogram bins (capped at 60)
- DUT folder regex pattern (`_NS\d+`)

## Documentation

Additional documentation is available in:

- `SI TestData Plotter .docx` — Application overview
- `SI_data_plotter.mp4` — Video demo/tutorial
