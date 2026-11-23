"""Bar-style charts."""
import plotly.graph_objects as go
from ._common import as_series, finish


def bar_chart(x, y, title="Bar chart", x_label=None, y_label=None, mode="group",
              horizontal=False, show_values=True, show=True, save=None, **kw):
    """x: category list. y: list OR {series: list}.
    mode: 'group' | 'stack' | 'relative'. horizontal=True flips axes."""
    fig = go.Figure()
    for name, vals in as_series(y).items():
        fig.add_bar(x=vals if horizontal else x, y=x if horizontal else vals, name=name,
                    orientation="h" if horizontal else "v",
                    text=vals if show_values else None, textposition="auto",
                    marker_cornerradius=6)
    fig.update_layout(barmode=mode, showlegend=len(fig.data) > 1)
    return finish(fig, title, x_label, y_label, show, save, **kw)


def lollipop_chart(x, y, title="Lollipop chart", x_label=None, y_label=None,
                   show=True, save=None, **kw):
    """x: category list, y: numbers."""
    fig = go.Figure()
    for xi, yi in zip(x, y):
        fig.add_scatter(x=[xi, xi], y=[0, yi], mode="lines", line=dict(color="#CBD5E1", width=3),
                        hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=x, y=y, mode="markers", marker=dict(size=14), showlegend=False)
    return finish(fig, title, x_label, y_label, show, save, **kw)


def waterfall_chart(labels, values, title="Waterfall chart", y_label=None,
                    show_total=True, show=True, save=None, **kw):
    """labels: step names. values: signed changes (+/-). Adds a final total bar."""
    measure = ["relative"] * len(values)
    labels = list(labels)
    values = list(values)
    if show_total:
        labels.append("Total")
        values.append(0)
        measure.append("total")
    fig = go.Figure(go.Waterfall(x=labels, y=values, measure=measure,
                                 increasing=dict(marker_color="#06D6A0"),
                                 decreasing=dict(marker_color="#EF476F"),
                                 totals=dict(marker_color="#4C78FF"),
                                 connector=dict(line=dict(color="#CBD5E1"))))
    return finish(fig, title, None, y_label, show, save, **kw)


def pareto_chart(categories, values, title="Pareto chart", y_label=None,
                 show=True, save=None, **kw):
    """Sorted bars + cumulative-percent line."""
    pairs = sorted(zip(categories, values), key=lambda p: p[1], reverse=True)
    cats, vals = [p[0] for p in pairs], [p[1] for p in pairs]
    total, run, cum = sum(vals), 0, []
    for v in vals:
        run += v
        cum.append(run / total * 100)
    fig = go.Figure()
    fig.add_bar(x=cats, y=vals, name="value", marker_cornerradius=6)
    fig.add_scatter(x=cats, y=cum, name="cumulative %", yaxis="y2", mode="lines+markers",
                    line=dict(width=3, color="#FF7A59"))
    fig.update_layout(yaxis2=dict(overlaying="y", side="right", range=[0, 105],
                                  ticksuffix="%", showgrid=False))
    return finish(fig, title, None, y_label, show, save, **kw)
