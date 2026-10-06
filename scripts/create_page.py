#!/usr/bin/env python
# coding: utf-8

# In[ ]:



import plotly.graph_objects as go
from plotly.colors import qualitative
from collections import Counter
import pyxatu
import pandas as pd
from datetime import timedelta


xatu = pyxatu.PyXatu()

color_palette = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
    "#aec7e8",
    "#ffbb78",
    "#98df8a",
    "#ff9896",
    "#c5b0d5",
    "#c49c94",
    "#f7b6d2",
    "#c7c7c7",
    "#dbdb8d",
    "#9edae5",
    "#393b79",
    "#8c6d31",
    "#5254a3",
    "#6b6ecf",
    "#9c9ede",
    "#d6616b",
    "#e7ba52",
    "#843c39",
    "#7b4173",
    "#a55194",
    "#637939",
    "#b5cf6b",
    "#cedb9c",
    "#8ca252",
]


# In[ ]:


latest = xatu.get_slots(columns="max(slot) as slot")["slot"][0]
latest


# In[ ]:


reg_old = pd.read_parquet("mevboost_registrations.parquet")
reg = xatu.execute_query(f"""
    SELECT distinct validator_index, slot, gas_limit
    FROM mev_relay_validator_registration
    where slot >= {latest  - 7200*30} and meta_network_name = 'mainnet'
""",columns="validator_index, slot, gas_limit")
reg = pd.concat([reg, reg_old], ignore_index=True)
reg = reg.loc[reg.groupby('validator_index')['slot'].idxmax()]
reg.to_parquet("mevboost_registrations.parquet", index=None)


# In[ ]:


last_update = reg[["validator_index", "slot"]].set_index("validator_index").to_dict()["slot"]
reg = reg[["validator_index", "gas_limit"]].set_index("validator_index").to_dict()["gas_limit"]


exited = set(xatu.execute_query("""
    SELECT distinct voluntary_exit_message_validator_index FROM canonical_beacon_block_voluntary_exit
    where meta_network_name = 'mainnet'
""")[0])

reg = {i: reg[i] for i in reg.keys() if i not in exited}



# In[ ]:


reg_keys = set(reg.keys())


# In[ ]:


df = xatu.get_beacon_block_v2(slot=[latest-7200*90, latest], columns="slot, proposer_index,execution_payload_gas_limit")
df.sort_values("slot", inplace=True)

last_value = 30_000_000
df["execution_payload_gas_limit_adj"] = 0


# In[ ]:


for ix, i in df.iterrows():
    #print(int(i["slot"]), end="\r")
    #slot = int(i["slot"])
    #if slot != last_slot + 1:
    #    print("missing slot", slot-1)

    curr_value = int(i["execution_payload_gas_limit"])
    if curr_value > last_value and int(i["proposer_index"]) in reg_keys and reg[int(i["proposer_index"])] > 36_000_000:
        df.loc[ix, "execution_payload_gas_limit_adj"] = 60_000_000
    elif curr_value > last_value and (last_value-curr_value) <= last_value/(1/1024):
        df.loc[ix, "execution_payload_gas_limit_adj"] = 36_000_000
    elif curr_value == 36_000_000 and curr_value == last_value:
        df.loc[ix, "execution_payload_gas_limit_adj"] = 36_000_000
    elif curr_value > last_value and (last_value-curr_value) > last_value/(1/1024):
        print("something strange with slot ", int(i["slot"]))
    else:
        if curr_value < last_value and int(i["proposer_index"]) in reg_keys and reg[int(i["proposer_index"])] >= last_value:
            with open("somethingwrong.txt", "w") as file:
                file.write(f"{curr_value}, {last_value}, {reg[int(i['proposer_index'])]}, {int(i['slot'])}\n")
        if int(i["proposer_index"]) in reg_keys and reg[int(i["proposer_index"])] >= 36_000_000 and last_update[int(i["proposer_index"])] >= int(i["slot"]):
            df.loc[ix, "execution_payload_gas_limit_adj"] = 36_000_000
        else:
            # drop from >36m to something still above 35_999_999. Validator not registered at relay with value higher than 36m
            if curr_value >= 36_000_000 and last_value > 36_000_000 and last_value > curr_value:
                df.loc[ix, "execution_payload_gas_limit_adj"] = 36_000_000    
            else:
                df.loc[ix, "execution_payload_gas_limit_adj"] = 30_000_000    
    
    last_value = curr_value    
    #last_slot = slot

