"""Specialised charts: candlestick, gauge, radar, sankey, gantt."""
import plotly.graph_objects as go
from ._common import as_series, finish


def candlestick_chart(dates, open, high, low, close, title="Candlestick chart",
                      y_label="Price", volume=None, show=True, save=None, **kw):
    """Five equal-length lists. volume: optional list, drawn as faint bars below."""
    fig = go.Figure(go.Candlestick(x=dates, open=open, high=high, low=low, close=close,
                                   increasing_line_color="#06D6A0",
                                   decreasing_line_color="#EF476F"))
    if volume is not None:
        fig.add_bar(x=dates, y=volume, yaxis="y2", marker_color="rgba(76,120,255,0.25)",
                    name="volume")
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False,
                                      range=[0, max(volume) * 4], showticklabels=False))
    fig.update_layout(xaxis_rangeslider_visible=False, showlegend=False)
    return finish(fig, title, None, y_label, show, save, **kw)


def gauge_chart(value, title="Gauge", min_value=0, max_value=100, target=None,
                show=True, save=None, **kw):
    """value: number. target: optional threshold marker."""
    span = max_value - min_value
    gauge = dict(axis=dict(range=[min_value, max_value]), bar=dict(color="#4C78FF"),
                 steps=[dict(range=[min_value, min_value + span * .5], color="#FDE2E4"),
                        dict(range=[min_value + span * .5, min_value + span * .8], color="#FFF1C9"),
                        dict(range=[min_value + span * .8, max_value], color="#D5F5E3")])
    if target is not None:
        gauge["threshold"] = dict(line=dict(color="#1F2937", width=4), value=target)
    fig = go.Figure(go.Indicator(mode="gauge+number", value=value, gauge=gauge))
    return finish(fig, title, None, None, show, save, **kw)


def radar_chart(categories, series, title="Radar chart", fill=True, show=True, save=None, **kw):
    """categories: axis names. series: list OR {name: list} of values per category."""
    fig = go.Figure()
    cats = list(categories) + [categories[0]]
    for name, vals in as_series(series).items():
        vals = list(vals) + [vals[0]]
        fig.add_scatterpolar(r=vals, theta=cats, name=name, fill="toself" if fill else None,
                             opacity=0.7)
    fig.update_layout(showlegend=len(fig.data) > 1)
    return finish(fig, title, None, None, show, save, **kw)


def sankey_chart(sources, targets, values, title="Sankey diagram", show=True, save=None, **kw):
    """Three equal-length lists describing flows: source name, target name, amount."""
    nodes = list(dict.fromkeys(list(sources) + list(targets)))
    idx = {n: i for i, n in enumerate(nodes)}
    fig = go.Figure(go.Sankey(
        node=dict(label=nodes, pad=20, thickness=18, line=dict(width=0)),
        link=dict(source=[idx[s] for s in sources], target=[idx[t] for t in targets],
                  value=list(values))))
    return finish(fig, title, None, None, show, save, **kw)


def gantt_chart(tasks, starts, ends, title="Gantt chart", groups=None, show=True, save=None, **kw):
    """tasks: names. starts/ends: ISO dates 'YYYY-MM-DD' (or numbers). groups: optional
    list of group names to colour by."""
    from datetime import datetime
    fig = go.Figure()
    seen = set()
    for i, (t, s, e) in enumerate(zip(tasks, starts, ends)):
        g = groups[i] if groups else "tasks"
        if isinstance(s, str):
            length = (datetime.fromisoformat(e) - datetime.fromisoformat(s)).total_seconds() * 1000
        else:
            length = e - s
        fig.add_bar(y=[t], x=[length], base=[s], orientation="h", name=g,
                    legendgroup=g, showlegend=g not in seen and bool(groups),
                    marker_cornerradius=6,
                    marker_color=None, hovertemplate=f"{t}<br>{s} → {e}<extra></extra>")
        seen.add(g)
    fig.update_layout(barmode="overlay")
    fig.update_yaxes(autorange="reversed")
    if tasks and isinstance(starts[0], str):
        fig.update_xaxes(type="date")
    return finish(fig, title, None, None, show, save, **kw)
