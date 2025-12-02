# app/agents/viz_agent.py
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, MutableMapping
import logging
import json

import openai
import plotly.graph_objects as go

from app.agents.knowledge_base import KnowledgeBase
from app.config import settings
from app.db import execute_readonly_query

logger = logging.getLogger(__name__)

try:
    # Available in openai>=1.x
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore


@dataclass
class VizPlan:
    sql: str
    chart: Dict[str, Any]
    usage: Optional[Dict[str, Any]] = None


@dataclass
class VisualizationResult:
    sql: str
    rows: List[Dict[str, Any]]
    figure: Optional[Dict[str, Any]]
    usage: Optional[Dict[str, Any]] = None


class VizAgent:
    """
    Generates SQL plus a simple chart spec, executes the query, and returns a Plotly figure.
    """

    # Simple, vivid palette to keep charts readable.
    PALETTE = ["#2563eb", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#0ea5e9"]

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.model = settings.openai_model
        if OpenAI:
            self.client = OpenAI(api_key=settings.openai_api_key)
            self.use_client = True
        else:
            openai.api_key = settings.openai_api_key
            self.client = None
            self.use_client = False

    def _plan(self, question: str) -> VizPlan:
        schema_text = self.kb.as_schema_text()
        fq_view = settings.allowed_fq_view
        system_prompt = f"""
You are a data visualization assistant. Given a user question, return JSON with two keys:
- "sql": a safe SELECT that answers the question, querying ONLY from {fq_view}
- "chart": an object with keys:
    - "type": one of ["bar","line","scatter","pie"]
    - "x": column name for x-axis (or category for pie)
    - "y": column name for y-axis (not needed for pie)
    - "title": short chart title

Rules:
- Only SELECT; no DDL/DML.
- Use fully qualified view name: {fq_view}
- Keep result sets small; include LIMIT when appropriate.

View schema:
{schema_text}
Respond with ONLY the JSON object.
""".strip()

        logger.info("Planning visualization for question: %s", question)
        usage: Optional[Dict[str, Any]] = None

        if self.use_client:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0.1,
            )
            content = resp.choices[0].message.content.strip()
            if getattr(resp, "usage", None):
                usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
        else:
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0.1,
            )
            content = resp.choices[0].message["content"].strip()
            if resp.get("usage"):
                usage = resp["usage"]

        # sanitize markdown fences if present
        if content.startswith("```"):
            content = content.strip("`")
            content = content.replace("json\n", "").replace("JSON\n", "").strip()

        try:
            plan_obj = json.loads(content)
        except Exception as exc:
            logger.exception("Failed to parse visualization plan JSON: %s", content)
            raise ValueError(f"Failed to parse visualization plan: {exc}")

        if "sql" not in plan_obj or "chart" not in plan_obj:
            raise ValueError("Visualization plan must include 'sql' and 'chart' keys.")

        return VizPlan(sql=plan_obj["sql"], chart=plan_obj["chart"], usage=usage)

    def _build_figure(self, rows: List[Dict[str, Any]], chart: Dict[str, Any]) -> Dict[str, Any]:
        chart_type = (chart.get("type") or "bar").lower()
        x_col = chart.get("x")
        y_col = chart.get("y")
        title = chart.get("title") or "Visualization"

        if not rows:
            raise ValueError("Visualization query returned no data to plot.")

        if chart_type not in {"bar", "line", "scatter", "pie"}:
            raise ValueError(f"Unsupported chart type: {chart_type}")
        if not x_col:
            raise ValueError("Chart spec missing x axis.")
        if chart_type != "pie" and not y_col:
            raise ValueError("Chart spec missing y axis.")

        def extract(col: str) -> List[Any]:
            try:
                return [r[col] for r in rows]
            except KeyError as exc:
                raise ValueError(f"Chart column not in result set: {col}") from exc

        if chart_type == "bar":
            fig = go.Figure(data=go.Bar(x=extract(x_col), y=extract(y_col)))
        elif chart_type == "line":
            fig = go.Figure(data=go.Scatter(x=extract(x_col), y=extract(y_col), mode="lines"))
        elif chart_type == "scatter":
            fig = go.Figure(data=go.Scatter(x=extract(x_col), y=extract(y_col), mode="markers"))
        elif chart_type == "pie":
            fig = go.Figure(data=go.Pie(labels=extract(x_col), values=extract(y_col or x_col)))

        # Styling for a clean white background and consistent aesthetic.
        fig.update_layout(
            title={"text": title, "x": 0.05, "xanchor": "left", "font": {"size": 18}},
            template="plotly_white",
            plot_bgcolor="white",
            paper_bgcolor="white",
            font={"family": "Inter, Arial, sans-serif", "size": 14, "color": "#0f172a"},
            margin={"l": 60, "r": 30, "t": 60, "b": 50},
            hovermode="x unified" if chart_type in {"bar", "line"} else "closest",
        )

        if chart_type == "bar":
            colors = (self.PALETTE * ((len(rows) // len(self.PALETTE)) + 1))[: len(rows)]
            fig.update_traces(marker_color=colors, marker_line_width=0.8, marker_line_color="#0f172a")
        elif chart_type in {"line", "scatter"}:
            fig.update_traces(line={"width": 2.4, "color": self.PALETTE[0]}, marker={"size": 8, "color": self.PALETTE[1]})
        elif chart_type == "pie":
            colors = (self.PALETTE * ((len(rows) // len(self.PALETTE)) + 1))[: len(rows)]
            fig.update_traces(marker={"colors": colors, "line": {"width": 0.8, "color": "#0f172a"}}, textinfo="label+percent")

        return fig.to_dict()

    def generate_viz(self, question: str, execute: bool = True, cache: Optional[MutableMapping[str, List[Dict[str, Any]]]] = None) -> VisualizationResult:
        plan = self._plan(question)

        if not execute:
            return VisualizationResult(sql=plan.sql, rows=[], figure=None, usage=plan.usage)

        if cache is not None and plan.sql in cache:
            rows = cache[plan.sql]
        else:
            rows = execute_readonly_query(plan.sql)
            if cache is not None:
                cache[plan.sql] = rows
        figure = self._build_figure(rows, plan.chart)
        return VisualizationResult(sql=plan.sql, rows=rows, figure=figure, usage=plan.usage)
