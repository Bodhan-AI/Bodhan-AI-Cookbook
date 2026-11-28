"""JARVIS charts. Usage:

    from charts import plot, list_charts
    plot("bar", x=["A","B"], y=[3,5], title="Sales")

or call any function directly, e.g. charts.line_chart(x, y).
Every chart accepts: title, show=True, save=None ('file.html' / '.png'), width, height.
"""
from .line_charts import line_chart, area_chart, band_chart
from .bar_charts import bar_chart, lollipop_chart, waterfall_chart, pareto_chart
from .distribution_charts import histogram_chart, box_plot, violin_plot
from .relationship_charts import scatter_plot, bubble_chart, heatmap_chart, correlation_matrix
from .part_of_whole_charts import pie_chart, donut_chart, treemap_chart, sunburst_chart, funnel_chart
from .special_charts import candlestick_chart, gauge_chart, radar_chart, sankey_chart, gantt_chart

REGISTRY = {
    "line": line_chart, "area": area_chart, "band": band_chart,
    "bar": bar_chart, "lollipop": lollipop_chart, "waterfall": waterfall_chart,
    "pareto": pareto_chart,
    "histogram": histogram_chart, "box": box_plot, "violin": violin_plot,
    "scatter": scatter_plot, "bubble": bubble_chart, "heatmap": heatmap_chart,
    "correlation": correlation_matrix,
    "pie": pie_chart, "donut": donut_chart, "treemap": treemap_chart,
    "sunburst": sunburst_chart, "funnel": funnel_chart,
    "candlestick": candlestick_chart, "gauge": gauge_chart, "radar": radar_chart,
    "sankey": sankey_chart, "gantt": gantt_chart,
}


def list_charts():
    """Return the names accepted by plot()."""
    return sorted(REGISTRY)


def plot(kind, **kwargs):
    """Single entry point: plot('line', x=[...], y=[...], title='...'). Returns the Figure."""
    try:
        fn = REGISTRY[kind.lower()]
    except KeyError:
        raise ValueError(f"Unknown chart '{kind}'. Available: {', '.join(list_charts())}")
    return fn(**kwargs)
