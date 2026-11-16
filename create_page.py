#!/usr/bin/env python
"""Build gaslimit.pics (index.html + entities/index.html + assets/*.json).

Data comes straight from xatu's ClickHouse over HTTP (no pyxatu).
Credentials are read from .pyxatu_config.json (gitignored), or from the path
in $XATU_CONFIG.
"""
import json
import os
from datetime import timedelta
from io import StringIO

import pandas as pd
import plotly.graph_objects as go
import requests

os.chdir(os.path.dirname(os.path.abspath(__file__)))

CFG = json.load(open(os.environ.get("XATU_CONFIG", ".pyxatu_config.json")))
ENDPOINT, AUTH = CFG["CLICKHOUSE_URL"], (CFG["CLICKHOUSE_USER"], CFG["CLICKHOUSE_PASSWORD"])


def query(sql):
    r = requests.post(ENDPOINT, auth=AUTH, data=sql + "\nFORMAT TSVWithNames",
                      params={"max_execution_time": 300}, timeout=300)
    if not r.ok:
        raise RuntimeError(f"ClickHouse {r.status_code}: {r.text[:500]}")
    return pd.read_csv(StringIO(r.text), sep="\t")


color_palette = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2",
    "#7f7f7f", "#bcbd22", "#17becf", "#aec7e8", "#ffbb78", "#98df8a", "#ff9896",
    "#c5b0d5", "#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5", "#393b79",
    "#8c6d31", "#5254a3", "#6b6ecf", "#9c9ede", "#d6616b", "#e7ba52", "#843c39",
    "#7b4173", "#a55194", "#637939", "#b5cf6b", "#cedb9c", "#8ca252",
]

# ---------------------------------------------------------------- data

latest_row = query("""
    SELECT slot, slot_start_date_time FROM canonical_beacon_block
    WHERE meta_network_name = 'mainnet' AND slot_start_date_time >= now() - INTERVAL 2 DAY
    ORDER BY slot DESC LIMIT 1
""").iloc[0]
latest = int(latest_row["slot"])
last_updated = pd.Timestamp(latest_row["slot_start_date_time"]).floor("h")

# MEV-Boost registrations: latest gas_limit per validator. Merged with the local
# cache so validators that haven't re-registered in 30 days are kept.
try:
    reg_old = pd.read_parquet("mevboost_registrations.parquet")
except FileNotFoundError:
    reg_old = pd.DataFrame()
reg = query(f"""
    SELECT validator_index, max(slot) AS last_slot, argMax(gas_limit, slot) AS gas_limit
    FROM mev_relay_validator_registration
    WHERE meta_network_name = 'mainnet'
      AND slot >= {latest - 7200 * 30}
      AND slot_start_date_time >= now() - INTERVAL 32 DAY
    GROUP BY validator_index
""")
reg = reg.rename(columns={"last_slot": "slot"})
reg = pd.concat([reg, reg_old], ignore_index=True)
reg = reg.loc[reg.groupby("validator_index")["slot"].idxmax()]
reg.to_parquet("mevboost_registrations.parquet", index=None)

exited = set(query("""
    SELECT DISTINCT voluntary_exit_message_validator_index AS v
    FROM canonical_beacon_block_voluntary_exit
    WHERE meta_network_name = 'mainnet'
""")["v"])
reg = reg[~reg["validator_index"].isin(exited)]
reg = dict(zip(reg["validator_index"].astype(int), reg["gas_limit"].astype(int)))

# Blocks of the last 90 days (GROUP BY slot dedupes re-ingested rows).
df = query(f"""
    SELECT slot, any(proposer_index) AS proposer_index,
           any(execution_payload_gas_limit) AS execution_payload_gas_limit,
           toDate(any(slot_start_date_time)) AS date
    FROM canonical_beacon_block
    WHERE meta_network_name = 'mainnet'
      AND slot BETWEEN {latest - 7200 * 90} AND {latest}
      AND slot_start_date_time >= now() - INTERVAL 92 DAY
    GROUP BY slot ORDER BY slot
""").dropna(subset=["execution_payload_gas_limit"])
df["execution_payload_gas_limit"] = df["execution_payload_gas_limit"].astype("int64")

