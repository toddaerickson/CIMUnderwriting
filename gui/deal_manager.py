"""
Deal Manager — folder-based deal persistence.

Each analyzed deal gets a subfolder under deals/ containing:
  - inputs/           uploaded documents (CIM, rent roll, financials)
  - deal_meta.json    structured metadata for the deal tracker
  - *.docx, *.xlsx    analysis output files
"""

import json
import os
import re
import logging
from datetime import date

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__)) or "."
DEALS_DIR = os.environ.get(
    "CIM_DEALS_DIR",
    os.path.join(PROJECT_ROOT, "deals"),
)


def sanitize_name(name: str) -> str:
    """Convert a property name to a filesystem-safe folder name."""
    # Replace non-alphanumeric chars (except spaces) with nothing
    clean = re.sub(r"[^\w\s-]", "", name)
    # Replace whitespace runs with underscore
    clean = re.sub(r"\s+", "_", clean.strip())
    return clean or "Unknown_Property"


def create_deal_folder(property_name: str) -> str:
    """Create a deal folder under deals/ with an inputs/ subfolder.

    Returns the absolute path to the deal folder.
    If the folder already exists, returns it without error.
    """
    folder_name = sanitize_name(property_name)
    deal_folder = os.path.join(DEALS_DIR, folder_name)
    inputs_folder = os.path.join(deal_folder, "inputs")
    os.makedirs(inputs_folder, exist_ok=True)
    return deal_folder


def save_uploaded_file(deal_folder: str, uploaded_file, subfolder: str = "inputs") -> str:
    """Save a Streamlit UploadedFile to a deal subfolder.

    Args:
        deal_folder: path to the deal folder
        uploaded_file: Streamlit UploadedFile object
        subfolder: subfolder within deal_folder (default "inputs")

    Returns:
        Absolute path to the saved file.
    """
    target_dir = os.path.join(deal_folder, subfolder)
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, uploaded_file.name)
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return file_path


def write_deal_meta(deal_folder: str, meta: dict):
    """Write deal_meta.json to a deal folder."""
    meta_path = os.path.join(deal_folder, "deal_meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)


def read_deal_meta(deal_folder: str) -> dict | None:
    """Read deal_meta.json from a deal folder. Returns None if missing."""
    meta_path = os.path.join(deal_folder, "deal_meta.json")
    if not os.path.isfile(meta_path):
        return None
    with open(meta_path, "r") as f:
        return json.load(f)


def list_all_deals() -> list[dict]:
    """Scan deals/ for all deal_meta.json files.

    Returns list of dicts sorted by analysis_date descending,
    with 'deal_folder' path added to each.
    """
    if not os.path.isdir(DEALS_DIR):
        return []

    deals = []
    for entry in os.scandir(DEALS_DIR):
        if not entry.is_dir():
            continue
        meta = read_deal_meta(entry.path)
        if meta:
            meta["deal_folder"] = entry.path
            deals.append(meta)

    # Sort by analysis date descending
    deals.sort(key=lambda d: d.get("analysis_date", ""), reverse=True)
    return deals


def detect_asset_type(cim_data) -> str:
    """Determine asset type from CIM data fields."""
    brv_sf = sum(filter(None, [
        getattr(cim_data, "brv_enclosed_sf", None),
        getattr(cim_data, "brv_covered_sf", None),
        getattr(cim_data, "brv_open_sf", None),
    ]))
    if brv_sf > 0:
        return "Boat & RV Storage"

    cc_pct = getattr(cim_data, "cc_pct", None)
    if cc_pct is not None and cc_pct > 0.5:
        return "Climate-Controlled Self Storage"

    return "Self Storage"


def build_deal_meta(cim_data, result, deal_folder: str, input_files: list[str] = None) -> dict:
    """Assemble deal_meta.json content from analysis results.

    Args:
        cim_data: parsed CIM data
        result: AnalysisResult from engine
        deal_folder: path to deal folder
        input_files: list of uploaded filenames
    """
    # Estimated fair value: prefer VA max offer, fall back to static
    fair_value = None
    if result.va_max_offer and result.va_max_offer.get("max_price"):
        fair_value = result.va_max_offer["max_price"]
    elif result.max_offer and result.max_offer.get("max_price"):
        fair_value = result.max_offer["max_price"]

    recommendation = result.gate_summary.get("recommendation", "N/A") if result.gate_summary else "N/A"

    return {
        "deal_id": sanitize_name(cim_data.property_name or "Unknown").lower(),
        "property_name": cim_data.property_name or "Unknown",
        "city": cim_data.city or "",
        "state": cim_data.state or "",
        "asset_type": detect_asset_type(cim_data),
        "nrsf": cim_data.nrsf,
        "acreage": getattr(cim_data, "acreage", None),
        "asking_price": cim_data.asking_price,
        "estimated_fair_value": round(fair_value) if fair_value else None,
        "recommendation": recommendation,
        "analysis_date": date.today().isoformat(),
        "memo_path": os.path.basename(result.memo_path) if result.memo_path else "",
        "excel_path": os.path.basename(result.excel_path) if result.excel_path else "",
        "input_files": input_files or [],
    }
