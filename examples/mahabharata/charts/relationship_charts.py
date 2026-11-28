"""Relationship charts: scatter, bubble, heatmap, correlation."""
import plotly.graph_objects as go
from ._common import finish


def scatter_plot(x, y, title="Scatter plot", x_label=None, y_label=None, color=None,
                 labels=None, trendline=False, show=True, save=None, **kw):
    """x, y: lists. color: optional list of numbers/categories. labels: hover text list.
    trendline=True adds a least-squares line."""
    fig = go.Figure()
    marker = dict(size=10, opacity=0.8, line=dict(width=1, color="white"))
    if color is not None:
        marker.update(color=color, colorscale="Viridis", showscale=True)
    fig.add_scatter(x=x, y=y, mode="markers", text=labels, marker=marker, name="data")
    if trendline:
        n = len(x)
        mx, my = sum(x) / n, sum(y) / n
        denom = sum((a - mx) ** 2 for a in x) or 1
        slope = sum((a - mx) * (b - my) for a, b in zip(x, y)) / denom
        b0 = my - slope * mx
        xs = [min(x), max(x)]
        fig.add_scatter(x=xs, y=[slope * v + b0 for v in xs], mode="lines",
                        line=dict(dash="dash", width=2, color="#FF7A59"), name="trend")
    fig.update_layout(showlegend=trendline)
    return finish(fig, title, x_label, y_label, show, save, **kw)


def bubble_chart(x, y, size, title="Bubble chart", x_label=None, y_label=None,
                 labels=None, color=None, show=True, save=None, **kw):
    """Like scatter, but size (list of numbers) controls bubble area."""
    mx = max(size) or 1
    marker = dict(size=size, sizemode="area", sizeref=2 * mx / (60 ** 2), sizemin=4,
                  opacity=0.7, line=dict(width=1, color="white"))
    if color is not None:
        marker.update(color=color, colorscale="Viridis", showscale=True)
    fig = go.Figure(go.Scatter(x=x, y=y, mode="markers", text=labels, marker=marker))
    return finish(fig, title, x_label, y_label, show, save, **kw)


def heatmap_chart(z, x=None, y=None, title="Heatmap", x_label=None, y_label=None,
                  colorscale="Blues", show_values=True, show=True, save=None, **kw):
    """z: 2-D list (rows). x: column labels. y: row labels."""
    fig = go.Figure(go.Heatmap(z=z, x=x, y=y, colorscale=colorscale,
                               text=z if show_values else None,
                               texttemplate="%{text}" if show_values else None,
                               xgap=2, ygap=2))
    fig.update_yaxes(autorange="reversed")
    return finish(fig, title, x_label, y_label, show, save, **kw)


def correlation_matrix(data, title="Correlation matrix", show=True, save=None, **kw):
    """data: {column_name: list_of_numbers} (equal lengths). Plots Pearson correlation."""
    names = list(data)

    def corr(a, b):
        n = len(a)
        ma, mb = sum(a) / n, sum(b) / n
        cov = sum((i - ma) * (j - mb) for i, j in zip(a, b))
        va = sum((i - ma) ** 2 for i in a)
        vb = sum((j - mb) ** 2 for j in b)
        return cov / ((va * vb) ** 0.5) if va and vb else 0.0

    z = [[round(corr(data[r], data[c]), 2) for c in names] for r in names]
    fig = go.Figure(go.Heatmap(z=z, x=names, y=names, zmin=-1, zmax=1, colorscale="RdBu",
                               reversescale=True, text=z, texttemplate="%{text}",
                               xgap=2, ygap=2))
    fig.update_yaxes(autorange="reversed")
    return finish(fig, title, None, None, show, save, **kw)
