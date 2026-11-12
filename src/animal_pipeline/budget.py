"""Deterministic footage budget for planning.

Computed before any LLM call: which rights-cleared assets verify this
subject, how many usable seconds exist, and which production profile the
media can honestly support. The LLM receives these constraints verbatim;
save_plan enforces them again deterministically.
"""
from __future__ import annotations

import sqlite3

from .core import content, settings
from .media import rights_errors

PROFILES = ("NEEDS_MEDIA", "MICRO", "STANDARD", "EXTENDED")


def species_match(topic_species: str, asset_species: str) -> str | None:
    if asset_species == topic_species:
        return "exact"
    if asset_species in (content().get("taxonomy") or {}).get(topic_species, ()):
        return "derived"
    return None


def select_profile(budget_data: dict) -> dict:
    usable = float(budget_data.get("total_usable_s", 0.0))
    render, footage = settings()["render"], settings()["footage"]
    if usable >= render["min_duration_s"]:
        return {"profile": "EXTENDED", "needs_media": False,
                "target_s": min(usable, render["max_duration_s"]),
                "reason": f"{usable:.1f}s usable footage supports the extended profile"}
    if usable >= footage["standard_min_s"]:
        return {"profile": "STANDARD", "needs_media": False, "target_s": usable,
                "reason": f"STANDARD profile: {usable:.1f}s usable footage is below "
                          f"the {render['min_duration_s']:.0f}s extended floor"}
    if usable >= footage["micro_min_s"]:
        return {"profile": "MICRO", "needs_media": False, "target_s": usable,
                "reason": f"MICRO profile: only {usable:.1f}s usable footage is available"}
    return {"profile": "NEEDS_MEDIA", "needs_media": True, "target_s": usable,
            "reason": f"{usable:.1f}s usable footage is below the "
                      f"{footage['micro_min_s']:.0f}s micro floor; import more "
                      "licensed media or eligible visual supplements"}


def _crop(asset: sqlite3.Row, render: dict) -> str:
    if asset["height"] <= 0 or asset["width"] / asset["height"] < render["width"] / render["height"]:
        return "near-vertical source; minimal vertical crop"
    return "landscape source; cover-crop to 9:16, sides cropped"


def footage_budget(conn: sqlite3.Connection, topic_id: str, *,
                   simulation: bool = False) -> dict:
    topic = conn.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
    if not topic:
        raise ValueError("unknown topic")
    render, footage_cfg = settings()["render"], settings()["footage"]
    assets, excluded = [], []
    for asset in conn.execute("SELECT * FROM assets ORDER BY created_at"):
        match = species_match(topic["species"], asset["species"])
        reasons = []
        if match is None:
            reasons.append("species tag cannot verify this subject")
        reasons.extend(rights_errors(asset, publishable=not simulation))
        if reasons:
            excluded.append({"asset_id": asset["id"], "species": asset["species"],
                             "reasons": sorted(set(reasons))})
            continue
        duration = float(asset["duration"])
        assets.append({"asset_id": asset["id"], "species": asset["species"],
                       "species_match": match, "behavior": asset["behavior"],
                       "in_s": 0.0, "out_s": duration, "duration": duration,
                       "usable_s": duration, "width": asset["width"],
                       "height": asset["height"], "crop": _crop(asset, render),
                       "rights_ok": True})
    total = sum(a["usable_s"] for a in assets)
    selection = select_profile({"total_usable_s": total})
    return {"topic_id": topic_id, "topic_species": topic["species"],
            "assets": assets, "excluded": excluded, "total_usable_s": total,
            **selection,
            "limits": {"max_shot_s": footage_cfg["max_shot_s"], "max_total_s": total},
            "narration": {"estimated_wps": footage_cfg["narration_wps"],
                          "estimated_max_words": int(selection["target_s"] *
                                                     footage_cfg["narration_wps"])},
            "visual_supplements": [],
            "required_review": ["operator confirms species and subject crop visually",
                                "operator confirms semantic citation support",
                                "rights evidence per asset already verified"]}
