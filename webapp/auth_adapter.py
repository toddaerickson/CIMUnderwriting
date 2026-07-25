from allauth.account.adapter import DefaultAccountAdapter


class ClosedSignupAdapter(DefaultAccountAdapter):
    """Single-operator system: no self-serve signup, ever.

    This adapter only closes signup. ALLOWED_EMAILS is enforced at
    account creation time — `manage.py bootstrap_operator` is the sole
    path that creates accounts, and it reads that allowlist. If a social
    provider is ever enabled, add explicit login-time enforcement here;
    do not assume this class already does it.
    """

    def is_open_for_signup(self, request):
        return False