#df = xatu.get_beacon_block_v2(slot=[latest-7200*30, latest], columns="slot, proposer_index,execution_payload_gas_limit")
#df["execution_payload_gas_limit_adj"] = df["execution_payload_gas_limit"].apply(lambda x: 30_000_000 if x <= 30_000_000 else 60_000_000)
df["date"] = df.slot.apply(lambda x: xatu.helpers.slot_to_day(x))
#df[df["execution_payload_gas_limit"] != 30000000].execution_payload_gas_limit.unique()

dfg = df.groupby(["date","execution_payload_gas_limit_adj"])["slot"].nunique().reset_index()

dfg_day = dfg.groupby("date")["slot"].sum().reset_index().rename(columns={"slot": "total"})
dfg = pd.merge(dfg, dfg_day, how="left", left_on="date", right_on="date")
dfg["signal_per"] = dfg["slot"] / dfg["total"] * 100
dfg = dfg[["date", "execution_payload_gas_limit_adj", "signal_per"]]
pivoted = dfg.pivot(index='date', columns='execution_payload_gas_limit_adj', values='signal_per')


# In[ ]:


#dfn.groupby(["date", "label"])["slot"].sum().reset_index().rename(columns={"slot": "total"})


# In[ ]:


#dfn_day = dfn.groupby(["date"])["slot"].sum().reset_index().rename(columns={"slot": "total"})
#dfn = pd.merge(dfn, dfn_day, how="left", left_on="date", right_on="date")
#dfn["signal_per"] = dfn["slot"] / dfn["total"] * 100
#dfn = dfn[["date", "label", "execution_payload_gas_limit_adj", "signal_per"]]
##pivotedn = dfn.pivot(index='date', columns='execution_payload_gas_limit_adj', values='signal_per')
#dfn


# In[ ]:


labels = xatu.validators.mapping[["validator_id", "label", "lido_node_operator"]]
labels["label"] = labels["label"].fillna("unidentified")


# In[ ]:


dfn = pd.merge(df, labels, how="left", left_on="proposer_index", right_on="validator_id")
dfn["label"] = dfn["label"].fillna("unidentified")


# In[ ]:


dfn["label"] = dfn.apply(lambda x: str(x["lido_node_operator"]).lower() + " (lido)" if x["lido_node_operator"] != None and not pd.isna(x["lido_node_operator"]) else x["label"] , axis=1)


# In[ ]:


#dfn.dropna(subset="label", inplace=True)


# In[ ]:


dfn["label"] = dfn["label"].apply(lambda x: "solo stakers" if x.endswith(".eth") else x)


# In[ ]:


dfn2 = dfn.copy()


# In[ ]:


#kiln = dfn2[dfn2["label"] == "kiln"]
#kiln


# In[ ]:


order = dfn.groupby("label")["slot"].sum().reset_index().sort_values("slot", ascending=False)["label"].tolist()#[:45]

dfn = dfn[dfn["execution_payload_gas_limit_adj"] > 31_000_000]
dfn = dfn.groupby(["date", "label", "execution_payload_gas_limit_adj"])["slot"].nunique().reset_index()


# In[ ]:


dfn["label"] = dfn["label"].apply(lambda x: "others" if x not in order else x)
order.append("others")

if "solo stakers" not in order:
    order.append("solo stakers")


# In[ ]:


dfn2 = dfn2.groupby(["date", "label", "execution_payload_gas_limit_adj"])["slot"].nunique().reset_index()
dfn2["label"] = dfn2["label"].apply(lambda x: "others" if x not in order else x)


# In[ ]:


