"""Read-only Land compatibility queries and Phase 1 analysis exports."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

from .errors import SchemaError, UpstreamError
from .land_analysis import (
    AvailabilityClassification,
    DEFAULT_PLAYER,
    Deed,
    Item,
    LandCapture,
    LandClient,
    LandItem,
    Plot,
    PlotStakingState,
    WorkerCard,
    Worksite,
    analyze_bundle,
    available_item_ids,
    classify_card,
    estimate_card_pp,
    load_snapshot,
    normalize_bundle,
    normalize_item,
    normalize_worker,
    recommend_land,
    recommendation_measure_text,
    render_land_analysis,
    replay_estimator,
    save_snapshot,
    stable_asset_ref,
)


@dataclass(frozen=True)
class LandPlot:
    pp: float
    plot: str
    region: str
    resource: str | None
    rarity: str | None
    status: str | None
    worksite: str | None
    deed_uid: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "pp": int(self.pp) if self.pp.is_integer() else self.pp,
            "plot": self.plot,
            "region": self.region,
            "resource": self.resource,
            "rarity": self.rarity,
            "status": self.status,
            "worksite": self.worksite,
            "deed_uid": self.deed_uid,
        }


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError("Land PP value is not numeric") from exc
    if number < 0:
        raise SchemaError("Land PP value is negative")
    return number


def normalize_plots(payload: dict[str, Any]) -> list[LandPlot]:
    if payload.get("status") not in (None, "success"):
        raise UpstreamError(f"Land API returned status {payload.get('status')}")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("deeds"), list) or not isinstance(data.get("staking_details"), list):
        raise SchemaError("Land deeds response must contain deeds and staking_details arrays")
    deeds = {str(item.get("deed_uid")): item for item in data["deeds"] if isinstance(item, dict) and item.get("deed_uid")}
    plots: list[LandPlot] = []
    for stake in data["staking_details"]:
        if not isinstance(stake, dict) or not stake.get("deed_uid"):
            raise SchemaError("Land staking record is missing deed_uid")
        deed_uid = str(stake["deed_uid"])
        deed = deeds.get(deed_uid)
        if deed is None:
            continue
        plots.append(LandPlot(
            pp=_number(stake.get("total_harvest_pp", 0)),
            plot=f"{deed.get('region_number')}-{deed.get('tract_number')}-{deed.get('plot_number')}",
            region=str(stake.get("region_uid") or deed.get("region_uid") or ""),
            resource=deed.get("resource_symbol"),
            rarity=deed.get("rarity"),
            status=deed.get("plot_status"),
            worksite=deed.get("worksite_type"),
            deed_uid=deed_uid,
        ))
    return plots


def fetch_land_plots(player: str, timeout: float = 20.0) -> list[LandPlot]:
    return normalize_plots(LandClient(timeout=timeout).bundle(player).data)


def rank_plots(plots: list[LandPlot], limit: int = 10) -> list[LandPlot]:
    if limit < 1:
        raise ValueError("limit must be positive")
    return sorted(plots, key=lambda plot: (-plot.pp, plot.plot, plot.deed_uid))[:limit]


def render_land(plots: list[LandPlot], fmt: str, player: str) -> str:
    rows = [plot.as_dict() for plot in plots]
    if fmt == "json":
        return json.dumps({"schema_version": 1, "player": player, "sort": "total_harvest_pp_desc", "plots": rows}, ensure_ascii=False, indent=2) + "\n"
    fields = ["pp", "plot", "region", "resource", "rarity", "status", "worksite", "deed_uid"]
    if fmt == "csv":
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue()
    headers = ["Rank", "PP", "Plot", "Region", "Resource", "Rarity", "Worksite"]
    values = [[str(index), f"{row['pp']:,.3f}", row["plot"], row["region"], str(row["resource"] or ""), str(row["rarity"] or ""), str(row["worksite"] or "")] for index, row in enumerate(rows, 1)]
    widths = [max(len(headers[i]), *(len(row[i]) for row in values)) if values else len(headers[i]) for i in range(len(headers))]
    line = " | ".join(headers[i].ljust(widths[i]) for i in range(len(headers)))
    divider = "-+-".join("-" * width for width in widths)
    body = [" | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) for row in values]
    body.append("")
    body.append(f"Top {len(rows)} combined PP: {sum(row['pp'] for row in rows):,.3f}")
    return "\n".join([line, divider, *body]) + "\n"


__all__ = [
    "AvailabilityClassification", "DEFAULT_PLAYER", "Deed", "Item", "LandCapture", "LandClient", "LandItem", "LandPlot", "Plot", "PlotStakingState", "WorkerCard", "Worksite", "analyze_bundle", "available_item_ids", "classify_card", "estimate_card_pp",
    "fetch_land_plots", "load_snapshot", "normalize_bundle", "normalize_item", "normalize_plots", "normalize_worker",
    "rank_plots", "recommend_land", "recommendation_measure_text", "render_land", "render_land_analysis", "replay_estimator", "save_snapshot", "stable_asset_ref",
]