# Classify each block as "raised the limit" (60M bucket) or not (45M bucket).
# Above 45M: a block is a 60M signal iff it raised the limit vs its parent.
# At/below 45M: a raise also counts, unless the proposer's registration asks < 60M.
curr = df["execution_payload_gas_limit"]
prev = curr.shift(1, fill_value=30_000_000)
reg_wanted = df["proposer_index"].map(reg)
raised = curr > prev
ok_registration = reg_wanted.isna() | (reg_wanted >= 60_000_000)
df["execution_payload_gas_limit_adj"] = 45_000_000
df.loc[raised & ((prev > 45_000_000) | ok_registration), "execution_payload_gas_limit_adj"] = 60_000_000

dfg = df.groupby(["date", "execution_payload_gas_limit_adj"])["slot"].nunique().reset_index()
dfg["signal_per"] = dfg["slot"] / dfg.groupby("date")["slot"].transform("sum") * 100
pivoted = dfg.pivot(index="date", columns="execution_payload_gas_limit_adj", values="signal_per")
pivoted = pivoted.reindex(columns=[45_000_000, 60_000_000], fill_value=0).fillna(0)

# Entity labels (ethseer). "foo_lido" -> "foo (lido)", "solo_stakers" -> "solo stakers".
labels = query("""
    SELECT index AS validator_id, argMax(entity, updated_date_time) AS label
    FROM ethseer_validator_entity
    WHERE meta_network_name = 'mainnet'
    GROUP BY index
""")
labels["label"] = (labels["label"].astype(str)
                   .str.replace(r"_lido$", " (lido)", regex=True)
                   .replace({"solo_stakers": "solo stakers"}))
label_map = dict(zip(labels["validator_id"], labels["label"]))

dfn = df.copy()
dfn["label"] = dfn["proposer_index"].map(label_map).fillna("unidentified")
order = dfn.groupby("label")["slot"].count().sort_values(ascending=False).index.tolist()

dfn2 = dfn.copy()
# ethseer has ~850 entities; keep the chart legend readable.
TOP_N = 40
order = order[:TOP_N] + ["others"]
dfn["label"] = dfn["label"].where(dfn["label"].isin(order), "others")
dfn = dfn[dfn["execution_payload_gas_limit_adj"].isin([45_000_000, 60_000_000])]
dfn = dfn.groupby(["date", "label", "execution_payload_gas_limit_adj"])["slot"].nunique().reset_index()

# Per-entity share of blocks that did NOT raise the limit over the last week.
dfn2["date"] = pd.to_datetime(dfn2["date"])
dfn2 = dfn2[dfn2["date"] >= dfn2["date"].max() - timedelta(weeks=1)]
dfn2["gas_limit_45M"] = (dfn2["execution_payload_gas_limit_adj"] == 45_000_000).astype(int)
dfn2["gas_limit_60M"] = (dfn2["execution_payload_gas_limit_adj"] == 60_000_000).astype(int)
dfn2 = dfn2.groupby("label")[["gas_limit_45M", "gas_limit_60M"]].sum().reset_index()
dfn2 = dfn2[dfn2["gas_limit_45M"] + dfn2["gas_limit_60M"] >= 10]  # drop tiny entities
dfn2["perc"] = dfn2["gas_limit_45M"] / (dfn2["gas_limit_60M"] + dfn2["gas_limit_45M"])
(dfn2.sort_values(["perc", "gas_limit_60M", "gas_limit_45M"], ascending=False)
     .reset_index(drop=True)
     .to_json("assets/entity_data.json", orient="values"))

mean_gas_limit = df[df["slot"] >= df["slot"].max() - 7200]["execution_payload_gas_limit"].mean()
latest_gas_limit = df.loc[df["slot"].idxmax(), "execution_payload_gas_limit"]

# Registration summary table: label x requested gas limit -> #validators.
reg_df = pd.DataFrame({"validator_id": list(reg.keys()), "gas_wanted": list(reg.values())})
reg_df["label"] = reg_df["validator_id"].map(label_map).fillna("unidentified")
(reg_df.groupby(["label", "gas_wanted"])["validator_id"].count().reset_index()
       .sort_values("validator_id", ascending=False)[["label", "gas_wanted", "validator_id"]]
       .reset_index(drop=True)
       .to_json("assets/reg_data.json", orient="records"))

# ---------------------------------------------------------------- charts

DARK = dict(plot_bgcolor="#1c1c1c", paper_bgcolor="#1c1c1c",
            font=dict(family="Ubuntu Mono", size=18, color="white"))
AXIS = dict(showgrid=True, gridcolor="gray", linecolor="white", title=None,
            tickfont=dict(size=18, color="white"), fixedrange=True)
