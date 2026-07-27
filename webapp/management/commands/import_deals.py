"""Import existing deal folders (deal_meta.json) into Deal rows.

Idempotent: re-running updates rows in place, keyed on deal_id.
Malformed folders are reported and skipped, never fatal.
"""
import datetime
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from webapp.models import Deal


class Command(BaseCommand):
    help = "Import deals/*/deal_meta.json into the Deal table (idempotent)"

    def handle(self, *args, **options):
        root = settings.CIM_DEALS_DIR
        if not os.path.isdir(root):
            self.stdout.write(f"no deals dir at {root}; nothing to import")
            return
        imported = skipped = 0
        for name in sorted(os.listdir(root)):
            meta_path = os.path.join(root, name, "deal_meta.json")
            if not os.path.isfile(meta_path):
                continue
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                analysis_date = None
                if meta.get("analysis_date"):
                    analysis_date = datetime.date.fromisoformat(meta["analysis_date"])
                Deal.objects.update_or_create(
                    deal_id=meta["deal_id"],
                    defaults={
                        "property_name": meta.get("property_name") or "Unknown",
                        "city": meta.get("city") or "",
                        "state": meta.get("state") or "",
                        "asset_type": meta.get("asset_type") or "",
                        "nrsf": meta.get("nrsf"),
                        "acreage": meta.get("acreage"),
                        "asking_price": meta.get("asking_price"),
                        "estimated_fair_value": meta.get("estimated_fair_value"),
                        "recommendation": meta.get("recommendation") or "N/A",
                        "analysis_date": analysis_date,
                        "deal_dir": os.path.join(root, name),
                        "memo_filename": meta.get("memo_path") or "",
                        "excel_filename": meta.get("excel_path") or "",
                        "input_files": meta.get("input_files") or [],
                    },
                )
                imported += 1
            except (
                KeyError,
                ValueError,
                json.JSONDecodeError,
                TypeError,
                AttributeError,
                OSError,
            ) as e:
                skipped += 1
                self.stderr.write(f"skipped {name}: {e}")
        self.stdout.write(f"imported/updated {imported}, skipped {skipped}")
