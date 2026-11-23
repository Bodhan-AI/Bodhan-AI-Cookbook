"""Line and area charts."""
import plotly.graph_objects as go
from ._common import as_series, finish


def line_chart(x, y, title="Line chart", x_label=None, y_label=None, smooth=False,
               markers=True, show=True, save=None, **kw):
    """x: list. y: list OR {series_name: list} for several lines."""
    fig = go.Figure()
    for name, vals in as_series(y).items():
        fig.add_scatter(x=x, y=vals, name=name, mode="lines+markers" if markers else "lines",
                        line=dict(width=3, shape="spline" if smooth else "linear"),
                        marker=dict(size=7))
    fig.update_layout(hovermode="x unified", showlegend=len(fig.data) > 1)
    return finish(fig, title, x_label, y_label, show, save, **kw)


def area_chart(x, y, title="Area chart", x_label=None, y_label=None, stacked=False,
               show=True, save=None, **kw):
    """x: list. y: list OR {series_name: list}. stacked=True stacks the areas."""
    fig = go.Figure()
    for name, vals in as_series(y).items():
        fig.add_scatter(x=x, y=vals, name=name, mode="lines", line=dict(width=2),
                        stackgroup="one" if stacked else None,
                        fill="tonexty" if stacked else "tozeroy")
    fig.update_layout(hovermode="x unified", showlegend=len(fig.data) > 1)
    return finish(fig, title, x_label, y_label, show, save, **kw)


def band_chart(x, y, lower, upper, title="Line with confidence band", x_label=None,
               y_label=None, show=True, save=None, **kw):
    """Single line y with a shaded band between lower and upper (lists)."""
    fig = go.Figure()
    fig.add_scatter(x=list(x) + list(x)[::-1], y=list(upper) + list(lower)[::-1],
                    fill="toself", fillcolor="rgba(76,120,255,0.18)",
                    line=dict(width=0), hoverinfo="skip", name="range")
    fig.add_scatter(x=x, y=y, name="value", mode="lines", line=dict(width=3))
    fig.update_layout(showlegend=False)
    return finish(fig, title, x_label, y_label, show, save, **kw)
