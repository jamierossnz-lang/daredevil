from allauth.account.adapter import DefaultAccountAdapter


class NoSignupAccountAdapter(DefaultAccountAdapter):
    """Daredevil is a single-user home server — there is no signup flow,
    ever. The only account is the superuser created via createsuperuser.
    This is what actually enforces that; allauth ships a signup view by
    default and would otherwise let anyone who reaches it create a login."""

    def is_open_for_signup(self, request):
        return False
