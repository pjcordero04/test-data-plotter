"""
Combined Test Data Plotter — SI (Signal Integrity) and ETest (Electrical Test)

A single Streamlit app that plots test data from either:
  - SI Test: ZIP archives containing summary.txt (resistance/signal measurements)
  - ETest: Folders of CSV files from easywire cable testers

Select the mode via a radio button at the top of the page.
"""

import io
import os
import re
import zipfile
from pathlib import Path
from datetime import datetime
from functools import lru_cache

import numpy as np
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from scipy.stats import norm


# ============================================================
# SI Helpers
# ============================================================

def safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


def extract_first_number(s):
    if s is None:
        return None
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(s))
    return float(m.group(0)) if m else None


def extract_unit(range_str):
    match = re.search(r"\)\s*([a-zA-Z]+)", str(range_str))
    return match.group(1) if match else ""


def extract_sn_from_text(text):
    for line in text.splitlines()[:5]:
        match = re.search(r"S/N:\s*(\S+)", line)
        if match:
            return match.group(1)
    match = re.search(r"S/N:\s*(\S+)", text)
    return match.group(1) if match else "UNKNOWN"


def parse_summary_text(text, source_label=None):
    data = []
    sn = extract_sn_from_text(text)
    stop_marker = "Background Limit Results"
    in_bg_section = False

    for line in text.splitlines():
        if stop_marker.lower() in line.lower():
            in_bg_section = True
            continue
        if ":" not in line:
            continue

        parts = [p.strip() for p in line.split(":")]
        if len(parts) < 9:
            continue

        try:
            parameter = f"{parts[0]} | {parts[1]} | {parts[2]} | {parts[3]} | {parts[6]}"
            if in_bg_section:
                parameter = f"BG {parameter}"
            value = safe_float(parts[4])
            result = parts[5]
            lower = extract_first_number(parts[7])
            upper = extract_first_number(parts[8])
            unit = extract_unit(parts[6])

            data.append({
                "SN": sn,
                "Parameter": parameter,
                "Lower Limit": lower,
                "Result": value,
                "Upper Limit": upper,
                "Pass/Fail": result,
                "Unit": unit,
                "Source": source_label
            })
        except Exception:
            continue

    # --- Disambiguate duplicate parameter names that have different limits ---
    from collections import Counter
    param_counts = Counter(row["Parameter"] for row in data)
    duplicates = {p for p, c in param_counts.items() if c > 1}

    if duplicates:
        for param_name in duplicates:
            entries = [r for r in data if r["Parameter"] == param_name]
            # Group by (Lower Limit, Upper Limit) to check if limits differ
            limit_combos = set((r["Lower Limit"], r["Upper Limit"]) for r in entries)
            if len(limit_combos) > 1:
                # Different limits exist — disambiguate by appending upper limit
                for r in entries:
                    ul = r["Upper Limit"]
                    ul_label = "None" if ul is None else str(ul)
                    r["Parameter"] = f"{r['Parameter']} [UL:{ul_label}]"

    return pd.DataFrame(
        data,
        columns=["SN", "Parameter", "Lower Limit", "Result", "Upper Limit", "Pass/Fail", "Unit", "Source"]
    )


def list_csv_files_in_zip(zip_path: Path):
    """Return a list of CSV file names inside a zip."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            return [n for n in zf.namelist() if n.lower().endswith(".csv")]
    except Exception:
        return []


def read_csv_from_zip(zip_path: Path, csv_member: str):
    """Read a CSV file from a zip and return a DataFrame."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        with zf.open(csv_member, "r") as mf:
            raw = mf.read()
    try:
        text = raw.decode("utf-8")
    except Exception:
        text = raw.decode("latin-1", errors="replace")
    return pd.read_csv(io.StringIO(text))


