"""Shared theme + helpers used by every chart module."""
import plotly.graph_objects as go
import plotly.io as pio

PALETTE = ["#4C78FF", "#FF7A59", "#2EC4B6", "#FFC857", "#9B5DE5",
           "#EF476F", "#06D6A0", "#8D99AE", "#F15BB5", "#00B4D8"]

_template = go.layout.Template()
_template.layout = go.Layout(
    font=dict(family="Inter, Helvetica, Arial, sans-serif", size=14, color="#1F2937"),
    colorway=PALETTE,
    paper_bgcolor="white",
    plot_bgcolor="white",
    title=dict(x=0.02, xanchor="left", font=dict(size=22)),
    xaxis=dict(showgrid=False, linecolor="#CBD5E1", ticks="outside", tickcolor="#CBD5E1", zeroline=False),
    yaxis=dict(gridcolor="#EEF2F7", zeroline=False, linecolor="#CBD5E1"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    hoverlabel=dict(font_size=13),
    margin=dict(l=60, r=30, t=80, b=60),
)
pio.templates["jarvis"] = _template
pio.templates.default = "jarvis"


def as_series(y, default_name="value"):
    """Normalise y into {name: list}. Accepts a list or a {name: list} dict."""
    if isinstance(y, dict):
        return {str(k): list(v) for k, v in y.items()}
    return {default_name: list(y)}


def finish(fig, title="", x_label=None, y_label=None, show=True, save=None,
           width=None, height=None, theme_dark=False):
    """Apply common layout options, optionally save (.html/.png/.svg/.pdf) and show."""
    fig.update_layout(title_text=title, width=width, height=height)
    if x_label is not None:
        fig.update_xaxes(title_text=x_label)
    if y_label is not None:
        fig.update_yaxes(title_text=y_label)
    if theme_dark:
        fig.update_layout(template="plotly_dark")
    if save:
        if str(save).lower().endswith(".html"):
            fig.write_html(save, include_plotlyjs="cdn")
        else:
            fig.write_image(save)  # needs: pip install kaleido
    if show:
        fig.show()
    return fig
