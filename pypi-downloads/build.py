import clickhouse_connect, pandas as pd, plotly.graph_objects as go, json, sys
from plotly.subplots import make_subplots
OUT = "/home/user/krayne/pypi-downloads"
PKGS = [("apache-airflow","Airflow"),("metaflow","Metaflow"),("flytekit","Flyte v1"),("flyte","Flyte v2"),("skypilot","SkyPilot")]
LIGHT = ["#2a78d6","#eb6834","#1baf7a","#eda100","#e87ba4"]
DARK  = ["#3987e5","#d95926","#199e70","#c98500","#d55181"]
WIN = pd.Timestamp("2022-11-01"); FLYTE2 = pd.Timestamp("2025-05-01")

c = clickhouse_connect.get_client(host="sql-clickhouse.clickhouse.com", port=443, username="demo", password="", secure=True)
df = c.query_df(f"""SELECT month, project, sum(count) AS downloads FROM pypi.pypi_downloads_per_month
 WHERE project IN ({",".join(repr(p) for p,_ in PKGS)}) GROUP BY month, project ORDER BY project, month""")
df["month"] = pd.to_datetime(df["month"])
excluded = int(df[(df.project=="flyte")&(df.month<FLYTE2)].downloads.sum())
df = df[~((df.project=="flyte")&(df.month<FLYTE2))]
# dense monthly grid from each package's first month
rows=[]
last = df.month.max()
for p,label in PKGS:
    s = df[df.project==p].set_index("month").downloads
    s = s.reindex(pd.date_range(s.index.min(), last, freq="MS"), fill_value=0)
    f = pd.DataFrame({"month":s.index,"package":p,"label":label,"downloads":s.values})
    f["lifetime_cumulative"] = f.downloads.cumsum()
    f["window_cumulative"] = f.downloads.where(f.month>=WIN,0).cumsum()
    rows.append(f)
d = pd.concat(rows)
d["partial_month"] = d.month==last
w = d[d.month>=WIN].copy()
w.assign(month=w.month.dt.strftime("%Y-%m")).to_csv(f"{OUT}/downloads.csv", index=False)

fig = make_subplots(rows=1, cols=2, shared_xaxes=False, horizontal_spacing=0.1,
    subplot_titles=("Lifetime cumulative (since first release)", "Cumulative since Nov 2022"))
import math
LBL=[]
for i,(p,label) in enumerate(PKGS):
    g = w[w.package==p]
    for col,key in [(1,"lifetime_cumulative"),(2,"window_cumulative")]:
        gg = g if key=="lifetime_cumulative" else g[g[key]>0]
        fig.add_trace(go.Scatter(x=gg.month, y=gg[key], name=label, legendgroup=label, showlegend=(col==1),
            mode="lines", line=dict(color=LIGHT[i], width=2), customdata=gg.downloads,
            hovertemplate=f"<b>{label}</b><br>%{{x|%b %Y}}<br>cumulative %{{y:,.0f}}<br>that month %{{customdata:,.0f}}<extra></extra>"), row=1, col=col)
        end = gg.iloc[-1]
        LBL.append(dict(x=end.month, y=float(end[key]), ref=col, text=label, yshift={"metaflow":10,"skypilot":-10}.get(p,0)))
n0=len(fig.layout.annotations)
for a in LBL:
    fig.add_annotation(x=a["x"], y=math.log10(a["y"]), xref="x" if a["ref"]==1 else "x2", yref="y" if a["ref"]==1 else "y2",
        text=a["text"], xanchor="left", xshift=6, yshift=a["yshift"], showarrow=False, font=dict(size=11, color="#52514e"))
logu={f"annotations[{n0+k}].y":math.log10(a["y"]) for k,a in enumerate(LBL)}
linu={f"annotations[{n0+k}].y":a["y"] for k,a in enumerate(LBL)}
ink, muted, grid, base, surf = "#0b0b0b","#898781","#e1e0d9","#c3c2b7","#fcfcfb"
fig.update_layout(template="plotly_white", paper_bgcolor=surf, plot_bgcolor=surf, height=560,
    font=dict(family="Inter, system-ui, sans-serif", color=ink, size=12), hovermode="x unified",
    title=dict(text="PyPI downloads, cumulative — Nov 2022 → Nov 2026", x=0.01, font=dict(size=18)),
    legend=dict(orientation="h", y=-0.12, x=0), margin=dict(l=60,r=90,t=110,b=110),
    updatemenus=[dict(type="buttons", direction="right", x=1, xanchor="right", y=1.17, showactive=True,
        bgcolor="#f9f9f7", bordercolor=base, font=dict(color=ink),
        buttons=[dict(label="Log", method="relayout", args=[{"yaxis.type":"log","yaxis2.type":"log",**logu}]),
                 dict(label="Linear", method="relayout", args=[{"yaxis.type":"linear","yaxis2.type":"linear",**linu}])])],
    annotations=list(fig.layout.annotations)+[dict(xref="paper", yref="paper", x=0, y=-0.27, showarrow=False, xanchor="left", align="left",
        font=dict(size=11,color=muted), text=f"Source: ClickHouse public PyPI dataset (pypi.pypi_downloads_per_month). Nov 2026 is partial (data through Nov 21).<br>"
        f"Flyte v2 (PyPI 'flyte') starts May 2025 at the first Flyte 2 SDK prerelease; {excluded:,} downloads of a Mar 2025 placeholder upload (0.1.0) are excluded.")])
