"""Report rendering — JSON + Markdown artifacts under the artifact dir."""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sre_agent.models.incident import IncidentState
from sre_agent.models.report import IncidentReport

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(enabled_extensions=()),
    trim_blocks=True,
    lstrip_blocks=True,
)


def write_report(report: IncidentReport, state: IncidentState, artifact_dir: Path) -> tuple[Path, Path]:
    """Write report.json + report.md; also dump full incident_state.json."""
    out_dir = artifact_dir / state.incident_id
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "report.json"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )

    md_path = out_dir / "report.md"
    md_path.write_text(
        _env.get_template("incident_report.md.j2").render(report=report, state=state),
        encoding="utf-8",
    )

    state_path = out_dir / "incident_state.json"
    state_path.write_text(
        json.dumps(state.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    return json_path, md_path
