from django.db import models


class Deal(models.Model):
    """One underwritten property. Row of record for the pipeline page.

    Mirrors deal_meta.json (gui/deal_manager.build_deal_meta) so existing
    deal folders import losslessly. Floats, not Decimals: this is display
    metadata sourced from a float pipeline, not accounting.
    """

    deal_id = models.SlugField(max_length=120, unique=True)
    property_name = models.CharField(max_length=200)
    city = models.CharField(max_length=100, blank=True, default="")
    state = models.CharField(max_length=2, blank=True, default="")
    asset_type = models.CharField(max_length=60, blank=True, default="")
    nrsf = models.FloatField(null=True, blank=True)
    acreage = models.FloatField(null=True, blank=True)
    asking_price = models.FloatField(null=True, blank=True)
    estimated_fair_value = models.FloatField(null=True, blank=True)
    recommendation = models.CharField(max_length=40, blank=True, default="N/A")
    analysis_date = models.DateField(null=True, blank=True)
    deal_dir = models.CharField(max_length=500, blank=True, default="")
    memo_filename = models.CharField(max_length=300, blank=True, default="")
    excel_filename = models.CharField(max_length=300, blank=True, default="")
    input_files = models.JSONField(default=list, blank=True)

    # ── Phase 3: web upload + extraction state ──
    # ""=imported (no snapshot), then pending → running → done|failed.
    extract_status = models.CharField(max_length=10, blank=True, default="")
    extract_requested_at = models.DateTimeField(null=True, blank=True)
    extract_error = models.TextField(blank=True, default="")
    extract_warnings = models.JSONField(default=list, blank=True)
    cim_json = models.JSONField(null=True, blank=True)
    extraction_report = models.JSONField(null=True, blank=True)
    assumption_overrides = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-analysis_date", "-created_at"]

    def __str__(self):
        return self.property_name


class AnalysisRun(models.Model):
    """One execution of the analysis pipeline against a Deal's snapshot
    + overrides. Append-only: each Run Analysis click creates a row, the
    worker writes only its own row, the UI shows the newest done run.
    """

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="runs")
    # running → done | failed (no pending: the row is created at start time)
    status = models.CharField(max_length=10, default="running")
    progress_step = models.IntegerField(default=0)
    progress_total = models.IntegerField(default=9)
    progress_msg = models.CharField(max_length=200, blank=True, default="")
    result_json = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    memo_filename = models.CharField(max_length=300, blank=True, default="")
    excel_filename = models.CharField(max_length=300, blank=True, default="")
    template_filename = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.deal_id}:{self.pk} {self.status}"
