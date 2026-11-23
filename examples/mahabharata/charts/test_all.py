"""Smoke test / demo: builds every chart to ./charts/demo_output/*.html (no browser pop-ups)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from charts import plot, list_charts

out = os.path.join(os.path.dirname(__file__), "demo_output")
os.makedirs(out, exist_ok=True)
m = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
tree_l = ["All", "A", "B", "A1", "A2", "B1"]
demos = {
    "line": dict(x=m, y={"2025": [3, 5, 4, 7, 8, 9], "2026": [4, 6, 7, 9, 11, 12]}),
    "area": dict(x=m, y={"a": [3, 5, 4, 7, 8, 9], "b": [1, 2, 3, 2, 3, 4]}, stacked=True),
    "band": dict(x=m, y=[3, 5, 4, 7, 8, 9], lower=[2, 4, 3, 5, 6, 7], upper=[4, 6, 5, 9, 10, 11]),
    "bar": dict(x=["A", "B", "C"], y={"Q1": [3, 5, 2], "Q2": [4, 3, 6]}),
    "lollipop": dict(x=["A", "B", "C"], y=[3, 5, 2]),
    "waterfall": dict(labels=["Start", "Sales", "Costs", "Tax"], values=[100, 60, -40, -15]),
    "pareto": dict(categories=["Bug", "Crash", "UI", "Docs"], values=[50, 30, 15, 5]),
    "histogram": dict(values=[1, 2, 2, 3, 3, 3, 4, 4, 5, 8], bins=5),
    "box": dict(data={"a": [1, 2, 3, 4, 9], "b": [2, 3, 4, 5, 6]}),
    "violin": dict(data={"a": [1, 2, 3, 4, 9], "b": [2, 3, 4, 5, 6]}),
    "scatter": dict(x=[1, 2, 3, 4], y=[2, 4, 5, 8], trendline=True),
    "bubble": dict(x=[1, 2, 3], y=[3, 1, 2], size=[10, 40, 25], labels=["a", "b", "c"]),
    "heatmap": dict(z=[[1, 2], [3, 4]], x=["x1", "x2"], y=["y1", "y2"]),
    "correlation": dict(data={"a": [1, 2, 3, 4], "b": [2, 4, 5, 9], "c": [9, 7, 4, 1]}),
    "pie": dict(labels=["A", "B", "C"], values=[5, 3, 2]),
    "donut": dict(labels=["A", "B", "C"], values=[5, 3, 2]),
    "treemap": dict(labels=tree_l, parents=["", "All", "All", "A", "A", "B"], values=[10, 6, 4, 4, 2, 4]),
    "sunburst": dict(labels=tree_l, parents=["", "All", "All", "A", "A", "B"], values=[10, 6, 4, 4, 2, 4]),
    "funnel": dict(stages=["Visits", "Signups", "Paid"], values=[1000, 300, 60]),
    "candlestick": dict(dates=["2026-01-01", "2026-01-02", "2026-01-03"], open=[10, 12, 11],
                        high=[13, 14, 12], low=[9, 11, 9], close=[12, 11, 10], volume=[100, 200, 150]),
    "gauge": dict(value=72, target=80),
    "radar": dict(categories=["Speed", "Power", "Range", "Cost"], series={"X": [4, 3, 5, 2], "Y": [2, 5, 3, 4]}),
    "sankey": dict(sources=["A", "A", "B"], targets=["B", "C", "C"], values=[5, 3, 2]),
    "gantt": dict(tasks=["Design", "Build", "Test"], starts=["2026-01-01", "2026-01-10", "2026-02-01"],
                  ends=["2026-01-12", "2026-02-05", "2026-02-15"], groups=["p1", "p1", "p2"]),
}
assert set(demos) == set(list_charts()), set(list_charts()) ^ set(demos)
for kind, args in demos.items():
    plot(kind, title=f"{kind} demo", show=False, save=os.path.join(out, f"{kind}.html"), **args)
    print("ok", kind)