for i in dfn2.label.unique():
    if dfn2[["label", "execution_payload_gas_limit_adj"]].drop_duplicates().label.tolist().count(i) == 2:
        continue
    #print(i)
    else:
        if dfn2[dfn2["label"] == i].execution_payload_gas_limit_adj.tolist()[0] == 36000000:
            continue
        if dfn2[dfn2["label"] == i].execution_payload_gas_limit_adj.tolist()[0] == 60000000:
            continue
        for j in dfn2[dfn2["label"] == i].date.unique().tolist():
            dfn2.loc[len(dfn2), ("date"   ,"label"  ,"execution_payload_gas_limit_adj"  ,"slot")] = (
                j,
                i,
                36000000,
                0
            )


# In[ ]:


dfn2['gas_limit_30M'] = dfn2['slot'].where(dfn2['execution_payload_gas_limit_adj'] == 30000000, 0)
dfn2['gas_limit_36M'] = dfn2['slot'].where(dfn2['execution_payload_gas_limit_adj'] == 36000000, 0)
dfn2['gas_limit_60M'] = dfn2['slot'].where(dfn2['execution_payload_gas_limit_adj'] == 60000000, 0)

dfn2 = dfn2.drop(columns=['execution_payload_gas_limit_adj'])

dfn2['date'] = pd.to_datetime(dfn2['date'])  

cutoff_date = dfn2['date'].max() - timedelta(weeks=1)

dfn2 = dfn2[dfn2['date'] >= cutoff_date]
#dfn2 = dfn2.groupby(["date", "label"])["gas_limit_30M", "gas_limit_60M"].sum().reset_index()

dfn2 = dfn2.groupby(["label"])[["gas_limit_30M", "gas_limit_36M", "gas_limit_60M"]].sum().reset_index()


# In[ ]:


dfn2["perc"] = dfn2["gas_limit_60M"] / (dfn2["gas_limit_30M"] + dfn2["gas_limit_60M"] + dfn2["gas_limit_36M"])
dfn2["gas_limit_30M"] = dfn2["gas_limit_30M"].astype(int)
dfn2["gas_limit_36M"] = dfn2["gas_limit_36M"].astype(int)
dfn2["gas_limit_60M"] = dfn2["gas_limit_60M"].astype(int)
dfn2.sort_values(["perc", "gas_limit_30M", "gas_limit_60M"], ascending=False).reset_index(drop=True).to_json("entity_data.json", orient="values")

# In[ ]:


mean_gas_limit = df[df["slot"] >= df.slot.max()-7200].execution_payload_gas_limit.mean()
mean_gas_limit


# In[ ]:


current_limit = mean_gas_limit//1_000_000*1_000_000


# In[ ]:


if current_limit != 36_000_000:
    df.replace(30_000_000, current_limit, inplace=True)


# In[ ]:


def create_chart_entities(mobile=False):

    fig = go.Figure()
    
    for ix, label in enumerate(order):
        _df = dfn[dfn["label"] == label]
        _df = _df[_df["execution_payload_gas_limit_adj"] == 60_000_000]

        fig.add_trace(go.Bar(
            x=_df.date,
            y=_df["slot"],
            name=f'{label}',
            marker_color=color_palette[(ix+1) % len(color_palette)],
            hoverinfo='skip'  # Skip hover info for this trace
        ))

    #fig.add_shape(
    #    type="line",
    #    x0=0,
    #    x1=1,
    #    y0=3550,
    #    y1=3550,
    #    xref='paper',
    #    yref='y',
    #    line=dict(color="red", width=2, dash="dash")
    #)

    #fig.add_annotation(
    #    x=1,  # Position at the far right
    #    y=3550,
    #    xref="paper",
    #    yref="y",
    #    text="50% threshold",
    #    showarrow=False,
    #    font=dict(size=14, color="red"),
    #    align="right",
    #    xanchor="left"
    #)

    fig.update_layout(
        title=None,
        barmode='stack',
        bargap=0,
        bargroupgap=0,
        plot_bgcolor='#1c1c1c',
        paper_bgcolor='#1c1c1c',
        font=dict(family='Ubuntu Mono', size=18, color='white'),
        xaxis=dict(
            showgrid=True,
            gridcolor='gray',
            linecolor='white',
            title=None,
            tickfont=dict(size=18, color='white'),
            fixedrange=True
            
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor='gray',
            linecolor='white',
            title="nr. of blocks",
            tickfont=dict(size=18, color='white'),
            tick0=0,
            fixedrange=True
            
        ),
        dragmode=False,
        legend=dict(
            bgcolor='#2b2b2b',
            bordercolor='white',
            borderwidth=1,
            font=dict(color='white'),
            traceorder="normal",
                orientation="h",
            yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
        ),
        margin=dict(l=60, r=60, t=50, b=100),
        hovermode='x',
        hoverlabel=dict(
            font_size=20,
            font_color='white',
            bgcolor='black'
        ),
        height=900,
        #width =1200
    )
    
    if mobile:
        fig.update_layout(
            margin=dict(l=60, r=60, t=150, b=100),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
                bgcolor='#2b2b2b',
                bordercolor='white',
                borderwidth=1,
                font=dict(color='white'),
                traceorder="normal"
            )
        )

    fig.update_xaxes(tickangle=45)
    return fig

