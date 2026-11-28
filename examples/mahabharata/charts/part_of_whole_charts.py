"""Composition / hierarchy / funnel charts."""
import plotly.graph_objects as go
from ._common import finish


def pie_chart(labels, values, title="Pie chart", donut=False, show=True, save=None, **kw):
    """labels: list of names, values: list of numbers. donut=True makes a ring."""
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.55 if donut else 0,
                           textinfo="label+percent", sort=False,
                           marker=dict(line=dict(color="white", width=2))))
    fig.update_layout(showlegend=False)
    return finish(fig, title, None, None, show, save, **kw)


def donut_chart(labels, values, title="Donut chart", show=True, save=None, **kw):
    """Same args as pie_chart."""
    return pie_chart(labels, values, title, donut=True, show=show, save=save, **kw)


def treemap_chart(labels, parents, values, title="Treemap", show=True, save=None, **kw):
    """Three equal-length lists. parents: parent label per node ('' for roots)."""
    fig = go.Figure(go.Treemap(labels=labels, parents=parents, values=values,
                               branchvalues="total", textinfo="label+value",
                               marker=dict(cornerradius=5)))
    return finish(fig, title, None, None, show, save, **kw)


def sunburst_chart(labels, parents, values, title="Sunburst", show=True, save=None, **kw):
    """Same args as treemap_chart."""
    fig = go.Figure(go.Sunburst(labels=labels, parents=parents, values=values,
                                branchvalues="total"))
    return finish(fig, title, None, None, show, save, **kw)


def funnel_chart(stages, values, title="Funnel chart", show=True, save=None, **kw):
    """stages: step names (top to bottom). values: numbers."""
    fig = go.Figure(go.Funnel(y=stages, x=values, textinfo="value+percent initial"))
    return finish(fig, title, None, None, show, save, **kw)
