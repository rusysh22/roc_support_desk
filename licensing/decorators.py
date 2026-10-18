from functools import wraps
from django.shortcuts import redirect
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.urls import reverse
from .models import LicenseRecord


def has_feature_access(request, feature_name):
    """
    Single source of truth for "is this license feature usable right now".

    Used by feature_required / FeatureRequiredMixin (to gate the view) and by
    licensing.context_processors.license_context (to gate the matching sidebar
    menu item) so the two never drift apart — a menu must never link to a view
    that will bounce the user straight to the upgrade page.
    """
    license_obj = getattr(request, 'license', None)
    if not license_obj:
        license_obj = LicenseRecord.get_current()

    status = getattr(request, 'license_status', None)
    if not status:
        status = license_obj.get_effective_status()

    # Full lock (expired/suspended/unlicensed) has no premium features.
    if status in ('expired', 'suspended', 'unlicensed'):
        return False

    from .middleware import PARTIAL_LOCK_DISABLED_FEATURES

    if status in ('active', 'grace'):
        # Empty features_json on an active license = full access (marketplace may not send feature list)
        if not license_obj.features_json:
            return True
        return bool(license_obj.features_json.get(feature_name, False))
    if status == 'partial_lock':
        # Check if this feature is one that is specifically locked during partial_lock phase
        if feature_name in PARTIAL_LOCK_DISABLED_FEATURES:
            return False
        return bool(license_obj.features_json.get(feature_name, False))
    if status == 'trial':
        # Per plan, trial usually has premium features disabled or limited.
        # In our spec, trial handles basic ticketing.
        # We block premium features in trial as well for upsell pressure.
        return False

    return False


def feature_required(feature_name):
    """
    Decorator for views that requires a specific license feature to be enabled.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            # 1. Get license and status
            license_obj = getattr(request, 'license', None)
            if not license_obj:
                license_obj = LicenseRecord.get_current()

            status = getattr(request, 'license_status', None)
            if not status:
                status = license_obj.get_effective_status()

            # 2. Check basic license requirements (active, trial, grace, partial_lock)
            # Full lock (expired/suspended) should already be handled by LicenseGateMiddleware
            # but we check it here as a safety net.
            if status in ('expired', 'suspended', 'unlicensed'):
                if request.headers.get('HX-Request') or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return HttpResponseForbidden("License expired or missing.")
                return redirect('licensing:activate')

            # 3. Check for specific feature access (shared with the sidebar's gating)
            has_access = has_feature_access(request, feature_name)

            if not has_access:
                upgrade_url = reverse('licensing:upgrade')
                if request.headers.get('HX-Request'):
                    response = HttpResponse()
                    response['HX-Redirect'] = upgrade_url
                    return response
                return redirect(upgrade_url)

            return view_func(request, *args, **kwargs)
        
        # Attach the required feature name to the function so middleware can inspect it if needed
        _wrapped_view._required_feature = feature_name
        return _wrapped_view
    return decorator

def license_required(view_func):
    """
    Simpler decorator that just ensures ANY active license (including trial/grace) is present.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        status = getattr(request, 'license_status', None)
        if not status:
            status = LicenseRecord.get_current().get_effective_status()
        
        if status in ('expired', 'suspended', 'unlicensed'):
            return redirect('licensing:activate')
        
        return view_func(request, *args, **kwargs)
    return _wrapped_view

class FeatureRequiredMixin:
    """
    Mixin for Class-Based Views that require a specific license feature.
    Usage:
        class MyView(FeatureRequiredMixin, ListView):
            feature_required = 'analytics'
            ...
    """
    feature_required = None

    def dispatch(self, request, *args, **kwargs):
        if not self.feature_required:
            return super().dispatch(request, *args, **kwargs)

        # 1. Get license and status
        status = getattr(request, 'license_status', None)
        if not status:
            license_obj = getattr(request, 'license', None) or LicenseRecord.get_current()
            status = license_obj.get_effective_status()

        # 2. Check basic license requirements
        if status in ('expired', 'suspended', 'unlicensed'):
            return redirect('licensing:activate')

        # 3. Check for specific feature access (shared with the sidebar's gating)
        has_access = has_feature_access(request, self.feature_required)

        if not has_access:
            upgrade_url = reverse('licensing:upgrade')
            if request.headers.get('HX-Request'):
                response = HttpResponse()
                response['HX-Redirect'] = upgrade_url
                return response
            return redirect(upgrade_url)

        return super().dispatch(request, *args, **kwargs)