#create_chart_entities()


# In[ ]:




def create_chart_reg(mobile=False):
    validator_preferences = reg.copy()
    # Categorize the gas limits
    categories = {"30M": 0, "36M": 0, "60M": 0}

    for gas_limit in validator_preferences.values():
        if gas_limit == 30000000:
            categories["30M"] += 1
        elif gas_limit == 36000000:
            categories["36M"] += 1
        else:

            categories["60M"] += 1

    # Example data
    cats = list(categories.keys())
    vals = list(categories.values())


    fig = go.Figure(
        go.Bar(
            x=cats,
            y=vals,
            marker=dict(
                color=color_palette,
                line=dict(color='rgb(50,50,50)', width=1.5)
            ),
            hovertemplate='<b>%{x}</b><br>Validators: %{y}<extra></extra>',
        )
    )

    fig.update_layout(
        title={
            'text': 'MEV-Boost Validators',
            'x':0.5,          # centered
            'xanchor': 'center'
        },
        template='plotly_white',
        plot_bgcolor='#1c1c1c',
        paper_bgcolor='#1c1c1c',
        font=dict(family='Ubuntu Mono', size=18, color='white'),
        margin=dict(l=60, r=20, t=60, b=80),

        xaxis=dict(
            title=None,
            tickangle=-45,
            automargin=True,
            showgrid=True,            # turn on grid
            gridcolor='lightgrey',
            gridwidth=0.1,
            zeroline=True,
            zerolinecolor='grey',
            zerolinewidth=1,
        ),
        yaxis=dict(
            title='Number of Validators',
            showgrid=True,
            gridcolor='lightgrey',
            gridwidth=0.1,
            zeroline=True,
            zerolinecolor='grey',
            zerolinewidth=1,
        )
    ),

    height=500,
    return fig



# In[ ]:





# In[ ]:


def create_chart(mobile=False):

    fig = go.Figure()    

    fig.add_trace(go.Bar(
        x=pivoted.index,
        y=pivoted[30000000],
        name='<36m',
        marker_color=color_palette[0],
        hoverinfo='skip'  # Skip hover info for this trace
    ))

    fig.add_trace(go.Bar(
        x=pivoted.index,
        y=pivoted[36000000],
        name='36m',
        marker_color=color_palette[1],
        hovertemplate="%{y:.1f}%"  # Show date and value with '%'
    ))
    
    fig.add_trace(go.Bar(
        x=pivoted.index,
        y=pivoted[60000000],
        name= "≥60m",
        marker_color=color_palette[2],
        hovertemplate="%{y:.1f}%"  # Show date and value with '%'
    ))

    fig.update_layout(
        title="Gas Limit Signaling",
        barmode='stack',
        bargap=0,
        bargroupgap=0,
        plot_bgcolor='#1c1c1c',
        paper_bgcolor='#1c1c1c',
        font=dict(family='Ubuntu Mono', size=18, color='white'),
        xaxis=dict(
            showgrid=True,
            gridcolor='gray',
            linecolor='white',
            title=None,
            tickfont=dict(size=18, color='white'),
            fixedrange=True
        ),
        dragmode=False,
        yaxis=dict(
            showgrid=True,
            gridcolor='gray',
            linecolor='white',
            title=None,
            tickfont=dict(size=18, color='white'),
            ticksuffix='%',
            tick0=0,
            dtick=20,
            fixedrange=True
        ),
        legend=dict(
            bgcolor='#2b2b2b',
            bordercolor='white',
            borderwidth=1,
            font=dict(color='white')
        ),
        margin=dict(l=60, r=60, t=50, b=100),
        hovermode='x',  # Vertical hover line at the x position
        hoverlabel=dict(
            font_size=20,
            font_color='white',
            bgcolor='black'  # Contrast background for hover labels
        ),
        height=500,
        #width=1000
    )
    if mobile:
        fig.update_layout(
            margin=dict(l=60, r=60, t=150, b=100),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
                bgcolor='#2b2b2b',
                bordercolor='white',
                borderwidth=1,
                font=dict(color='white')
            )
        )


    fig.update_xaxes(tickangle=45)
    return fig