LEGEND = dict(bgcolor="#2b2b2b", bordercolor="white", borderwidth=1, font=dict(color="white"))
MOBILE_LEGEND = dict(LEGEND, orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5)
HOVER = dict(font_size=20, font_color="white", bgcolor="black")


def create_chart(mobile=False):
    fig = go.Figure()
    fig.add_trace(go.Bar(x=pivoted.index, y=pivoted[45_000_000], name="<=45m",
                         marker_color=color_palette[0], hoverinfo="skip"))
    fig.add_trace(go.Bar(x=pivoted.index, y=pivoted[60_000_000], name=">=60m",
                         marker_color=color_palette[1], hovertemplate="%{y:.1f}%"))
    fig.update_layout(
        title="Gas Limit Signaling", barmode="stack", bargap=0, bargroupgap=0, **DARK,
        xaxis=AXIS, yaxis=dict(AXIS, ticksuffix="%", tick0=0, dtick=20),
        dragmode=False, legend=LEGEND, margin=dict(l=60, r=60, t=50, b=100),
        hovermode="x", hoverlabel=HOVER, height=500,
    )
    if mobile:
        fig.update_layout(margin=dict(l=60, r=60, t=150, b=100), legend=MOBILE_LEGEND)
    fig.update_xaxes(tickangle=45)
    return fig


def create_chart_entities(mobile=False):
    fig = go.Figure()
    for ix, label in enumerate(order):
        _df = dfn[(dfn["label"] == label) & (dfn["execution_payload_gas_limit_adj"] == 60_000_000)]
        fig.add_trace(go.Bar(x=_df["date"], y=_df["slot"], name=label,
                             marker_color=color_palette[(ix + 1) % len(color_palette)],
                             hoverinfo="skip"))
    fig.update_layout(
        title=None, barmode="stack", bargap=0, bargroupgap=0, **DARK,
        xaxis=AXIS, yaxis=dict(AXIS, title="nr. of blocks", tick0=0),
        dragmode=False, legend=dict(MOBILE_LEGEND, traceorder="normal"),
        margin=dict(l=60, r=60, t=150 if mobile else 50, b=100),
        hovermode="x", hoverlabel=HOVER, height=900,
    )
    fig.update_xaxes(tickangle=45)
    return fig


def create_chart_reg(mobile=False):
    vals = [sum(g < 60_000_000 for g in reg.values()), sum(g >= 60_000_000 for g in reg.values())]
    grid = dict(showgrid=True, gridcolor="lightgrey", gridwidth=0.1,
                zeroline=True, zerolinecolor="grey", zerolinewidth=1)
    fig = go.Figure(go.Bar(
        x=["45M", "60M"], y=vals,
        marker=dict(color=color_palette[:2], line=dict(color="rgb(50,50,50)", width=1.5)),
        hovertemplate="<b>%{x}</b><br>Validators: %{y}<extra></extra>",
    ))
    fig.update_layout(
        title={"text": "MEV-Boost Validators", "x": 0.5, "xanchor": "center"},
        template="plotly_white", **DARK, margin=dict(l=60, r=20, t=60, b=80),
        xaxis=dict(grid, title=None, tickangle=-45, automargin=True),
        yaxis=dict(grid, title="Number of Validators"),
    )
    return fig


desktop_chart_html = create_chart(False).to_html(include_plotlyjs="inline", full_html=False)
mobile_chart_html = create_chart(True).to_html(include_plotlyjs=False, full_html=False)
reg_desktop_chart_html = create_chart_reg(False).to_html(include_plotlyjs=False, full_html=False)
reg_mobile_chart_html = create_chart_reg(True).to_html(include_plotlyjs=False, full_html=False)
entities_desktop_chart_html = create_chart_entities(False).to_html(include_plotlyjs="inline", full_html=False)
entities_mobile_chart_html = create_chart_entities(True).to_html(include_plotlyjs=False, full_html=False)

# ---------------------------------------------------------------- html

custom_css = """
<style>
body, p {
    color: #ffffff !important;
    background-color: #1c1c1c !important;
    font-family: 'Ubuntu Mono', monospace;
    margin-left: 10%;
    margin-right: 10%;
}
@media (max-width: 768px) {
    body, p {
        margin-left: 5% !important;
        margin-right: 5% !important;
    }
}
.header-text {
    text-align: center;
    margin: 20px;
    font-size: 16px;
    color: #cccccc;
    font-family: 'Ubuntu', sans-serif;
    line-height: 1.5;
}
.desktop-chart { display: block; }
.mobile-chart { display: none; }
@media (max-width: 768px) {
    .desktop-chart { display: none; }
    .mobile-chart { display: block; }
}
.header-text a:first-of-type {
    font-size: 1.5em;
    font-weight: bold;
    text-decoration: none;
}
.section-separator {
  border: none;
  border-top: 1px solid #fff;
  margin: 2rem 0;
  width: 100%;
  display: block;
}
</style>
"""

