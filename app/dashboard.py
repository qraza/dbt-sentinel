"""dbt-sentinel dashboard — browse recorded runs, failures, and their history.

Reads dbt-sentinel's own history database (written by `sentinel analyze`), so it
needs no warehouse connection and no API key: everything shown here was already
computed and stored by a previous run.

    uv run --group ui streamlit run app/dashboard.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from dbt_sentinel.store import DEFAULT_HISTORY_PATH, connect

st.set_page_config(page_title="dbt-sentinel", layout="wide")
st.title("dbt-sentinel")
st.caption("Recorded dbt test failures, their diagnoses, and how they trend over time.")

history_path = st.sidebar.text_input("History database", str(DEFAULT_HISTORY_PATH))

if not Path(history_path).is_file():
    st.warning(
        f"No history database at `{history_path}`.\n\n"
        "Run `sentinel analyze ...` at least once to record a run."
    )
    st.stop()

con = connect(history_path)

runs = con.execute(
    "select run_id, run_at, project from runs order by run_at desc"
).fetchdf()

if runs.empty:
    st.info("The history database exists but has no runs recorded yet.")
    st.stop()

# --- summary -------------------------------------------------------------

failures = con.execute(
    """
    select r.run_id, r.run_at, t.unique_id, t.test_name, t.status,
           t.failure_count, t.confidence, t.root_cause
    from test_results t
    join runs r using (run_id)
    order by r.run_at desc
    """
).fetchdf()

c1, c2, c3 = st.columns(3)
c1.metric("Runs recorded", len(runs))
c2.metric("Distinct failing tests", failures["unique_id"].nunique())
c3.metric("Failures in latest run", int((failures["run_id"] == runs.iloc[0]["run_id"]).sum()))

# --- latest run ----------------------------------------------------------

st.subheader("Latest run")
latest_id = runs.iloc[0]["run_id"]
latest = failures[failures["run_id"] == latest_id]

if latest.empty:
    st.success("No failures in the most recent run.")
else:
    st.dataframe(
        latest[["test_name", "status", "failure_count", "confidence"]],
        hide_index=True,
        use_container_width=True,
    )

# --- per-test history ----------------------------------------------------

st.subheader("Test history")
options = sorted(failures["unique_id"].unique())
selected = st.selectbox("Test", options)

test_hist = failures[failures["unique_id"] == selected].sort_values("run_at")

if not test_hist.empty:
    chart_df = test_hist.copy()
    chart_df["run"] = chart_df["run_at"].dt.strftime("%m-%d %H:%M")
    chart_df = chart_df.set_index("run")[["failure_count"]]
    st.line_chart(chart_df, height=220)

    latest_row = test_hist.iloc[-1]
    st.markdown(f"**Latest confidence:** {latest_row['confidence']}")
    if latest_row["root_cause"]:
        st.markdown("**Most recent root cause**")
        st.info(latest_row["root_cause"])

    with st.expander("All recorded runs for this test"):
        st.dataframe(
            test_hist[["run_at", "status", "failure_count", "confidence"]],
            hide_index=True,
            use_container_width=True,
        )