def read_summary_text_from_zip(zip_path: Path, member: str = "summary.txt"):
    """
    Fast path: assume summary.txt exists at the zip root.
    If it's nested, we fall back to searching the names (still only for selected zips).
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        try:
            with zf.open(member, "r") as mf:
                raw = mf.read()
        except KeyError:
            # Fallback: find any member that endswith summary.txt
            found = None
            for name in zf.namelist():
                if name.lower().endswith("summary.txt"):
                    found = name
                    break
            if not found:
                raise KeyError("summary.txt not found")
            with zf.open(found, "r") as mf:
                raw = mf.read()
            member = found

    try:
        text = raw.decode("utf-8")
    except Exception:
        text = raw.decode("latin-1", errors="replace")
    return member, text


def get_dut_folder(zip_path: Path) -> Path:
    """DUT folder detection."""
    patterns = [
        re.compile(r"_NS\d+\.$", re.IGNORECASE),
        re.compile(r"_NS\d+$", re.IGNORECASE),
    ]

    for parent in [zip_path.parent, *zip_path.parents]:
        name = parent.name
        if any(p.search(name) for p in patterns):
            return parent
    return zip_path.parent


@lru_cache(maxsize=50000)
def get_dut_folder_cached(zip_path_str: str) -> str:
    return str(get_dut_folder(Path(zip_path_str)))


# ============================================================
# ETest CSV Parser (embedded inline)
# ============================================================

def etest_normalize_value(value_str):
    """
    Parse a measurement string like '223 mOhm' or '0.7 Ohm' into (float, str).
    Returns (value_as_float, unit_string) or (None, None) if unparseable.
    """
    if not value_str or 'not tested' in str(value_str).lower():
        return None, None

    value_str = str(value_str).strip()
    m = re.match(r"([\d.]+)\s*(mOhm|Ohm|GOhm|MOhm)", value_str)
    if m:
        return float(m.group(1)), m.group(2)
    return None, None


def etest_parse_csv(file_path):
    """
    Parse one ETEST CSV file and return structured data.
    """
    result = {
        'file_path': file_path,
        'sn': 'UNKNOWN',
        'cable_number': None,
        'run_number': None,
        'test_date': '',
        'test_time': '',
        'final_result': 'UNKNOWN',
        'test_name': '',
        'measurements': [],
    }

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return result

    lines = text.splitlines()

    # --- Parse header metadata ---
    for line in lines:
        line_stripped = line.strip()

        m = re.match(r"\s*S/N:\s*,\s*(.+)", line_stripped)
        if m:
            result['sn'] = m.group(1).strip()
            continue

        m = re.match(r"\s*Cable Number:\s*,\s*(\d+)", line_stripped)
        if m:
            result['cable_number'] = int(m.group(1))
            continue

        m = re.match(r"\s*Run Number:\s*,\s*(\d+)", line_stripped)
        if m:
            result['run_number'] = int(m.group(1))
            continue

        m = re.match(r"\s*Test Date:\s*,\s*(.+)", line_stripped)
        if m:
            result['test_date'] = m.group(1).strip()
            continue

        m = re.match(r"\s*Test Time:\s*,\s*(.+)", line_stripped)
        if m:
            result['test_time'] = m.group(1).strip()
            continue

        m = re.match(r"\s*Final Test Result:\s*,\s*(.+)", line_stripped)
        if m:
            result['final_result'] = m.group(1).strip()
            continue

        m = re.match(r"\s*Test Name:\s*,\s*(.+)", line_stripped)
        if m:
            result['test_name'] = m.group(1).strip()
            continue

    # --- Parse "Measured Values" section ---
    in_measured_section = False
    for line in lines:
        line_stripped = line.strip()

        if 'Title:' in line and 'Measured Values' in line:
            in_measured_section = True
            continue

        if in_measured_section and line_stripped.startswith('#,'):
            continue

        if in_measured_section and line_stripped.startswith('Title:'):
            break

        if in_measured_section and line_stripped:
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 6:
                try:
                    row_num = parts[0].strip()
                    if not row_num.isdigit():
                        continue

                    instr_type = parts[1].strip()
                    if instr_type not in ('WIRE', '4WIRE'):
                        continue

                    from_point = parts[2].strip()
                    to_point = parts[3].strip()
                    value_str = parts[4].strip()
                    expected_str = parts[5].strip()

                    value, unit = etest_normalize_value(value_str)
                    upper_limit, limit_unit = etest_normalize_value(expected_str)

                    parameter = f"{instr_type}_{from_point}_{to_point}"

                    result['measurements'].append({
                        'type': instr_type,
                        'from_point': from_point,
                        'to_point': to_point,
                        'value': value,
                        'unit': unit,
                        'lower_limit': None,
                        'upper_limit': upper_limit,
                        'limit_unit': limit_unit,
                        'parameter': parameter,
                    })
                except (ValueError, IndexError):
                    continue

    return result


def etest_parse_timestamp(test_date, test_time):
    """Parse Test Date and Test Time into a datetime object."""
    if not test_date or not test_time:
        return None
    try:
        combined = f"{test_date} {test_time}"
        return datetime.strptime(combined, "%m/%d/%Y %I:%M:%S %p")
    except (ValueError, TypeError):
        return None


def etest_load_folder(folder_path, include_failed=True):
    """
    Load all ETEST CSV files from a folder and return a combined DataFrame.
    """
    rows = []

    if not os.path.isdir(folder_path):
        return pd.DataFrame()

    csv_files = sorted([
        f for f in os.listdir(folder_path)
        if f.lower().endswith('.csv') and os.path.isfile(os.path.join(folder_path, f))
    ])

    for fname in csv_files:
        fpath = os.path.join(folder_path, fname)
        parsed = etest_parse_csv(fpath)

        if not include_failed and parsed['final_result'].lower() == 'failed':
            continue

        if not parsed['measurements']:
            continue

        timestamp = etest_parse_timestamp(parsed['test_date'], parsed['test_time'])

        for meas in parsed['measurements']:
            if meas['value'] is None:
                continue

            rows.append({
                'File': fname,
                'SN': parsed['sn'],
                'CableNumber': parsed['cable_number'],
                'RunNumber': parsed['run_number'],
                'TestDate': parsed['test_date'],
                'TestTime': parsed['test_time'],
                'Timestamp': timestamp,
                'FinalResult': parsed['final_result'],
                'Parameter': meas['parameter'],
                'Type': meas['type'],
                'FromPoint': meas['from_point'],
                'ToPoint': meas['to_point'],
                'Value': meas['value'],
                'Unit': meas['unit'],
                'LowerLimit': meas['lower_limit'],
                'UpperLimit': meas['upper_limit'],
                'LimitUnit': meas['limit_unit'],
            })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    if 'Timestamp' in df.columns and df['Timestamp'].notna().any():
        df = df.sort_values('Timestamp').reset_index(drop=True)
    elif 'CableNumber' in df.columns:
        df = df.sort_values('CableNumber').reset_index(drop=True)

    return df


# ============================================================
# Shared Statistics Helpers
# ============================================================

def compute_yield(df_in: pd.DataFrame, ll, ul, value_col="Result"):
    vals = pd.to_numeric(df_in[value_col], errors="coerce").dropna()
    total = int(vals.shape[0])
    if total == 0:
        return 0, 0, 0, 0.0

    mask = pd.Series(True, index=vals.index)
    if ll is not None:
        mask &= (vals >= ll)
    if ul is not None:
        mask &= (vals <= ul)

    passed = int(mask.sum())
    failed = total - passed
    yield_pct = 100.0 * passed / total
    return total, passed, failed, yield_pct


def compute_cpk(df_in: pd.DataFrame, ll, ul, value_col="Result"):
    """
    Compute Cpk using effective LL/UL and result values.
    - Both limits: Cpk = min(Cpu, Cpl)
    - Upper only:  Cpk = Cpu = (UL - mean) / (3 sigma)
    - Lower only:  Cpk = Cpl = (mean - LL) / (3 sigma)
    Returns None when Cpk is not computable.
    """
    if ll is None and ul is None:
        return None

    vals = pd.to_numeric(df_in[value_col], errors="coerce").dropna()
    if vals.shape[0] < 2:
        return None

    mean = float(vals.mean())
    sigma = float(vals.std(ddof=1))
    if sigma <= 0:
        return None

    if ll is not None and ul is not None:
        cpu = (ul - mean) / (3.0 * sigma)
        cpl = (mean - ll) / (3.0 * sigma)
        return min(cpu, cpl)
    elif ul is not None:
        return (ul - mean) / (3.0 * sigma)
    else:
        return (mean - ll) / (3.0 * sigma)


def compute_cpk_separate(vals, ll, ul):
    """
    Compute Cpk lower and Cpk upper separately.
    Returns (cpk_lower, cpk_upper) — either can be None.
    """
    if vals.shape[0] < 2:
        return None, None

    mean = float(vals.mean())
    sigma = float(vals.std(ddof=1))
    if sigma <= 0:
        return None, None

    cpk_lower = None
    cpk_upper = None

    if ll is not None:
        cpk_lower = (mean - ll) / (3.0 * sigma)
    if ul is not None:
        cpk_upper = (ul - mean) / (3.0 * sigma)

    return cpk_lower, cpk_upper


def compute_mean_std_min_max(df_in: pd.DataFrame, value_col="Result"):
    """Compute mean, sample std, min, max of values."""
    vals = pd.to_numeric(df_in[value_col], errors="coerce").dropna()
    if vals.empty:
        return None, None, None, None

    mean = float(vals.mean())
    std = float(vals.std(ddof=1)) if vals.shape[0] >= 2 else None
    min_val = float(vals.min())
    max_val = float(vals.max())
    return mean, std, min_val, max_val


def auto_histogram_bins(values: pd.Series, min_bins: int = 8, max_bins: int = 60) -> int:
    """Compute a data-driven histogram bin count (Freedman-Diaconis / Sturges)."""
    vals = pd.to_numeric(values, errors="coerce").dropna()
    n = int(vals.shape[0])
    if n <= 1:
        return 1

    vmin = float(vals.min())
    vmax = float(vals.max())
    vrange = vmax - vmin
    if vrange <= 0:
        return 1

    q1 = float(vals.quantile(0.25))
    q3 = float(vals.quantile(0.75))
    iqr = q3 - q1

    if iqr > 0:
        bin_width = 2.0 * iqr / (n ** (1.0 / 3.0))
        if bin_width > 0:
            bins = int((vrange / bin_width) + 0.999999)
        else:
            bins = int((n.bit_length())) + 1
    else:
        bins = int((n.bit_length())) + 1

    bins = max(min_bins, min(max_bins, bins))
    return bins


def extract_selected_point_indices(selection_event):
    """Normalize Streamlit plotly selection payload and return selected point indices."""
    if not selection_event:
        return []

    points = []
    if isinstance(selection_event, dict):
        points = selection_event.get("selection", {}).get("points", [])
    else:
        sel = getattr(selection_event, "selection", None)
        if sel is not None:
            points = getattr(sel, "points", [])

    indices = []
    for p in points:
        if isinstance(p, dict):
            idx = p.get("point_index")
        else:
            idx = getattr(p, "point_index", None)
        if idx is not None:
            indices.append(idx)
    return indices


def extract_selected_sns(selection_event, filtered_plot: pd.DataFrame, plot_type: str, sn_col="SN", value_col="Result"):
    """Extract selected SNs from plot selection for both scatter and histogram."""
    if selection_event is None or filtered_plot.empty:
        return []

    points = []
    if isinstance(selection_event, dict):
        points = selection_event.get("selection", {}).get("points", [])
    else:
        sel = getattr(selection_event, "selection", None)
        if sel is not None:
            points = getattr(sel, "points", [])

    selected_sns = set()
    selected_indices = []
    selected_x = []

    for p in points:
        if isinstance(p, dict):
            idx = p.get("point_index")
            custom = p.get("customdata")
            x_val = p.get("x")
        else:
            idx = getattr(p, "point_index", None)
            custom = getattr(p, "customdata", None)
            x_val = getattr(p, "x", None)

        if idx is not None:
            selected_indices.append(idx)

        if custom is not None:
            selected_sns.add(str(custom))

        x_num = extract_first_number(x_val)
        if x_num is not None:
            selected_x.append(x_num)

    if plot_type == "Scatter Plot" and selected_indices:
        for idx in selected_indices:
            if 0 <= idx < len(filtered_plot):
                selected_sns.add(str(filtered_plot.iloc[idx][sn_col]))

    if plot_type == "Histogram" and selected_x:
        x_min = min(selected_x)
        x_max = max(selected_x)
        vals = pd.to_numeric(filtered_plot[value_col], errors="coerce")
        mask = vals.between(x_min, x_max, inclusive="both")
        selected_sns.update(filtered_plot.loc[mask, sn_col].astype(str).tolist())

    return sorted(selected_sns)


# ============================================================
# Summary Export Helper (shared by both modes)
# ============================================================

def build_summary_wide(df, parameters, value_col, unit_col, ll_col, ul_col):
    """
    Build a wide-format summary DataFrame:
      rows = [Unit, Count, Mean, Std Dev, Min, Max, Range, Lower Limit, Upper Limit, Cpk (lower), Cpk (upper)]
      columns = parameters
    """
    summary_data = {"Statistic": ["Unit", "Count", "Mean", "Std Dev", "Min", "Max",
                                   "Range", "Lower Limit", "Upper Limit", "Cpk (lower)", "Cpk (upper)"]}

    for param in parameters:
        subset = df[df["Parameter"] == param]
        vals = pd.to_numeric(subset[value_col], errors="coerce").dropna()
        n = int(vals.shape[0])
        mean = float(vals.mean()) if n > 0 else None
        std = float(vals.std(ddof=1)) if n >= 2 else None
        vmin = float(vals.min()) if n > 0 else None
        vmax = float(vals.max()) if n > 0 else None
        vrange = (vmax - vmin) if (vmin is not None and vmax is not None) else None

        # Get unit
        unit_series = subset[unit_col].dropna()
        unit_val = str(unit_series.iloc[0]) if not unit_series.empty else ""

        # Get limits
        ll_series = pd.to_numeric(subset[ll_col], errors="coerce").dropna()
        ul_series = pd.to_numeric(subset[ul_col], errors="coerce").dropna()
        ll = float(ll_series.iloc[0]) if not ll_series.empty else None
        ul = float(ul_series.iloc[0]) if not ul_series.empty else None

        cpk_lower, cpk_upper = compute_cpk_separate(vals, ll, ul)

        def fmt(v):
            if v is None:
                return "None"
            return f"{v:.4f}" if isinstance(v, float) else str(v)

        summary_data[param] = [
            unit_val,
            str(n),
            fmt(mean),
            fmt(std),
            fmt(vmin),
            fmt(vmax),
            fmt(vrange),
            fmt(ll),
            fmt(ul),
            fmt(cpk_lower),
            fmt(cpk_upper),
        ]

    return pd.DataFrame(summary_data)


# ============================================================
# Fast network scan: latest zip per DUT folder (SI mode)
# ============================================================

def select_latest_zip_per_dut_fast(root: Path, progress_every: int = 2000):
    """Walk with os.scandir, selecting latest zip per DUT folder by mtime."""
    best = {}
    scanned_entries = 0
    found_zips = 0

    stack = [str(root)]
    status = st.empty()

    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    scanned_entries += 1
                    if scanned_entries % progress_every == 0:
                        status.info(
                            f"Scanning... entries={scanned_entries:,}  zips_found={found_zips:,}  "
                            f"dut_folders={len(best):,}"
                        )

                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue

                    if not entry.is_file():
                        continue

                    if not entry.name.lower().endswith(".zip"):
                        continue

                    found_zips += 1
                    try:
                        mtime = entry.stat().st_mtime
                    except OSError:
                        continue

                    zpath = Path(entry.path)
                    dut_folder_str = get_dut_folder_cached(str(zpath))

                    cur_best = best.get(dut_folder_str)
                    if cur_best is None or mtime > cur_best["mtime"]:
                        best[dut_folder_str] = {"zip_path": zpath, "mtime": mtime}

        except (PermissionError, FileNotFoundError):
            continue

    status.empty()
    return best, {"scanned_entries": scanned_entries, "found_zips": found_zips, "dut_folders": len(best)}


@st.cache_data(show_spinner=False)
def cached_parse_all_summaries(zip_info_tuple):
    """
    Read and parse summary.txt from all selected ZIPs.
    Cached so switching parameters doesn't re-read files.
    zip_info_tuple: tuple of (folder_str, zip_path_str, mtime) for hashability.
    """
    all_dfs = []
    selection_rows = []

    for folder_str, zip_path_str, mtime in zip_info_tuple:
        zpath = Path(zip_path_str)
        mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

        try:
            member, text = read_summary_text_from_zip(zpath, "summary.txt")
            df_one = parse_summary_text(text, source_label=f"{zpath}:{member}")
            if not df_one.empty:
                all_dfs.append(df_one)

            selection_rows.append({
                "Folder": folder_str,
                "Selected Zip": zpath.name,
                "ZIP Modified Time": mtime_str,
                "Summary Member": member,
                "Rows Parsed": 0 if df_one is None else int(df_one.shape[0]),
                "Status": "parsed"
            })

        except KeyError:
            selection_rows.append({
                "Folder": folder_str,
                "Selected Zip": zpath.name,
                "ZIP Modified Time": mtime_str,
                "Summary Member": "summary.txt",
                "Rows Parsed": 0,
                "Status": "no summary.txt"
            })
        except Exception as e:
            selection_rows.append({
                "Folder": folder_str,
                "Selected Zip": zpath.name,
                "ZIP Modified Time": mtime_str,
                "Summary Member": "summary.txt",
                "Rows Parsed": 0,
                "Status": f"error: {type(e).__name__}: {e}"
            })

    if all_dfs:
        df = pd.concat(all_dfs, ignore_index=True)
    else:
        df = pd.DataFrame(columns=["SN", "Parameter", "Lower Limit", "Result", "Upper Limit", "Pass/Fail", "Unit", "Source"])

    return df, selection_rows


@st.cache_data(show_spinner=False)
def cached_latest_zip_selection(root_str: str):
    root = Path(root_str)
    best = {}
    stack = [str(root)]
    scanned_entries = 0
    found_zips = 0

    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    scanned_entries += 1
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    if not entry.is_file():
                        continue
                    if not entry.name.lower().endswith(".zip"):
                        continue

                    found_zips += 1
                    try:
                        mtime = entry.stat().st_mtime
                    except OSError:
                        continue

                    zpath = Path(entry.path)
                    dut_folder_str = get_dut_folder_cached(str(zpath))
                    cur_best = best.get(dut_folder_str)
                    if cur_best is None or mtime > cur_best["mtime"]:
                        best[dut_folder_str] = {"zip_path": zpath, "mtime": mtime}
        except (PermissionError, FileNotFoundError):
            continue

    meta = {"scanned_entries": scanned_entries, "found_zips": found_zips, "dut_folders": len(best)}
    return best, meta


# ============================================================
# Streamlit App — Page Config & Mode Selection
# ============================================================

st.set_page_config(layout="wide", initial_sidebar_state="collapsed", page_title="TestDataPlotter")
st.markdown("<br>", unsafe_allow_html=True)
st.title("Test Data Plotter")

st.markdown(
    """
    <style>
    .block-container {padding-top: 0.7rem; padding-bottom: 0.7rem;}
    div[data-testid="stVerticalBlock"] > div:has(> div[data-testid="stAlert"]) {margin-bottom: 0.25rem;}
    div[data-testid="stHorizontalBlock"] {gap: 0.5rem;}
    p, label, .stMarkdown {font-size: 0.92rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

test_mode = st.radio("Test Type", ["SI Test", "ETest"], horizontal=True)


# ============================================================
# SI TEST MODE
# ============================================================

if test_mode == "SI Test":
    st.caption(
        "Enter a folder path (local or UNC). The app scans for ZIPs, selects the **latest ZIP per DUT folder** "
        "(by ZIP modified time), then reads **summary.txt** from those selected ZIPs and plots results."
    )

    ctrl_path, ctrl_details = st.columns([7, 1.5], gap="small")
    with ctrl_path:
        folder_path = st.text_input("Folder path", value=r"", key="si_folder_path")
    with ctrl_details:
        show_scan_details = st.checkbox("Details", value=False, key="si_details")

    if folder_path:
        p = Path(folder_path)

        if not p.exists() or not p.is_dir():
            st.error("Provided path does not exist or is not a directory. Please enter a valid folder path.")
            st.stop()

        st.caption(f"Scanning under: {p}")

        # 1) Scan + select latest zip per DUT folder (cached for 5 min; press Enter to re-scan)
        with st.spinner("Scanning..."):
            best_zip_by_folder, meta = cached_latest_zip_selection(str(p))

        st.caption(
            f"Scan done. Entries scanned: {meta['scanned_entries']:,} | "
            f"ZIPs found: {meta['found_zips']:,} | "
            f"DUT folders selected: {meta['dut_folders']:,}"
        )

        if not best_zip_by_folder:
            st.warning("No zip files found.")
            st.stop()

        # 2) Read summary.txt from selected zips only (CACHED)
        folders_sorted = sorted(best_zip_by_folder.items(), key=lambda kv: kv[0])

        # Build a hashable tuple for the cache key: (folder, zip_path, mtime)
        zip_info_tuple = tuple(
            (folder_str, str(rec["zip_path"]), rec["mtime"])
            for folder_str, rec in folders_sorted
        )

        with st.spinner("Reading summary.txt from selected latest ZIPs..."):
            df, selection_rows = cached_parse_all_summaries(zip_info_tuple)

        if show_scan_details:
            with st.expander("Selected latest zip per DUT folder"):
                st.dataframe(pd.DataFrame(selection_rows), use_container_width=True)

        if df.empty:
            st.warning("No summary.txt content was successfully parsed from the selected ZIP files.")
            st.stop()

        # 4) Unit Number per SN (1-based)
        sn_list = df["SN"].unique()
        sn_to_row = {sn: i for i, sn in enumerate(sn_list, start=1)}
        df["Unit Number"] = df["SN"].map(sn_to_row)

        # Download buttons
        part_number = p.name
        dl_col1, dl_col2, dl_col3 = st.columns([1, 1, 1], gap="small")

        # Long format
        csv_long_bytes = df.to_csv(index=False).encode("utf-8")
        with dl_col1:
            st.download_button(
                label="Download CSV (Long)",
                data=csv_long_bytes,
                file_name=f"{part_number}_long.csv",
                mime="text/csv",
                key="si_dl_long",
            )

        # Wide format
        df_wide = df.pivot_table(index="SN", columns="Parameter", values="Result", aggfunc="first")
        df_wide = df_wide.reset_index()
        csv_wide_bytes = df_wide.to_csv(index=False).encode("utf-8")
        with dl_col2:
            st.download_button(
                label="Download CSV (Wide)",
                data=csv_wide_bytes,
                file_name=f"{part_number}_wide.csv",
                mime="text/csv",
                key="si_dl_wide",
            )

        # Summary format
        si_parameters = sorted(df["Parameter"].unique())
        si_summary_df = build_summary_wide(df, si_parameters, "Result", "Unit", "Lower Limit", "Upper Limit")
        csv_summary_bytes = si_summary_df.to_csv(index=False).encode("utf-8")
        with dl_col3:
            st.download_button(
                label="Download Summary",
                data=csv_summary_bytes,
                file_name=f"{part_number}_summary.csv",
                mime="text/csv",
                key="si_dl_summary",
            )

        # 5) Parameter / plot controls
        st.divider()
        sel_col, type_col = st.columns([4.2, 1.8], gap="small")
        with type_col:
            plot_type = st.radio("Plot", ["Scatter Plot", "Histogram", "Waveform"], horizontal=True, key="si_plot_type")

        # The dropdown switches content based on plot type:
        # - Scatter/Histogram: shows Parameter list
        # - Waveform: shows CSV file list
        csv_options = []
        selected_csv = None
        if plot_type == "Waveform":
            all_csv_names = set()
            for folder_str, rec in folders_sorted:
                csv_list = list_csv_files_in_zip(rec["zip_path"])
                all_csv_names.update(csv_list)
            csv_options = sorted(all_csv_names)
            with sel_col:
                selected_csv = st.selectbox("CSV File", csv_options, key="si_waveform_csv_select") if csv_options else None
            # Still need selected_param for session state keys
            selected_param = df["Parameter"].unique()[0] if len(df["Parameter"].unique()) > 0 else ""
        else:
            with sel_col:
                selected_param = st.selectbox("Parameter", df["Parameter"].unique(), key="si_param")

        filtered = df[df["Parameter"] == selected_param].copy()
        unit = filtered["Unit"].iloc[0] if not filtered.empty else ""

        # Original limits from data
        orig_ll_series = pd.to_numeric(filtered["Lower Limit"], errors="coerce").dropna()
        orig_ul_series = pd.to_numeric(filtered["Upper Limit"], errors="coerce").dropna()
        orig_ll = float(orig_ll_series.iloc[0]) if not orig_ll_series.empty else None
        orig_ul = float(orig_ul_series.iloc[0]) if not orig_ul_series.empty else None

        # Initialize removed SNs in session state
        removed_sns_key = f"si_removed_sns_{selected_param}"
        if removed_sns_key not in st.session_state:
            st.session_state[removed_sns_key] = []

        # Initialize per-parameter state if not yet set
        ll_key = f"si_new_ll_{selected_param}"
        ul_key = f"si_new_ul_{selected_param}"
        if ll_key not in st.session_state:
            st.session_state[ll_key] = "" if orig_ll is None else str(orig_ll)
        if ul_key not in st.session_state:
            st.session_state[ul_key] = "" if orig_ul is None else str(orig_ul)

        new_ll_str = st.session_state[ll_key]
        new_ul_str = st.session_state[ul_key]
        use_new_limits = st.session_state.get(f"si_use_new_limits_{selected_param}", False)

        new_ll_val = extract_first_number(new_ll_str) if str(new_ll_str).strip() != "" else None
        new_ul_val = extract_first_number(new_ul_str) if str(new_ul_str).strip() != "" else None
        eff_ll = new_ll_val if use_new_limits else orig_ll
        eff_ul = new_ul_val if use_new_limits else orig_ul

        # Apply removed SNs filter
        filtered_plot = filtered[~filtered["SN"].isin(st.session_state[removed_sns_key])].copy()

        # Plot + right-side controls panel
        if plot_type == "Waveform":
            plot_col = st.container()
            right_col = None
        else:
            plot_col, right_col = st.columns([4.3, 1.7], gap="small")
        if plot_type in ("Scatter Plot", "Histogram"):
            with right_col:
                st.subheader("Custom Limits")
                use_new_limits = st.toggle(
                    "Use custom limits",
                    value=use_new_limits,
                    key=f"si_use_new_limits_{selected_param}",
                )

                # Text inputs: disabled when custom limits is OFF, reset to originals
                if not use_new_limits:
                    # Force reset session_state to original limits when disabled
                    st.session_state[f"si_new_ll_{selected_param}"] = "" if orig_ll is None else str(orig_ll)
                    st.session_state[f"si_new_ul_{selected_param}"] = "" if orig_ul is None else str(orig_ul)

                new_ll_str = st.text_input(
                    "LL (blank = none)",
                    key=f"si_new_ll_{selected_param}",
                    disabled=not use_new_limits,
                )
                new_ul_str = st.text_input(
                    "UL (blank = none)",
                    key=f"si_new_ul_{selected_param}",
                    disabled=not use_new_limits,
                )

                new_ll_val = extract_first_number(new_ll_str) if str(new_ll_str).strip() != "" else None
                new_ul_val = extract_first_number(new_ul_str) if str(new_ul_str).strip() != "" else None
                eff_ll = new_ll_val if use_new_limits else orig_ll
                eff_ul = new_ul_val if use_new_limits else orig_ul

                total_n, pass_n, fail_n, yield_pct = compute_yield(filtered_plot, eff_ll, eff_ul)
                cpk_val = compute_cpk(filtered_plot, eff_ll, eff_ul)
                mean_val, std_val, min_val, max_val = compute_mean_std_min_max(filtered_plot)
                cpk_txt = "N/A" if cpk_val is None else f"{cpk_val:.3f}"
                mean_txt = "N/A" if mean_val is None else f"{mean_val:.4f}"
                std_txt = "N/A" if std_val is None else f"{std_val:.4f}"
                min_txt = "N/A" if min_val is None else f"{min_val:.4f}"
                max_txt = "N/A" if max_val is None else f"{max_val:.4f}"
                ll_disp = "None" if eff_ll is None else f"{eff_ll}"
                ul_disp = "None" if eff_ul is None else f"{eff_ul} {unit}"

                st.markdown(
                    f"""
                    <div style='font-size:14px; line-height:1.6;'>
                    <b>Count:</b> {total_n}<br>
                    <b>Mean:</b> {mean_txt} {unit}<br>
                    <b>Std Dev:</b> {std_txt}<br>
                    <b>Min:</b> {min_txt}<br>
                    <b>Max:</b> {max_txt}<br>
                    <b>Lower Limit:</b> {ll_disp}<br>
                    <b>Upper Limit:</b> {ul_disp}<br>
                    <b>Yield:</b> {yield_pct:.2f}% ({pass_n}/{total_n})<br>
                    <b>Cpk:</b> {cpk_txt}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        # Delete mode
        st.divider()

        delete_mode_key = f"si_delete_mode_{selected_param}"
        if delete_mode_key not in st.session_state:
            st.session_state[delete_mode_key] = False

        col1, col2, col3 = st.columns([1, 2, 2])

        with col1:
            delete_mode = st.toggle(
                "Delete Mode",
                value=st.session_state[delete_mode_key],
                key=f"si_toggle_delete_{selected_param}"
            )
            st.session_state[delete_mode_key] = delete_mode

        with col2:
            if delete_mode:
                st.caption("Delete mode: drag-select points to remove.")

        with col3:
            if st.session_state[removed_sns_key]:
                if st.button("Restore All", key=f"si_btn_restore_all_{selected_param}"):
                    st.session_state[removed_sns_key] = []
                    st.rerun()

        if delete_mode:
            st.caption("Delete mode ON")

        if st.session_state[removed_sns_key]:
            st.caption(f"Removed points: {', '.join(st.session_state[removed_sns_key])}")

        mapping = filtered_plot[["Unit Number", "SN"]].drop_duplicates().sort_values("Unit Number")

        fig = go.Figure()

        if plot_type == "Scatter Plot":
            fig.add_trace(go.Scatter(
                x=filtered_plot["Unit Number"],
                y=filtered_plot["Result"],
                mode="markers",
                marker=dict(color="royalblue", size=8),
                text=filtered_plot["SN"],
                customdata=filtered_plot["SN"],
                hovertemplate="Unit %{x}<br>SN %{text}<br>Result %{y}<extra></extra>",
                name="Measurement"
            ))

            if eff_ll is not None:
                fig.add_hline(y=eff_ll, line_dash="dash", line_color="red", line_width=4)
                fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="red", width=3, dash="dash"), name=f"LL ({eff_ll})"))
            if eff_ul is not None:
                fig.add_hline(y=eff_ul, line_dash="dash", line_color="red", line_width=4)
                fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="red", width=3, dash="dash"), name=f"UL ({eff_ul})"))

            if mean_val is not None:
                fig.add_hline(y=mean_val, line_dash="dash", line_color="orange", line_width=4)
                fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="orange", width=3, dash="dash"), name=f"Mean ({mean_val:.3f})"))

            if mean_val is not None and std_val is not None:
                plus3 = mean_val + 3.0 * std_val
                minus3 = mean_val - 3.0 * std_val
                fig.add_hline(y=plus3, line_dash="dash", line_color="#333333", line_width=4)
                fig.add_hline(y=minus3, line_dash="dash", line_color="#333333", line_width=4)
                fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="#333333", width=3, dash="dash"), name=f"+3\u03c3 ({plus3:.3f})"))
                fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="#333333", width=3, dash="dash"), name=f"-3\u03c3 ({minus3:.3f})"))

            unit_nums = mapping["Unit Number"].tolist()
            max_ticks = 50
            step = max(1, len(unit_nums) // max_ticks)
            tickvals = unit_nums[::step]
            ticktext = [str(int(v)) for v in tickvals]

            drag_mode = "select" if st.session_state[delete_mode_key] else "zoom"

            fig.update_layout(
                title=selected_param,
                xaxis_title="Unit Number",
                yaxis_title=f"Result ({unit})",
                xaxis=dict(
                    tickmode="array",
                    tickvals=tickvals,
                    ticktext=ticktext,
                    tickangle=-90,
                ),
                showlegend=True,
                legend=dict(x=1.02, y=1, xanchor="left", yanchor="top", font=dict(size=10)),
                dragmode=drag_mode,
                margin=dict(l=10, r=10, t=50, b=10),
            )

        elif plot_type == "Histogram":
            all_results = pd.to_numeric(filtered_plot["Result"], errors="coerce").dropna()
            nbins = auto_histogram_bins(all_results)

            fig.add_trace(go.Histogram(
                x=all_results,
                customdata=filtered_plot.loc[all_results.index, "SN"].astype(str),
                name="Measurements", marker_color="royalblue", opacity=0.65, nbinsx=nbins
            ))

            # Normal curve overlay
            if mean_val is not None and std_val is not None and len(all_results) > 1:
                x_range = np.linspace(
                    mean_val - 4 * std_val,
                    mean_val + 4 * std_val,
                    200
                )
                bin_width = (all_results.max() - all_results.min()) / nbins if nbins > 0 else 1
                y_norm = norm.pdf(x_range, mean_val, std_val) * len(all_results) * bin_width
                fig.add_trace(go.Scatter(
                    x=x_range, y=y_norm, mode="lines",
                    name="Normal Curve", line=dict(color="darkorange", width=2.5),
                ))

            if eff_ll is not None:
                fig.add_vline(x=eff_ll, line_dash="dash", line_color="red", line_width=4)
                fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="red", width=3, dash="dash"), name=f"LL ({eff_ll})"))
            if eff_ul is not None:
                fig.add_vline(x=eff_ul, line_dash="dash", line_color="red", line_width=4)
                fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="red", width=3, dash="dash"), name=f"UL ({eff_ul})"))

            if mean_val is not None:
                fig.add_vline(x=mean_val, line_dash="dash", line_color="orange", line_width=4)
                fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="orange", width=3, dash="dash"), name=f"Mean ({mean_val:.3f})"))

            if mean_val is not None and std_val is not None:
                plus3 = mean_val + 3.0 * std_val
                minus3 = mean_val - 3.0 * std_val
                fig.add_vline(x=plus3, line_dash="dash", line_color="#333333", line_width=4)
                fig.add_vline(x=minus3, line_dash="dash", line_color="#333333", line_width=4)
                fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="#333333", width=3, dash="dash"), name=f"+3\u03c3 ({plus3:.3f})"))
                fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="#333333", width=3, dash="dash"), name=f"-3\u03c3 ({minus3:.3f})"))

            drag_mode = "select" if st.session_state[delete_mode_key] else "zoom"

            fig.update_layout(
                title=selected_param,
                xaxis_title=f"Result ({unit})",
                yaxis_title="Count",
                barmode="overlay",
                bargap=0.08,
                showlegend=True,
                legend=dict(x=1.02, y=1, xanchor="left", yanchor="top", font=dict(size=10)),
                dragmode=drag_mode,
                margin=dict(l=10, r=10, t=50, b=10),
            )

        else:  # Waveform
            if not csv_options:
                with plot_col:
                    st.warning("No CSV waveform files found in the selected ZIPs.")
            elif selected_csv:
                # Determine X-axis type based on CSV filename:
                # Z-parameters (ZDD, ZSS, ZSD, ZDS) are time-domain (seconds → ns)
                # S-parameters (S2_1, SDD, SCD, SCC, etc.) are frequency-domain (Hz → GHz)
                csv_base = selected_csv.replace(".csv", "").upper()
                is_time_domain = csv_base.startswith("Z")

                colors_palette = px.colors.qualitative.Plotly
                trace_count = 0
                for folder_str, rec in folders_sorted:
                    zpath = rec["zip_path"]
                    csv_list = list_csv_files_in_zip(zpath)
                    if selected_csv not in csv_list:
                        continue

                    try:
                        csv_df = read_csv_from_zip(zpath, selected_csv)
                    except Exception:
                        continue

                    if csv_df.empty or csv_df.shape[1] < 2:
                        continue

                    x_col = csv_df.columns[0]
                    x_data = pd.to_numeric(csv_df[x_col], errors="coerce")

                    # Convert X-axis based on domain type
                    if is_time_domain:
                        x_display = x_data * 1e9  # seconds → nanoseconds
                        x_unit = "ns"
                        hover_fmt = f"SN: {{sn}}<br>%{{x:.2f}} ns<br>%{{y:.2f}}<extra>{{y_col}}</extra>"
                    else:
                        x_display = x_data / 1e9  # Hz → GHz
                        x_unit = "GHz"
                        hover_fmt = f"SN: {{sn}}<br>%{{x:.2f}} GHz<br>%{{y:.2f}} dB<extra>{{y_col}}</extra>"

                    try:
                        _, summary_text = read_summary_text_from_zip(zpath)
                        sn = extract_sn_from_text(summary_text)
                    except Exception:
                        sn = zpath.stem[:20]

                    if sn in st.session_state.get(removed_sns_key, []):
                        continue

                    for y_col in csv_df.columns[1:]:
                        y_data = pd.to_numeric(csv_df[y_col], errors="coerce")
                        color = colors_palette[trace_count % len(colors_palette)]
                        fig.add_trace(go.Scatter(
                            x=x_display, y=y_data, mode="lines",
                            name=f"{sn} | {y_col}",
                            line=dict(color=color, width=1.5),
                            opacity=0.6,
                            hovertemplate=f"SN: {sn}<br>%{{x:.2f}} {x_unit}<br>%{{y:.2f}}<extra>{y_col}</extra>",
                        ))
                        trace_count += 1

                y_label = selected_csv.replace(".csv", "") if csv_options else "Y"
                x_label = "Time (ns)" if is_time_domain else "Frequency (GHz)"

                fig.update_layout(
                    title=f"Waveform: {selected_csv}",
                    xaxis_title=x_label,
                    yaxis_title=y_label,
                    margin=dict(l=10, r=10, t=50, b=10),
                    dragmode="zoom",
                    showlegend=True,
                    legend=dict(font=dict(size=9)),
                    hovermode="closest",
                )

        with plot_col:
            chart_event = st.plotly_chart(
                fig,
                use_container_width=True,
                config={"scrollZoom": True, "displaylogo": False},
                key=f"si_plot_{selected_param}_{plot_type}",
                on_select="rerun",
                selection_mode=("box", "lasso", "points"),
            )

            if delete_mode:
                selected_sns = extract_selected_sns(chart_event, filtered_plot, plot_type)

                if selected_sns:
                    newly_removed = 0
                    for sn in selected_sns:
                        if sn not in st.session_state[removed_sns_key]:
                            st.session_state[removed_sns_key].append(sn)
                            newly_removed += 1
                    if newly_removed > 0:
                        st.rerun()
                elif delete_mode:
                    st.caption("No points selected yet. Drag on the plot to select points.")



# ============================================================
# ETEST MODE
# ============================================================

elif test_mode == "ETest":
    st.caption(
        "Enter the path to a folder containing ETEST CSV files (from easywire cable tester). "
        "The app parses all CSVs, extracts WIRE/4WIRE measurements, and plots results."
    )

    etest_folder_path = st.text_input("Folder path (ETest CSVs)", value=r"", key="etest_folder_path")

    if etest_folder_path:
        ep = Path(etest_folder_path)

        if not ep.exists() or not ep.is_dir():
            st.error("Provided path does not exist or is not a directory.")
            st.stop()

        with st.spinner("Loading ETest CSV files..."):
            etest_df = etest_load_folder(str(ep), include_failed=True)

        if etest_df.empty:
            st.warning("No measurement data found in the CSV files in this folder.")
            st.stop()

        n_files = etest_df['File'].nunique()
        st.caption(
            f"Loaded {len(etest_df)} measurements from {n_files} files | "
            f"Parameters: {etest_df['Parameter'].nunique()} | "
            f"Units: {n_files}"
        )

        # Assign a sequence number per file/unit (for X axis)
        # Use File as the unique unit identifier since CableNumber can repeat across runs
        # Assign Unit# based on earliest file (by timestamp)
        file_order = etest_df.groupby("File")["Timestamp"].min().sort_values().index.tolist()
        if not file_order:
            file_order = sorted(etest_df["File"].dropna().unique().tolist())
        file_seq_map = {f: i + 1 for i, f in enumerate(file_order)}
        etest_df["Unit#"] = etest_df["File"].map(file_seq_map)
        etest_df["CableSeq"] = etest_df["Unit#"]  # alias for plotting

        # Download buttons
        folder_name = ep.name
        dl_c1, dl_c2, dl_c3 = st.columns([1, 1, 1], gap="small")

        # Long format (include Unit#)
        etest_long_csv = etest_df.to_csv(index=False).encode("utf-8")
        with dl_c1:
            st.download_button(
                label="Download CSV (Long)",
                data=etest_long_csv,
                file_name=f"{folder_name}_etest_long.csv",
                mime="text/csv",
                key="etest_dl_long",
            )

        # Wide format: one row per unit, columns = parameters (include Unit#)
        # Build a file-level lookup for Unit# and SN
        file_info = etest_df.groupby("File").agg(
            {"Unit#": "first", "CableNumber": "first", "SN": "first"}
        ).reset_index()
        etest_wide = etest_df.pivot_table(
            index="File",
            columns="Parameter",
            values="Value",
            aggfunc="first"
        ).reset_index()
        etest_wide = etest_wide.merge(file_info[["File", "Unit#", "CableNumber", "SN"]], on="File", how="left")
        # Reorder columns: Unit#, CableNumber, SN first, then parameters
        param_cols = [c for c in etest_wide.columns if c not in ["File", "Unit#", "CableNumber", "SN"]]
        etest_wide = etest_wide[["Unit#", "CableNumber", "SN"] + sorted(param_cols)]
        etest_wide = etest_wide.sort_values("Unit#").reset_index(drop=True)
        etest_wide_csv = etest_wide.to_csv(index=False).encode("utf-8")
        with dl_c2:
            st.download_button(
                label="Download CSV (Wide)",
                data=etest_wide_csv,
                file_name=f"{folder_name}_etest_wide.csv",
                mime="text/csv",
                key="etest_dl_wide",
            )

        # Summary format
        etest_parameters = sorted(etest_df["Parameter"].unique())
        etest_summary_df = build_summary_wide(
            etest_df, etest_parameters, "Value", "Unit", "LowerLimit", "UpperLimit"
        )
        etest_summary_csv = etest_summary_df.to_csv(index=False).encode("utf-8")
        with dl_c3:
            st.download_button(
                label="Download Summary",
                data=etest_summary_csv,
                file_name=f"{folder_name}_etest_summary.csv",
                mime="text/csv",
                key="etest_dl_summary",
            )

        # Parameter / plot selection
        st.divider()
        esel_col, etype_col = st.columns([4.2, 1.8], gap="small")
        with esel_col:
            etest_param = st.selectbox("Parameter", etest_parameters, key="etest_param")
        with etype_col:
            etest_plot_type = st.radio("Plot", ["Scatter Plot", "Histogram"], horizontal=True, key="etest_plot_type")

        # Filter to selected parameter
        etest_filtered = etest_df[etest_df["Parameter"] == etest_param].copy()
        etest_unit = etest_filtered["Unit"].iloc[0] if not etest_filtered.empty else ""

        # Get limits
        etest_ll_series = pd.to_numeric(etest_filtered["LowerLimit"], errors="coerce").dropna()
        etest_ul_series = pd.to_numeric(etest_filtered["UpperLimit"], errors="coerce").dropna()
        etest_orig_ll = float(etest_ll_series.iloc[0]) if not etest_ll_series.empty else None
        etest_orig_ul = float(etest_ul_series.iloc[0]) if not etest_ul_series.empty else None

        # Initialize removed SNs
        etest_removed_key = f"etest_removed_sns_{etest_param}"
        if etest_removed_key not in st.session_state:
            st.session_state[etest_removed_key] = []

        # Custom limits state — initialize keys if not set
        etest_ll_key = f"etest_new_ll_{etest_param}"
        etest_ul_key = f"etest_new_ul_{etest_param}"
        if etest_ll_key not in st.session_state:
            st.session_state[etest_ll_key] = "" if etest_orig_ll is None else str(etest_orig_ll)
        if etest_ul_key not in st.session_state:
            st.session_state[etest_ul_key] = "" if etest_orig_ul is None else str(etest_orig_ul)

        etest_new_ll_str = st.session_state[etest_ll_key]
        etest_new_ul_str = st.session_state[etest_ul_key]
        etest_use_new_limits = st.session_state.get(f"etest_use_new_limits_{etest_param}", False)

        etest_new_ll_val = extract_first_number(etest_new_ll_str) if str(etest_new_ll_str).strip() != "" else None
        etest_new_ul_val = extract_first_number(etest_new_ul_str) if str(etest_new_ul_str).strip() != "" else None
        etest_eff_ll = etest_new_ll_val if etest_use_new_limits else etest_orig_ll
        etest_eff_ul = etest_new_ul_val if etest_use_new_limits else etest_orig_ul

        # Apply removed SNs
        etest_filtered_plot = etest_filtered[
            ~etest_filtered["SN"].isin(st.session_state[etest_removed_key])
        ].copy()

        # Plot + right panel
        eplot_col, eright_col = st.columns([4.3, 1.7], gap="small")

        with eright_col:
            st.subheader("Custom Limits")
            etest_use_new_limits = st.toggle(
                "Use custom limits",
                value=etest_use_new_limits,
                key=f"etest_use_new_limits_{etest_param}",
            )

            # Reset text boxes to originals when custom limits is disabled
            if not etest_use_new_limits:
                st.session_state[etest_ll_key] = "" if etest_orig_ll is None else str(etest_orig_ll)
                st.session_state[etest_ul_key] = "" if etest_orig_ul is None else str(etest_orig_ul)

            etest_new_ll_str = st.text_input(
                "LL (blank = none)",
                key=f"etest_new_ll_{etest_param}",
                disabled=not etest_use_new_limits,
            )
            etest_new_ul_str = st.text_input(
                "UL (blank = none)",
                key=f"etest_new_ul_{etest_param}",
                disabled=not etest_use_new_limits,
            )

            etest_new_ll_val = extract_first_number(etest_new_ll_str) if str(etest_new_ll_str).strip() != "" else None
            etest_new_ul_val = extract_first_number(etest_new_ul_str) if str(etest_new_ul_str).strip() != "" else None
            etest_eff_ll = etest_new_ll_val if etest_use_new_limits else etest_orig_ll
            etest_eff_ul = etest_new_ul_val if etest_use_new_limits else etest_orig_ul

            # Statistics
            etest_vals = pd.to_numeric(etest_filtered_plot["Value"], errors="coerce").dropna()
            etest_n = int(etest_vals.shape[0])
            etest_mean = float(etest_vals.mean()) if etest_n > 0 else None
            etest_std = float(etest_vals.std(ddof=1)) if etest_n >= 2 else None
            etest_min = float(etest_vals.min()) if etest_n > 0 else None
            etest_max = float(etest_vals.max()) if etest_n > 0 else None
            etest_range = (etest_max - etest_min) if (etest_min is not None and etest_max is not None) else None

            total_n, pass_n, fail_n, yield_pct = compute_yield(
                etest_filtered_plot, etest_eff_ll, etest_eff_ul, value_col="Value"
            )
            cpk_val = compute_cpk(etest_filtered_plot, etest_eff_ll, etest_eff_ul, value_col="Value")

            cpk_txt = "N/A" if cpk_val is None else f"{cpk_val:.3f}"
            mean_txt = "N/A" if etest_mean is None else f"{etest_mean:.4f}"
            std_txt = "N/A" if etest_std is None else f"{etest_std:.4f}"
            min_txt = "N/A" if etest_min is None else f"{etest_min:.4f}"
            max_txt = "N/A" if etest_max is None else f"{etest_max:.4f}"
            range_txt = "N/A" if etest_range is None else f"{etest_range:.4f}"
            ll_disp = "None" if etest_eff_ll is None else f"{etest_eff_ll}"
            ul_disp = "None" if etest_eff_ul is None else f"{etest_eff_ul}"

            st.markdown(
                f"""
                <div style='font-size:14px; line-height:1.6;'>
                <b>Count:</b> {etest_n}<br>
                <b>Mean:</b> {mean_txt} {etest_unit}<br>
                <b>Std Dev:</b> {std_txt}<br>
                <b>Min:</b> {min_txt}<br>
                <b>Max:</b> {max_txt}<br>
                <b>Range:</b> {range_txt}<br>
                <b>Lower Limit:</b> {ll_disp}<br>
                <b>Upper Limit:</b> {ul_disp} {etest_unit}<br>
                <b>Yield:</b> {yield_pct:.2f}% ({pass_n}/{total_n})<br>
                <b>Cpk:</b> {cpk_txt}
                </div>
                """,
                unsafe_allow_html=True,
            )

        # Delete mode for ETest
        st.divider()

        etest_delete_mode_key = f"etest_delete_mode_{etest_param}"
        if etest_delete_mode_key not in st.session_state:
            st.session_state[etest_delete_mode_key] = False

        ecol1, ecol2, ecol3 = st.columns([1, 2, 2])

        with ecol1:
            etest_delete_mode = st.toggle(
                "Delete Mode",
                value=st.session_state[etest_delete_mode_key],
                key=f"etest_toggle_delete_{etest_param}"
            )
            st.session_state[etest_delete_mode_key] = etest_delete_mode

        with ecol2:
            if etest_delete_mode:
                st.caption("Delete mode: drag-select points to remove.")

        with ecol3:
            if st.session_state[etest_removed_key]:
                if st.button("Restore All", key=f"etest_btn_restore_all_{etest_param}"):
                    st.session_state[etest_removed_key] = []
                    st.rerun()

        if etest_delete_mode:
            st.caption("Delete mode ON")

        if st.session_state[etest_removed_key]:
            st.caption(f"Removed points: {', '.join(st.session_state[etest_removed_key])}")

        # Build the plot
        efig = go.Figure()

        if etest_plot_type == "Scatter Plot":
            efig.add_trace(go.Scatter(
                x=etest_filtered_plot["CableSeq"],
                y=etest_filtered_plot["Value"],
                mode="markers",
                marker=dict(color="royalblue", size=8),
                text=etest_filtered_plot["SN"],
                customdata=etest_filtered_plot["SN"],
                hovertemplate="Cable #%{x}<br>SN: %{text}<br>Value: %{y}<extra></extra>",
                name="Measurement"
            ))

            # Mean line
            if etest_mean is not None:
                efig.add_hline(y=etest_mean, line_dash="dash", line_color="orange", line_width=3)
                efig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="orange", width=3, dash="dash"), name=f"Mean ({etest_mean:.3f})"))

            # +/- 3 sigma lines
            if etest_mean is not None and etest_std is not None:
                plus3 = etest_mean + 3.0 * etest_std
                minus3 = etest_mean - 3.0 * etest_std
                efig.add_hline(y=plus3, line_dash="dash", line_color="#333333", line_width=3)
                efig.add_hline(y=minus3, line_dash="dash", line_color="#333333", line_width=3)
                efig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="#333333", width=3, dash="dash"), name=f"+3\u03c3 ({plus3:.3f})"))
                efig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="#333333", width=3, dash="dash"), name=f"-3\u03c3 ({minus3:.3f})"))

            # Limit lines
            if etest_eff_ll is not None:
                efig.add_hline(y=etest_eff_ll, line_dash="dash", line_color="red", line_width=4)
                efig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="red", width=3, dash="dash"), name=f"LL ({etest_eff_ll})"))
            if etest_eff_ul is not None:
                efig.add_hline(y=etest_eff_ul, line_dash="dash", line_color="red", line_width=4)
                efig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="red", width=3, dash="dash"), name=f"UL ({etest_eff_ul})"))

            etest_drag_mode = "select" if st.session_state[etest_delete_mode_key] else "zoom"

            efig.update_layout(
                title=f"{etest_param} ({etest_unit})",
                xaxis_title="Cable Sequence",
                yaxis_title=f"Value ({etest_unit})",
                showlegend=True,
                legend=dict(x=1.02, y=1, xanchor="left", yanchor="top", font=dict(size=10)),
                dragmode=etest_drag_mode,
                margin=dict(l=10, r=10, t=50, b=10),
            )

        elif etest_plot_type == "Histogram":
            nbins = auto_histogram_bins(etest_vals)

            efig.add_trace(go.Histogram(
                x=etest_vals,
                customdata=etest_filtered_plot.loc[etest_vals.index, "SN"].astype(str),
                name="Measurements",
                marker_color="royalblue",
                opacity=0.65,
                nbinsx=nbins,
            ))

            # Normal curve overlay
            if etest_mean is not None and etest_std is not None and etest_n > 1:
                x_range = np.linspace(
                    etest_mean - 4 * etest_std,
                    etest_mean + 4 * etest_std,
                    200
                )
                # Scale the normal PDF to match histogram counts
                bin_width = (etest_vals.max() - etest_vals.min()) / nbins if nbins > 0 else 1
                y_norm = norm.pdf(x_range, etest_mean, etest_std) * etest_n * bin_width

                efig.add_trace(go.Scatter(
                    x=x_range,
                    y=y_norm,
                    mode="lines",
                    name="Normal Curve",
                    line=dict(color="darkorange", width=2.5),
                ))

            # Limit lines
            if etest_eff_ll is not None:
                efig.add_vline(x=etest_eff_ll, line_dash="dash", line_color="red", line_width=4)
                efig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="red", width=3, dash="dash"), name=f"LL ({etest_eff_ll})"))
            if etest_eff_ul is not None:
                efig.add_vline(x=etest_eff_ul, line_dash="dash", line_color="red", line_width=4)
                efig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="red", width=3, dash="dash"), name=f"UL ({etest_eff_ul})"))

            # Mean line
            if etest_mean is not None:
                efig.add_vline(x=etest_mean, line_dash="dash", line_color="orange", line_width=3)
                efig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="orange", width=3, dash="dash"), name=f"Mean ({etest_mean:.3f})"))

            # +/- 3 sigma
            if etest_mean is not None and etest_std is not None:
                plus3 = etest_mean + 3.0 * etest_std
                minus3 = etest_mean - 3.0 * etest_std
                efig.add_vline(x=plus3, line_dash="dash", line_color="#333333", line_width=3)
                efig.add_vline(x=minus3, line_dash="dash", line_color="#333333", line_width=3)
                efig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="#333333", width=3, dash="dash"), name=f"+3σ ({plus3:.3f})"))
                efig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                    line=dict(color="#333333", width=3, dash="dash"), name=f"-3σ ({minus3:.3f})"))

            etest_drag_mode = "select" if st.session_state[etest_delete_mode_key] else "zoom"

            efig.update_layout(
                title=f"{etest_param} ({etest_unit}) — Histogram",
                xaxis_title=f"Value ({etest_unit})",
                yaxis_title="Count",
                barmode="overlay",
                bargap=0.08,
                showlegend=True,
                legend=dict(x=1.02, y=1, xanchor="left", yanchor="top", font=dict(size=10)),
                dragmode=etest_drag_mode,
                margin=dict(l=10, r=10, t=50, b=10),
            )

        with eplot_col:
            echart_event = st.plotly_chart(
                efig,
                use_container_width=True,
                config={"scrollZoom": True, "displaylogo": False},
                key=f"etest_plot_{etest_param}_{etest_plot_type}",
                on_select="rerun",
                selection_mode=("box", "lasso", "points"),
            )

            if etest_delete_mode:
                etest_selected_sns = extract_selected_sns(
                    echart_event, etest_filtered_plot, etest_plot_type,
                    sn_col="SN", value_col="Value"
                )

                if etest_selected_sns:
                    newly_removed = 0
                    for sn in etest_selected_sns:
                        if sn not in st.session_state[etest_removed_key]:
                            st.session_state[etest_removed_key].append(sn)
                            newly_removed += 1
                    if newly_removed > 0:
                        st.rerun()
                elif etest_delete_mode:
                    st.caption("No points selected yet. Drag on the plot to select points.")

        st.caption(
        )
