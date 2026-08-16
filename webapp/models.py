from django.db import models


class Deal(models.Model):
    """One underwritten property. Row of record for the pipeline page.

    Mirrors deal_meta.json (webapp.services.build_deal_meta) so existing
    deal folders import losslessly. Floats, not Decimals: this is display
    metadata sourced from a float pipeline, not accounting.
    """

    deal_id = models.SlugField(max_length=200, unique=True)
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

    # Portfolio detection. A CIM describing more than one property must be
    # flagged, not silently underwritten as a single asset. Set at
    # extraction from CIMData.portfolio_signal; evidence is a list of the
    # human-readable reasons that tripped the detector.
    portfolio_suspect = models.BooleanField(default=False)
    portfolio_evidence = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # F() ordering: NULL analysis_date sorts FIRST on Postgres under
        # plain "-analysis_date" but LAST on SQLite — pin nulls_last so
        # undated deals don't jump to the top at the Neon cutover.
        ordering = [models.F("analysis_date").desc(nulls_last=True),
                    "-created_at"]

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
    # Snapshot of the deltas this run actually used: {"config": {dotted
    # key: value}, "assumptions": <Deal.assumption_overrides at run
    # time>}. Written at run start so even failed runs record what they
    # attempted — this is how past analyses keep the thresholds they
    # ran under regardless of later ConfigOverride edits.
    applied_overrides = models.JSONField(null=True, blank=True)
    memo_filename = models.CharField(max_length=300, blank=True, default="")
    excel_filename = models.CharField(max_length=300, blank=True, default="")
    template_filename = models.CharField(max_length=300, blank=True, default="")
    investor_summary_filename = models.CharField(max_length=300, blank=True,
                                                 default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"run {self.pk} ({self.status})"


class ConfigOverride(models.Model):
    """One delta from a config.py threshold. Append-mostly: to change a
    value going forward, add a new row with a later effective_date; the
    resolver picks per key the asset-specific-then-latest row. Values are
    stored in canonical config units (decimals, [low, high] lists).
    """

    key = models.CharField(max_length=80)          # dotted path, e.g. "GATES.min_irr_5yr"
    value = models.JSONField()                     # number or [low, high]
    asset_type = models.CharField(max_length=60, blank=True, default="")  # "" = all
    effective_date = models.DateField()
    note = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["key", "asset_type", "-effective_date", "-pk"]

    def __str__(self):
        scope = self.asset_type or "all"
        return f"{self.key} [{scope}] from {self.effective_date}"