meta_tags = """
<meta name="description" content="A report on Gas Limit Signaling with interactive charts."/>
<meta name="keywords" content="Ethereum, Gas Limit, Blockchain, Analytics, Data Visualization"/>
<meta name="author" content="Toni Wahrstätter"/>
<meta property="og:title" content="Gas Limit Signaling Report"/>
<meta property="og:description" content="Detailed insights on Gas Limit Signaling."/>
<meta property="og:image" content="https://gaslimit.pics/assets/previewimage.png"/>
<meta property="og:url" content="https://gaslimit.pics"/>
<meta property="og:type" content="website"/>
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:title" content="Gas Limit Signaling Report"/>
<meta name="twitter:description" content="Detailed insights on Gas Limit Signaling."/>
<meta name="twitter:image" content="https://gaslimit.pics/assets/previewimage.png"/>
<link rel="shortcut icon" href="https://mevboost.toniwahrstaetter.com/ethlogo.png">
"""


def header(link_html):
    return f"""
<div class="header-text">
    <a href="https://github.com/nerolation/gaslimit.pics" style="color:orange;">GasLimit.Pics</a> <br/>
    Made with ❤️ by <a href="https://x.com/nero_eth" style="color:orange;">Toni</a> |
    <a href="https://github.com/nerolation/gaslimit.pics" style="color:orange;">GitHub</a> | Last updated: {last_updated:%Y-%m-%d %H:%M:%S} UTC<br/>
    Avg. Gas Limit (24h): {mean_gas_limit:,.0f} | Latest: {latest_gas_limit:,.0f}<br/>
    Data: <a href="https://github.com/ethpandaops/xatu" style="color:orange;">Xatu</a> by <a href="https://ethpandaops.io/" style="color:orange;">EthPandaOps</a>. <br/>
    {link_html}<br/>
</div>
"""


index_html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
{meta_tags}
{custom_css}
</head>
<body>
{header('<a href="/entities/" style="color:orange;">show individual entities</a>')}
<div class="desktop-chart">
    {desktop_chart_html}
</div>
<hr class="section-separator desktop-chart">
<div class="desktop-chart">
    {reg_desktop_chart_html}
</div>
<div class="mobile-chart">
    {mobile_chart_html}
</div>
<hr class="section-separator mobile-chart">
<div class="mobile-chart">
    {reg_mobile_chart_html}
</div>
</body>
</html>
"""

entities_css = custom_css + """
<style>
#dashboard { display: flex; flex-direction: column; padding: 20px; }
.validator { display: flex; align-items: center; justify-content: space-between; margin-bottom: 5px; }
.validator-name {
    flex: 1; margin-right: 5px; font-size: 14px; color: #ffffff; font-weight: bold;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.progress-bar-container {
    flex: 2; max-width: 70%; background-color: #e0e0e0; border-radius: 5px;
    overflow: hidden; position: relative;
}
.progress-bar {
    height: 20px; background-color: #ff7f0e; width: 0%; transition: width 0.5s ease-in-out;
    text-align: center; line-height: 20px; font-size: 12px; color: #101010; font-weight: bold;
}
.tooltip {
    position: absolute; background-color: rgba(0, 0, 0, 0.8); color: #ffffff; padding: 8px;
    border-radius: 5px; font-size: 12px; font-family: 'Ubuntu Mono', monospace;
    display: none; z-index: 1000; pointer-events: none; white-space: pre-line;
}
#gasTable_filter { display: none; }
.label-filter { margin-bottom: 1em; }
</style>
"""

# Data is fetched same-origin from /assets and only ever inserted as text.
entities_js = """
<script>
document.addEventListener("DOMContentLoaded", function () {
    const dashboard = document.getElementById("dashboard");
    const tooltip = document.createElement("div");
    tooltip.className = "tooltip";
    document.body.appendChild(tooltip);

    fetch("/assets/entity_data.json")
        .then(r => r.json())
        .then(data => {
            data.forEach(([entity, blocks_kept, blocks_raised, percentage]) => {
                blocks_kept = Number(blocks_kept) || 0;
                blocks_raised = Number(blocks_raised) || 0;
                percentage = Number(percentage) || 0;
                const total = blocks_kept + blocks_raised;
                const pct = (percentage * 100).toFixed(2);

                const row = document.createElement("div");
                row.className = "validator";
                const name = document.createElement("div");
                name.className = "validator-name";
                name.textContent = entity;
                const container = document.createElement("div");
                container.className = "progress-bar-container";
                const bar = document.createElement("div");
                bar.className = "progress-bar";
                bar.style.width = `${pct}%`;
                bar.textContent = `${pct}%`;

                const move = e => {
                    tooltip.style.left = `${e.pageX + 10}px`;
                    tooltip.style.top = `${e.pageY + 10}px`;
                };
                container.addEventListener("mouseover", e => {
                    tooltip.textContent =
                        `Total number of blocks (last 7 days): ${total}\\n` +
                        `Number of blocks that increased the gas limit: ${blocks_raised}`;
                    tooltip.style.display = "block";
                    move(e);
                });
                container.addEventListener("mousemove", move);
                container.addEventListener("mouseout", () => { tooltip.style.display = "none"; });

                container.appendChild(bar);
                row.appendChild(name);
                row.appendChild(container);
                dashboard.appendChild(row);
            });
        })
        .catch(err => console.error("Error fetching data:", err));
});
</script>
"""

reg_table = """
<div class="label-filter">
  <label>
    Filter by Label:
    <select id="labelSelect">
      <option value="">All</option>
    </select>
  </label>