def create_reg_summary():
    labels = xatu.validators.mapping[["validator_id", "label", "lido_node_operator"]].copy()
    labels["label"] = labels["label"].fillna("unidentified")
    labels["label"] = labels.apply(lambda x: str(x["lido_node_operator"]).lower() + " (lido)" if x["lido_node_operator"] != None and not pd.isna(x["lido_node_operator"]) else x["label"] , axis=1)
    labels.drop("lido_node_operator", axis=1, inplace=True)
    regs= set(reg.keys())
    labels["gas_wanted"] = labels.validator_id.apply(lambda x: reg[x] if x in regs else None)
    #labels.dropna(inplace=True)
    reg_summary = labels.groupby(["label", "gas_wanted"])["validator_id"].count().reset_index().sort_values("validator_id", ascending=False)
    reg_summary.reset_index(drop=True, inplace=True)
    reg_summary.to_json("assets/reg_data.json", orient="records")

#create_chart()


# In[ ]:


#fig.write_html("index.html", include_plotlyjs='inline', full_html=True)
#with open("index.html", "r", encoding="utf-8") as f:
#    html_content = f.read()
#fig.write_html("index.html", include_plotlyjs='inline', full_html=True)
#html_content


# In[ ]:


desktop_fig = create_chart(mobile=False)
entities_fig = create_chart_entities(False)
mobile_fig = create_chart(mobile=True)
mobile_entities_fig = create_chart_entities(True)
desktop_reg_fig = create_chart_reg(False)
mobile_reg_fig = create_chart_reg(True)

# Get their HTML snippets
desktop_chart_html = desktop_fig.to_html(include_plotlyjs='inline', full_html=False)
entities_desktop_chart_html = entities_fig.to_html(include_plotlyjs='inline', full_html=False)
mobile_chart_html = mobile_fig.to_html(include_plotlyjs=False, full_html=False)
entities_mobile_chart_html = mobile_entities_fig.to_html(include_plotlyjs=False, full_html=False)
reg_desktop_chart_html = desktop_reg_fig.to_html(include_plotlyjs=False, full_html=False)
reg_mobile_chart_html = mobile_reg_fig.to_html(include_plotlyjs=False, full_html=False)
# Define custom CSS and text you want at the top of the body

create_reg_summary()

# In[ ]:


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
    color: #cccccc; /* slightly grayer than white */
    font-family: 'Ubuntu', sans-serif; /* a rounder font */
    line-height: 1.5; /* additional line spacing */
}
.title-text {
    text-align: center;
    margin: 20px;
    font-size: 24px;
    font-weight: bold;
    color: orange;
}

/* By default, show desktop chart and hide mobile chart */
.desktop-chart { display: block; }
.mobile-chart { display: none; }

/* When screen width is small (e.g. < 768px), show mobile chart and hide desktop chart */
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

<!-- Open Graph Tags for social media previews -->
<meta property="og:title" content="Gas Limit Signaling Report"/>
<meta property="og:description" content="Detailed insights on Gas Limit Signaling."/>
<meta property="og:image" content="https://raw.githubusercontent.com/nerolation/gaslimit.pics/36591db90f6193f48261c0a0b035f505d378c05b/assets/previewimage.png"/>
<meta property="og:url" content="https://gaslimit.pics"/>
<meta property="og:type" content="website"/>

