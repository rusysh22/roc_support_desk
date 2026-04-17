from functools import wraps
from django.shortcuts import redirect
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden, JsonResponse
from django.urls import reverse
from .models import LicenseRecord

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

            # 3. Check for specific feature access
            from .middleware import PARTIAL_LOCK_DISABLED_FEATURES
            
            # During trial or unlicensed, premium features are disabled
            has_access = False
            if status in ('active', 'grace'):
                # Empty features_json on an active license = full access (marketplace may not send feature list)
                if not license_obj.features_json:
                    has_access = True
                else:
                    has_access = license_obj.features_json.get(feature_name, False)
            elif status == 'partial_lock':
                # Check if this feature is one that is specifically locked during partial_lock phase
                if feature_name in PARTIAL_LOCK_DISABLED_FEATURES:
                    has_access = False
                else:
                    has_access = license_obj.features_json.get(feature_name, False)
            elif status == 'trial':
                # Per plan, trial usually has premium features disabled or limited.
                # In our spec, trial handles basic ticketing. 
                # We block premium features in trial as well for upsell pressure.
                has_access = False

            if not has_access:
                if request.headers.get('HX-Request') or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    # If HTMX, we can return a message or trigger a client-side redirect
                    response = HttpResponseForbidden(f"Upgrade required for {feature_name}.")
                    response['HX-Trigger'] = 'show-upgrade-modal' # Example trigger
                    return response
                
                # For regular requests, redirect to the upgrade page
                return redirect('licensing:upgrade')

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
        license_obj = getattr(request, 'license', None)
        if not license_obj:
            license_obj = LicenseRecord.get_current()
        
        status = getattr(request, 'license_status', None)
        if not status:
            status = license_obj.get_effective_status()

        # 2. Check basic license requirements
        if status in ('expired', 'suspended', 'unlicensed'):
            return redirect('licensing:activate')

        # 3. Check for specific feature access
        from .middleware import PARTIAL_LOCK_DISABLED_FEATURES
        
        has_access = False
        if status in ('active', 'grace'):
            # Empty features_json on an active license = full access (marketplace may not send feature list)
            if not license_obj.features_json:
                has_access = True
            else:
                has_access = license_obj.features_json.get(self.feature_required, False)
        elif status == 'partial_lock':
            if self.feature_required in PARTIAL_LOCK_DISABLED_FEATURES:
                has_access = False
            else:
                has_access = license_obj.features_json.get(self.feature_required, False)
        elif status == 'trial':
            has_access = False

        if not has_access:
            if request.headers.get('HX-Request') or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return HttpResponseForbidden(f"Upgrade required for {self.feature_required}.")
            return redirect('licensing:upgrade')

        return super().dispatch(request, *args, **kwargs)