</div>
<table id="gasTable" class="display" style="width:100%">
  <thead>
    <tr><th>Label</th><th>Gas Limit</th><th>Validators</th></tr>
  </thead>
</table>
<script>
document.addEventListener("DOMContentLoaded", function() {
  fetch("/assets/reg_data.json")
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var labelSelect = document.getElementById("labelSelect");
      Array.from(new Set(data.map(function(d) { return d.label; })))
        .sort()
        .forEach(function(lbl) {
          var opt = document.createElement("option");
          opt.value = lbl;
          opt.textContent = lbl;
          labelSelect.appendChild(opt);
        });
      var table = $("#gasTable").DataTable({
        data: data,
        columns: [
          { data: "label", render: $.fn.dataTable.render.text() },
          { data: "gas_wanted", render: $.fn.dataTable.render.number(",", ".", 0) },
          { data: "validator_id", render: $.fn.dataTable.render.number(",", ".", 0) }
        ],
        pageLength: 10,
        order: [[2, "desc"]],
        dom: "tip"
      });
      labelSelect.addEventListener("change", function() {
        var val = $.fn.dataTable.util.escapeRegex(labelSelect.value);
        table.column(0).search(val ? "^" + val + "$" : "", true, false).draw();
      });
    })
    .catch(function(err) { console.error("Error loading JSON:", err); });
});
</script>
"""

entities_html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
{meta_tags}
{entities_css}
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css"
      integrity="sha384-w9ufcIOKS67vY4KePhJtmWDp4+Ai5DMaHvqqF85VvjaGYSW2AhIbqorgKYqIJopv" crossorigin="anonymous"/>
<script src="https://code.jquery.com/jquery-3.6.4.min.js"
        integrity="sha384-UG8ao2jwOWB7/oDdObZc6ItJmwUkR/PfMyt9Qs5AwX7PsnYn1CRKCTWyncPTWvaS" crossorigin="anonymous"></script>
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"
        integrity="sha384-k5vbMeKHbxEZ0AEBTSdR7UjAgWCcUfrS8c0c5b2AfIh7olfhNkyCZYwOfzOQhauK" crossorigin="anonymous"></script>
{entities_js}
</head>
<body>
{header('<a href="/" style="color:orange;">back to homepage</a>')}
<hr class="section-separator">
{reg_table}
<hr class="section-separator">
<div class="desktop-chart">
    {entities_desktop_chart_html}
</div>
<div class="mobile-chart">
    {entities_mobile_chart_html}
</div>
<hr class="section-separator">
<div id="dashboard"></div>
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(index_html)
os.makedirs("entities", exist_ok=True)
with open("entities/index.html", "w", encoding="utf-8") as f:
    f.write(entities_html)

print(f"slot {latest} | {last_updated} UTC | avg 24h gas limit {mean_gas_limit:,.0f} | "
      f"{len(df):,} blocks | {len(reg):,} registered validators")
