"""Distribution charts."""
import plotly.graph_objects as go
from ._common import as_series, finish


def histogram_chart(values, title="Histogram", x_label=None, y_label="Count", bins=None,
                    show=True, save=None, **kw):
    """values: list OR {series: list} (overlaid). bins: number of bins (optional)."""
    fig = go.Figure()
    for name, vals in as_series(values).items():
        fig.add_histogram(x=vals, name=name, nbinsx=bins, opacity=0.75)
    fig.update_layout(barmode="overlay", bargap=0.05, showlegend=len(fig.data) > 1)
    return finish(fig, title, x_label, y_label, show, save, **kw)


def box_plot(data, title="Box plot", y_label=None, show_points=True, show=True, save=None, **kw):
    """data: {group_name: list_of_numbers}."""
    fig = go.Figure()
    for name, vals in data.items():
        fig.add_box(y=vals, name=name, boxpoints="outliers" if show_points else False,
                    line=dict(width=2))
    fig.update_layout(showlegend=False)
    return finish(fig, title, None, y_label, show, save, **kw)


def violin_plot(data, title="Violin plot", y_label=None, show=True, save=None, **kw):
    """data: {group_name: list_of_numbers}."""
    fig = go.Figure()
    for name, vals in data.items():
        fig.add_violin(y=vals, name=name, box_visible=True, meanline_visible=True, opacity=0.8)
    fig.update_layout(showlegend=False)
    return finish(fig, title, None, y_label, show, save, **kw)
