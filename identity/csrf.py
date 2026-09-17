from django.conf import settings
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import PermissionDenied


class IdentityCSRFCheck(CSRFCheck):
    """Temporary origin override for the identity API; still validate CSRF tokens."""

    def _origin_verified(self, request):
        if settings.ALLOW_ALL_HOSTS_AND_ORIGINS:
            return True
        return super()._origin_verified(request)


def enforce_identity_csrf(request):
    check = IdentityCSRFCheck(lambda request: None)
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        raise PermissionDenied("CSRF Failed: %s" % reason)
