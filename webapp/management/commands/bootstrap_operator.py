"""Idempotently create the operator account(s) from ALLOWED_EMAILS.

Run once at deploy: password comes from OPERATOR_PASSWORD env if set,
otherwise the account is created with an unusable password (set later
via `manage.py changepassword`).
"""
import os

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create operator superuser accounts from settings.ALLOWED_EMAILS"

    def handle(self, *args, **options):
        password = os.environ.get("OPERATOR_PASSWORD")
        for email in settings.ALLOWED_EMAILS:
            user, created = User.objects.get_or_create(
                username=email, defaults={"email": email, "is_staff": True,
                                          "is_superuser": True},
            )
            if created:
                if password:
                    user.set_password(password)
                else:
                    user.set_unusable_password()
                user.save()
                self.stdout.write(f"created {email}")
            else:
                # Reconcile, don't just report: a row created any other way
                # (createsuperuser, interrupted run) must end up with the
                # operator flags, or this stops being the enforcement point.
                changed = False
                if not (user.is_staff and user.is_superuser):
                    user.is_staff = True
                    user.is_superuser = True
                    changed = True
                if user.email != email:
                    user.email = email
                    changed = True
                if changed:
                    user.save()
                    self.stdout.write(f"updated {email}")
                else:
                    self.stdout.write(f"exists {email}")