for ax in ("xaxis","xaxis2"):
    fig.layout[ax].update(range=["2022-10-15","2027-07-01"], showgrid=False, linecolor=base, tickfont=dict(color=muted), dtick="M6", tickformat="%b %Y")
for ax in ("yaxis","yaxis2"):
    fig.layout[ax].update(type="log", gridcolor=grid, linecolor=base, tickfont=dict(color=muted), exponentformat="SI", zeroline=False)
fig.write_image(f"{OUT}/downloads.png", width=1400, height=620, scale=2)

dark_js = f"""
var mq=window.matchMedia('(prefers-color-scheme: dark)');
function theme(){{var g=document.querySelector('.plotly-graph-div'); if(!g)return; var dk=mq.matches; var L={json.dumps(LIGHT)},D={json.dumps(DARK)};
 document.body.style.background=dk?'#0d0d0d':'#f9f9f7';
 var idx=[],cols=[],tidx=[]; g.data.forEach(function(t,k){{ if(t.mode==='text'){{return;}} var i=L.indexOf(t.line.color); if(i<0)i=D.indexOf(t.line.color); idx.push(k); cols.push((dk?D:L)[i]);}});
 Plotly.restyle(g,{{'line.color':cols}},idx);
 var u={{paper_bgcolor:dk?'#1a1a19':'#fcfcfb',plot_bgcolor:dk?'#1a1a19':'#fcfcfb','font.color':dk?'#f0efec':'#0b0b0b',
  'yaxis.gridcolor':dk?'#2c2c2a':'#e1e0d9','yaxis2.gridcolor':dk?'#2c2c2a':'#e1e0d9','updatemenus[0].font.color':'#52514e','updatemenus[0].bgcolor':dk?'#c3c2b7':'#f9f9f7'}};
 ['xaxis','xaxis2','yaxis','yaxis2'].forEach(function(a){{u[a+'.linecolor']=dk?'#383835':'#c3c2b7';}});
 
 g.layout.annotations.forEach(function(a,i){{ if(a.font&&a.font.color&&a.font.color!=='#898781') u['annotations['+i+'].font.color']=dk?'#c3c2b7':'#52514e'; }});
 Plotly.relayout(g,u);}}
theme(); mq.addEventListener('change',theme);"""
fig.write_html(f"{OUT}/downloads.html", include_plotlyjs=True, full_html=True, post_script=dark_js,
    config={"displaylogo":False,"responsive":True}, default_width="100%", default_height="560px")
# summary
print(f"Data through {last:%Y-%m} (partial). Flyte v2 excluded placeholder downloads: {excluded:,}\n")
print(f"{'package':16}{'lifetime':>15}{'since Nov 2022':>16}{'last 12 mo':>14}{'prior 12 mo':>14}{'YoY':>8}{'cum. growth 12mo':>18}")
# last 12 complete months = Nov 2025..Oct 2026
end = last - pd.DateOffset(months=1); s12 = end - pd.DateOffset(months=11); p12s = s12 - pd.DateOffset(months=12)
for p,label in PKGS:
    g=d[d.package==p].set_index("month")
    l12=g.loc[s12:end].downloads.sum(); pr=g.loc[p12s:s12-pd.DateOffset(months=1)].downloads.sum()
    cum_end=g.lifetime_cumulative.loc[end]; cum_start=g.lifetime_cumulative.loc[:s12-pd.DateOffset(months=1)]
    cum_start=cum_start.iloc[-1] if len(cum_start) else 0
    yoy = f"{l12/pr-1:+.0%}" if pr else "n/a"
    cg = f"{cum_end/cum_start:.1f}x" if cum_start else "n/a"
    print(f"{label+' ('+p+')':16}{g.lifetime_cumulative.iloc[-1]:>15,}{g.window_cumulative.iloc[-1]:>16,}{l12:>14,}{pr:>14,}{yoy:>8}{cg:>18}")
