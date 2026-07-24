import os

from django.core.exceptions import ImproperlyConfigured
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cimweb.settings")
application = get_wsgi_application()

# Fail closed at the prod entrypoint: gunicorn imports this module, so a
# deploy without a real secret refuses to boot. Tests and manage.py never
# import wsgi, so dev/CI are unaffected.
from django.conf import settings  # noqa: E402

if not settings.DEBUG and settings.SECRET_KEY == "dev-only-insecure-key":
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG=False")
