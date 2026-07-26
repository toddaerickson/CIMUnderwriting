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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-analysis_date", "-created_at"]

    def __str__(self):
        return self.property_name
