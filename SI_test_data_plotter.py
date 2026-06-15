import os
import re
import zipfile
from pathlib import Path
from datetime import datetime
from functools import lru_cache

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# ============================================================
# Helpers
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

    for line in text.splitlines():
        if stop_marker.lower() in line.lower():
            break
        if ":" not in line:
            continue

        parts = [p.strip() for p in line.split(":")]
        if len(parts) < 9:
            continue

        try:
            parameter = f"{parts[0]} | {parts[1]} | {parts[2]} | {parts[3]} | {parts[6]}"
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

    return pd.DataFrame(
        data,
        columns=["SN", "Parameter", "Lower Limit", "Result", "Upper Limit", "Pass/Fail", "Unit", "Source"]
    )


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
    """
    Your original DUT folder detection.
    """
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


def compute_yield(df_in: pd.DataFrame, ll, ul):
    vals = pd.to_numeric(df_in["Result"], errors="coerce").dropna()
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


def compute_cpk(df_in: pd.DataFrame, ll, ul):
    """
    Compute Cpk using effective LL/UL and result values.
    Returns None when Cpk is not computable (missing limits, too few points, zero sigma).
    """
    if ll is None or ul is None:
        return None

    vals = pd.to_numeric(df_in["Result"], errors="coerce").dropna()
    if vals.shape[0] < 2:
        return None

    mean = float(vals.mean())
    sigma = float(vals.std(ddof=1))
    if sigma <= 0:
        return None

    cpu = (ul - mean) / (3.0 * sigma)
    cpl = (mean - ll) / (3.0 * sigma)
    return min(cpu, cpl)


def compute_mean_std_min_max(df_in: pd.DataFrame):
    """
    Compute mean, sample standard deviation, min, and max of Result values.
    Returns (mean, std, min, max) where std can be None if not enough points.
    """
    vals = pd.to_numeric(df_in["Result"], errors="coerce").dropna()
    if vals.empty:
        return None, None, None, None

    mean = float(vals.mean())
    std = float(vals.std(ddof=1)) if vals.shape[0] >= 2 else None
    min_val = float(vals.min())
    max_val = float(vals.max())
    return mean, std, min_val, max_val


def auto_histogram_bins(values: pd.Series, min_bins: int = 8, max_bins: int = 60) -> int:
    """
    Compute a data-driven histogram bin count.
    Uses Freedman-Diaconis when possible, with Sturges fallback.
    """
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
        # Freedman-Diaconis bin width
        bin_width = 2.0 * iqr / (n ** (1.0 / 3.0))
        if bin_width > 0:
            bins = int((vrange / bin_width) + 0.999999)
        else:
            bins = int((n.bit_length())) + 1
    else:
        # Sturges fallback for low-variance data
        bins = int((n.bit_length())) + 1

    bins = max(min_bins, min(max_bins, bins))
    return bins


def extract_selected_point_indices(selection_event):
    """
    Normalize Streamlit plotly selection payload and return selected point indices.
    """
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


def extract_selected_sns(selection_event, filtered_plot: pd.DataFrame, plot_type: str):
    """
    Extract selected SNs from plot selection for both scatter and histogram.
    """
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

    # Scatter usually returns point indices that map directly to filtered_plot rows.
    if plot_type == "Scatter Plot" and selected_indices:
        for idx in selected_indices:
            if 0 <= idx < len(filtered_plot):
                selected_sns.add(str(filtered_plot.iloc[idx]["SN"]))

    # Histogram selection returns bins/points in x-domain; use selected x span.
    if plot_type == "Histogram" and selected_x:
        x_min = min(selected_x)
        x_max = max(selected_x)
        vals = pd.to_numeric(filtered_plot["Result"], errors="coerce")
        mask = vals.between(x_min, x_max, inclusive="both")
        selected_sns.update(filtered_plot.loc[mask, "SN"].astype(str).tolist())

    return sorted(selected_sns)


