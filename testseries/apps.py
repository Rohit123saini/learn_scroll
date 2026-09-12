# testseries/apps.py
from django.apps import AppConfig
 
 
class TestseriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "testseries"
    verbose_name = "Test Series"