<!-- Twitter Card -->
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:title" content="Gas Limit Signaling Report"/>
<meta name="twitter:description" content="Detailed insights on Gas Limit Signaling."/>
<meta name="twitter:image" content="https://raw.githubusercontent.com/nerolation/gaslimit.pics/36591db90f6193f48261c0a0b035f505d378c05b/assets/previewimage.png"/>

<link rel="shortcut icon" href="https://mevboost.toniwahrstaetter.com/ethlogo.png">
"""

base_html = f"""
<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
{meta_tags}
{custom_css}
</head>
<body>
<div class="header-text">
    <a href="https://github.com/nerolation/gaslimit.pics" style="color:orange;">GasLimit.Pics</a> <br/> 
    Made with ❤️ by <a href="https://x.com/nero_eth" style="color:orange;">Toni</a> | 
    <a href="https://github.com/nerolation/gaslimit.pics" style="color:orange;">GitHub</a> | Last updated: {xatu.helpers.slot_to_hour(latest)}<br/>
    Avg. Gas Limit (24h): {mean_gas_limit:,.0f}<br/>
    Data: <a href="https://github.com/nerolation/pyxatu" style="color:orange;">Xatu</a> by <a href="https://ethpandaops.io/" style="color:orange;">EthPandaOps</a>. </br>
    <a href="https://gaslimit.pics/entities" style="color:orange;">show individual entities</a><br/>
</div>

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

with open("index.html", "w", encoding="utf-8") as f:
    f.write(base_html)

    
custom_css += """
<style>
#dashboard {
    display: flex;
    flex-direction: column;
    padding: 20px;
}

.validator {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 5px;
}

.validator-name {
    flex: 1; 
    margin-right: 5px;
    font-size: 14px;
    color: #ffffff;
    font-weight: bold;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.progress-bar-container {
    flex: 2;
    max-width: 70%; 
    background-color: #e0e0e0;
    border-radius: 5px;
    overflow: hidden;
    position: relative;
}

.progress-bar {
    height: 20px;
    background-color: #ff7f0e;
    width: 0%;
    transition: width 0.5s ease-in-out;
    text-align: center;
    line-height: 20px;
    font-size: 12px;
    color: #101010;
    font-weight: bold;
}

.tooltip {
    position: absolute;
    background-color: rgba(0, 0, 0, 0.8);
    color: #ffffff;
    padding: 8px;
    border-radius: 5px;
    font-size: 12px;
    font-family: 'Ubuntu Mono', monospace;
    display: none;
    z-index: 1000;
    pointer-events: none;
}

.progress-bar-container:hover .tooltip {
    display: block;
}
#gasTable_filter { display: none; }
.label-filter { margin-bottom: 1em; }
</style>
"""