# ============================================================
# Fast network scan: latest zip per DUT folder
# ============================================================

def select_latest_zip_per_dut_fast(root: Path, progress_every: int = 2000):
    """
    Walk with os.scandir (fast on SMB), selecting latest zip per DUT folder by mtime.
    Returns dict: dut_folder_str -> {"zip_path": Path, "mtime": float}
    """
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

                    # Avoid following symlinks/reparse points on corp images
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
            # network share hiccups / permission-limited folders
            continue

    status.empty()
    return best, {"scanned_entries": scanned_entries, "found_zips": found_zips, "dut_folders": len(best)}


@st.cache_data(ttl=300, show_spinner=False)
def cached_latest_zip_selection(root_str: str):
    root = Path(root_str)
    # NOTE: st.* calls cannot be inside cached functions, so we do not call st here.
    # We'll do a non-UI scan here using the same algorithm but without status updates.
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
# Streamlit App
# ============================================================

st.set_page_config(layout="wide", initial_sidebar_state="collapsed")
st.title("Test Data Plot")

compact_mode = st.toggle("Compact UI", value=True)
if compact_mode:
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

st.caption(
    "Enter a folder path (local or UNC). The app scans for ZIPs, selects the **latest ZIP per DUT folder** "
    "(by ZIP modified time), then reads **summary.txt** from those selected ZIPs and plots results."
)

ctrl_path, ctrl_cache, ctrl_details = st.columns([6, 1.5, 1.5], gap="small")
with ctrl_path:
    folder_path = st.text_input("Folder path", value=r"")
with ctrl_cache:
    use_cache = st.checkbox("Cache", value=True)
with ctrl_details:
    show_scan_details = st.checkbox("Details", value=False)

if folder_path:
    p = Path(folder_path)

    if not p.exists() or not p.is_dir():
        st.error("Provided path does not exist or is not a directory. Please enter a valid folder path.")
        st.stop()

    st.caption(f"Scanning under: {p}")

    # 1) Scan + select latest zip per DUT folder
    if use_cache:
        with st.spinner("Scanning (cached)..."):
            best_zip_by_folder, meta = cached_latest_zip_selection(str(p))
    else:
        with st.spinner("Scanning (live)..."):
            best_zip_by_folder, meta = select_latest_zip_per_dut_fast(p)

    st.caption(
        f"Scan done. Entries scanned: {meta['scanned_entries']:,} | "
        f"ZIPs found: {meta['found_zips']:,} | "
        f"DUT folders selected: {meta['dut_folders']:,}"
    )

    if not best_zip_by_folder:
        st.warning("No zip files found.")
        st.stop()

    # 2) Read summary.txt from selected zips only
    st.caption("Reading summary.txt from selected latest ZIPs...")

    all_dfs = []
    selection_rows = []

    progress = st.progress(0)
    folders_sorted = sorted(best_zip_by_folder.items(), key=lambda kv: kv[0])
    n_sel = len(folders_sorted)

    for i, (folder_str, rec) in enumerate(folders_sorted, start=1):
        zpath = rec["zip_path"]
        mtime_str = datetime.fromtimestamp(rec["mtime"]).strftime("%Y-%m-%d %H:%M:%S")

        if i % 5 == 0 or i == n_sel:
            progress.progress(i / n_sel)

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

    progress.empty()

    if show_scan_details:
        with st.expander("Selected latest zip per DUT folder"):
            st.dataframe(pd.DataFrame(selection_rows), use_container_width=True)

    if not all_dfs:
        st.warning("No summary.txt content was successfully parsed from the selected ZIP files.")
        st.stop()

    # 3) Combine
    df = pd.concat(all_dfs, ignore_index=True)

    # 4) Unit Number per SN (1-based)
    sn_list = df["SN"].unique()
    sn_to_row = {sn: i for i, sn in enumerate(sn_list, start=1)}
    df["Unit Number"] = df["SN"].map(sn_to_row)

    # Download combined data as CSV
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download CSV",
        data=csv_bytes,
        file_name="combined_summary_data.csv",
        mime="text/csv",
        use_container_width=False,
    )

    # 5) Parameter / plot controls
    st.divider()
    sel_col, type_col = st.columns([4.2, 1.8], gap="small")
    with sel_col:
        selected_param = st.selectbox("Parameter", df["Parameter"].unique())
    with type_col:
        plot_type = st.radio("Plot", ["Scatter Plot", "Histogram"], horizontal=True)

    filtered = df[df["Parameter"] == selected_param].copy()
    unit = filtered["Unit"].iloc[0] if not filtered.empty else ""

    # Original limits from data
    orig_ll_series = pd.to_numeric(filtered["Lower Limit"], errors="coerce").dropna()
    orig_ul_series = pd.to_numeric(filtered["Upper Limit"], errors="coerce").dropna()
    orig_ll = float(orig_ll_series.iloc[0]) if not orig_ll_series.empty else None
    orig_ul = float(orig_ul_series.iloc[0]) if not orig_ul_series.empty else None

    # Initialize removed SNs in session state
    removed_sns_key = f"removed_sns_{selected_param}"
    if removed_sns_key not in st.session_state:
        st.session_state[removed_sns_key] = []

    # Defaults for per-parameter state
    default_new_ll = "" if orig_ll is None else str(orig_ll)
    default_new_ul = "" if orig_ul is None else str(orig_ul)

    new_ll_str = st.session_state.get(f"new_ll_{selected_param}", default_new_ll)
    new_ul_str = st.session_state.get(f"new_ul_{selected_param}", default_new_ul)
    use_new_limits = st.session_state.get(f"use_new_limits_{selected_param}", False)

    new_ll_val = extract_first_number(new_ll_str) if str(new_ll_str).strip() != "" else None
    new_ul_val = extract_first_number(new_ul_str) if str(new_ul_str).strip() != "" else None
    eff_ll = new_ll_val if use_new_limits else orig_ll
    eff_ul = new_ul_val if use_new_limits else orig_ul

    # Apply removed SNs filter
    all_sns = filtered["SN"].unique().tolist()
    filtered_plot = filtered[~filtered["SN"].isin(st.session_state[removed_sns_key])].copy()

    # Plot + right-side controls panel
    plot_col, right_col = st.columns([4.3, 1.7], gap="small")
    with right_col:
        st.subheader("Custom Limits")
        use_new_limits = st.toggle(
            "Use custom limits",
            value=use_new_limits,
            key=f"use_new_limits_{selected_param}",
        )
        new_ll_str = st.text_input(
            "LL (blank = none)",
            value=new_ll_str,
            key=f"new_ll_{selected_param}",
        )
        new_ul_str = st.text_input(
            "UL (blank = none)",
            value=new_ul_str,
            key=f"new_ul_{selected_param}",
        )

        new_ll_val = extract_first_number(new_ll_str) if str(new_ll_str).strip() != "" else None
        new_ul_val = extract_first_number(new_ul_str) if str(new_ul_str).strip() != "" else None
        eff_ll = new_ll_val if use_new_limits else orig_ll
        eff_ul = new_ul_val if use_new_limits else orig_ul

        total_n, pass_n, fail_n, yield_pct = compute_yield(filtered_plot, eff_ll, eff_ul)
        cpk_val = compute_cpk(filtered_plot, eff_ll, eff_ul)
        mean_val, std_val, min_val, max_val = compute_mean_std_min_max(filtered_plot)
        cpk_txt = "N/A" if cpk_val is None else f"{cpk_val:.3f}"
        mean_txt = "N/A" if mean_val is None else f"{mean_val:.3f}"
        std_txt = "N/A" if std_val is None else f"{std_val:.3f}"
        min_txt = "N/A" if min_val is None else f"{min_val:.3f}"
        max_txt = "N/A" if max_val is None else f"{max_val:.3f}"
        st.markdown(
            (
                "<div style='font-size:20px; font-weight:700; color:#000000; line-height:1.2;'>"
                f"Yield {yield_pct:.2f}% | Units {total_n} | Pass {pass_n} | Fail {fail_n} | "
                f"Mean {mean_txt} | Std Dev {std_txt} | Min {min_txt} | Max {max_txt} | Cpk {cpk_txt}"
                "</div>"
            ),
            unsafe_allow_html=True,
        )

    # Show data table with selection and delete capability
    st.divider()
    
    delete_mode_key = f"delete_mode_{selected_param}"
    if delete_mode_key not in st.session_state:
        st.session_state[delete_mode_key] = False
    
    col1, col2, col3 = st.columns([1, 2, 2])
    
    with col1:
        delete_mode = st.toggle(
            "Delete Mode",
            value=st.session_state[delete_mode_key],
            key=f"toggle_delete_{selected_param}"
        )
        st.session_state[delete_mode_key] = delete_mode
    
    with col2:
        if delete_mode:
            st.caption("Delete mode: drag-select points to remove.")
    
    with col3:
        if st.session_state[removed_sns_key]:
            if st.button("↩️ Restore All", key=f"btn_restore_all_{selected_param}"):
                st.session_state[removed_sns_key] = []
                st.rerun()
    
    # Delete mode interface
    if delete_mode:
        st.caption("Delete mode ON")
    
    # Show removed SNs status
    if st.session_state[removed_sns_key]:
        st.caption(f"🗑️ Removed points: {', '.join(st.session_state[removed_sns_key])}")

    ll_txt = "—" if eff_ll is None else eff_ll
    ul_txt = "—" if eff_ul is None else eff_ul
    limit_legend_text = f"Limits used: LL={ll_txt}, UL={ul_txt}"

    mapping = filtered_plot[["Unit Number", "SN"]].drop_duplicates().sort_values("Unit Number")

    color_map = {"PASS": "green", "FAIL": "red"}
    point_colors = filtered_plot["Pass/Fail"].map(color_map).fillna("blue")

    fig = go.Figure()

    if plot_type == "Scatter Plot":
        fig.add_trace(go.Scatter(
            x=filtered_plot["Unit Number"],
            y=filtered_plot["Result"],
            mode="markers",
            marker=dict(color=point_colors, size=9),
            text=filtered_plot["SN"],
            customdata=filtered_plot["SN"],
            hovertemplate="Unit %{x}<br>SN %{text}<br>Result %{y}<extra></extra>",
            name="Result"
        ))

        if eff_ll is not None:
            fig.add_hline(y=eff_ll, line_dash="dot", line_color="red", line_width=2, annotation_text="LL", annotation_position="bottom left")
        if eff_ul is not None:
            fig.add_hline(y=eff_ul, line_dash="dot", line_color="red", line_width=2, annotation_text="UL", annotation_position="top left")

        if mean_val is not None:
            fig.add_hline(y=mean_val, line_dash="dash", line_color="orange", line_width=2, annotation_text="Mean", annotation_position="top right")

        if mean_val is not None and std_val is not None:
            plus3 = mean_val + 3.0 * std_val
            minus3 = mean_val - 3.0 * std_val
            fig.add_hline(y=plus3, line_dash="dash", line_color="#333333", line_width=2, annotation_text="+3σ", annotation_position="top right")
            fig.add_hline(y=minus3, line_dash="dash", line_color="#333333", line_width=2, annotation_text="-3σ", annotation_position="bottom right")

        # Build reduced x-axis ticks so labels remain readable.
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
            annotations=[
                dict(
                    x=0.01,
                    y=0.99,
                    xref="paper",
                    yref="paper",
                    xanchor="left",
                    yanchor="top",
                    text=limit_legend_text,
                    showarrow=False,
                    bgcolor="rgba(255,255,255,0.7)",
                    bordercolor="#999",
                    borderwidth=1,
                    font=dict(size=11),
                )
            ],
            dragmode=drag_mode,
            margin=dict(l=10, r=10, t=50, b=10),
        )

    else:  # Histogram
        # Separate data by PASS/FAIL
        pass_df = filtered_plot[filtered_plot["Pass/Fail"] == "PASS"].copy()
        fail_df = filtered_plot[filtered_plot["Pass/Fail"] == "FAIL"].copy()
        other_df = filtered_plot[~filtered_plot["Pass/Fail"].isin(["PASS", "FAIL"])].copy()

        pass_data = pd.to_numeric(pass_df["Result"], errors="coerce").dropna()
        fail_data = pd.to_numeric(fail_df["Result"], errors="coerce").dropna()
        other_data = pd.to_numeric(other_df["Result"], errors="coerce").dropna()

        # Adaptive binning for clearer distribution shape
        all_results = pd.concat([pass_data, fail_data, other_data])
        nbins = auto_histogram_bins(all_results)

        if not pass_data.empty:
            fig.add_trace(go.Histogram(
                x=pass_data,
                customdata=pass_df.loc[pass_data.index, "SN"].astype(str),
                name="PASS",
                marker_color="green",
                opacity=0.55,
                nbinsx=nbins
            ))
        if not fail_data.empty:
            fig.add_trace(go.Histogram(
                x=fail_data,
                customdata=fail_df.loc[fail_data.index, "SN"].astype(str),
                name="FAIL",
                marker_color="red",
                opacity=0.55,
                nbinsx=nbins
            ))
        if not other_data.empty:
            fig.add_trace(go.Histogram(
                x=other_data,
                customdata=other_df.loc[other_data.index, "SN"].astype(str),
                name="Other",
                marker_color="blue",
                opacity=0.55,
                nbinsx=nbins
            ))

        if eff_ll is not None:
            fig.add_vline(x=eff_ll, line_dash="dot", line_color="red", line_width=2, annotation_text="LL", annotation_position="top left")
        if eff_ul is not None:
            fig.add_vline(x=eff_ul, line_dash="dot", line_color="red", line_width=2, annotation_text="UL", annotation_position="top left")

        if mean_val is not None:
            fig.add_vline(x=mean_val, line_dash="dash", line_color="orange", line_width=2, annotation_text="Mean", annotation_position="top right")

        if mean_val is not None and std_val is not None:
            plus3 = mean_val + 3.0 * std_val
            minus3 = mean_val - 3.0 * std_val
            fig.add_vline(x=plus3, line_dash="dash", line_color="#333333", line_width=2, annotation_text="+3σ", annotation_position="top right")
            fig.add_vline(x=minus3, line_dash="dash", line_color="#333333", line_width=2, annotation_text="-3σ", annotation_position="top right")

        drag_mode = "select" if st.session_state[delete_mode_key] else "zoom"

        fig.update_layout(
            title=selected_param,
            xaxis_title=f"Result ({unit})",
            yaxis_title="Count",
            barmode="overlay",
            bargap=0.08,
            annotations=[
                dict(
                    x=0.01,
                    y=0.99,
                    xref="paper",
                    yref="paper",
                    xanchor="left",
                    yanchor="top",
                    text=limit_legend_text,
                    showarrow=False,
                    bgcolor="rgba(255,255,255,0.7)",
                    bordercolor="#999",
                    borderwidth=1,
                    font=dict(size=11),
                )
            ],
            dragmode=drag_mode,
            margin=dict(l=10, r=10, t=50, b=10),
        )

    with plot_col:
        chart_event = st.plotly_chart(
            fig,
            use_container_width=True,
            config={"scrollZoom": True, "displaylogo": False},
            key=f"plot_{selected_param}_{plot_type}",
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

    st.caption("Note: Upper/lower limit lines are shown in red, mean is orange, and ±3σ lines are shown in dark grey.")