meta_tags += """
<script>

document.addEventListener("DOMContentLoaded", function () {
    const dashboard = document.getElementById("dashboard");

    // Create a tooltip element
    const tooltip = document.createElement("div");
    tooltip.className = "tooltip";
    document.body.appendChild(tooltip);

    // Fetch data from the JSON file
    fetch("https://raw.githubusercontent.com/nerolation/gaslimit.pics/refs/heads/main/assets/entity_data.json")
        .then(response => response.json())
        .then(data => {
            data.forEach(([entity, blocks_30m, blocks_higher, percentage]) => {
                // Ensure the values are numbers
                blocks_30m = Number(blocks_30m) || 0; // Default to 0 if not a number
                blocks_higher = Number(blocks_higher) || 0; // Default to 0 if not a number
                percentage = Number(percentage) || 0; // Default to 0 if not a number

                // Calculate total blocks
                const totalBlocks = blocks_30m + blocks_higher;

                // Convert percentage to a readable format
                const progressPercentage = (percentage * 100).toFixed(2);

                // Create container for each validator/entity
                const validatorContainer = document.createElement("div");
                validatorContainer.className = "validator";

                // Create and set entity name
                const name = document.createElement("div");
                name.className = "validator-name";
                name.textContent = entity;

                // Create progress bar container
                const progressBarContainer = document.createElement("div");
                progressBarContainer.className = "progress-bar-container";

                // Create progress bar
                const progressBar = document.createElement("div");
                progressBar.className = "progress-bar";
                progressBar.style.width = `${progressPercentage}%`; // Set width dynamically
                progressBar.textContent = `${progressPercentage}%`; // Show percentage on the bar

                progressBarContainer.addEventListener("mouseover", (event) => {
                    tooltip.style.display = "block";
                    tooltip.innerHTML = `
                        Total number of blocks (last 7 days): ${totalBlocks}<br>
                        Number of blocks that increased the gas limit: ${blocks_higher}
                    `;
                    tooltip.style.left = `${event.pageX + 10}px`;
                    tooltip.style.top = `${event.pageY + 10}px`;
                });

                progressBarContainer.addEventListener("mousemove", (event) => {
                    tooltip.style.left = `${event.pageX + 10}px`;
                    tooltip.style.top = `${event.pageY + 10}px`;
                });

                progressBarContainer.addEventListener("mouseout", () => {
                    tooltip.style.display = "none";
                });

                // Append elements to the DOM
                progressBarContainer.appendChild(progressBar);
                validatorContainer.appendChild(name);
                validatorContainer.appendChild(progressBarContainer);
                dashboard.appendChild(validatorContainer);
            });
        })
        .catch(error => console.error("Error fetching data:", error));
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
    <tr>
      <th>Label</th>
      <th>Gas Limit</th>
      <th>Validators</th>
    </tr>
  </thead>
</table>

<script>
document.addEventListener("DOMContentLoaded", function() {
  // Fetch the JSON (same-origin)
  fetch("https://raw.githubusercontent.com/nerolation/gaslimit.pics/refs/heads/main/assets/reg_data.json")
    .then(function(response) { return response.json(); })
    .then(function(data) {
      // 1) Populate the label dropdown
      var labelSelect = document.getElementById("labelSelect");
      Array.from(new Set(data.map(function(d) { return d.label; })))
        .sort()
        .forEach(function(lbl) {
          var opt = document.createElement("option");
          opt.value = lbl;
          opt.textContent = lbl;
          labelSelect.appendChild(opt);
        });

      // 2) Initialize DataTable, default sorting by Validators (col index 2)
      var table = $("#gasTable").DataTable({
        data: data,
        columns: [
          { data: "label" },
          {
            data: "gas_wanted",
            render: $.fn.dataTable.render.number(",", ".", 0)
          },
          { data: "validator_id" }
        ],
        pageLength: 10,
        order: [[2, "desc"]],
        dom: "tip"
      });

      // 3) Wire the dropdown → table filter
      labelSelect.addEventListener("change", function() {
        var val = $.fn.dataTable.util.escapeRegex(labelSelect.value);
        table
          .column(0)
          .search(val ? "^" + val + "$" : "", true, false)
          .draw();
      });
    })
    .catch(function(err) {
      console.error("Error loading JSON:", err);
    });
});
</script>
"""

base_html = f"""
<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
{meta_tags}
{custom_css}



<link
  rel="stylesheet"
  href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css"
/>
<script src="https://code.jquery.com/jquery-3.6.4.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>

</head>
<body>
<div class="header-text">
    <a href="https://github.com/nerolation/gaslimit.pics" style="color:orange;">GasLimit.Pics</a> <br/> 
    Made with ❤️ by <a href="https://x.com/nero_eth" style="color:orange;">Toni</a> | 
    <a href="https://github.com/nerolation/gaslimit.pics" style="color:orange;">GitHub</a> | Last updated: {xatu.helpers.slot_to_hour(latest)}<br/>
    Avg. Gas Limit (24h): {mean_gas_limit:,.0f}<br/>
    Data: <a href="https://github.com/nerolation/pyxatu" style="color:orange;">Xatu</a> by <a href="https://ethpandaops.io/" style="color:orange;">EthPandaOps</a>. </br>
    <a href="https://gaslimit.pics/" style="color:orange;">back to homepage</a><br/>
</div>
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


with open("index_entities.html", "w", encoding="utf-8") as f:
    f.write(base_html)


# In[ ]:





# In[ ]:




