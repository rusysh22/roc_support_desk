from django.contrib.auth.forms import AuthenticationForm
from django import forms
from django.core.cache import cache
from django.contrib.auth import get_user_model

User = get_user_model()

class CustomAuthenticationForm(AuthenticationForm):
    # This field is a honeypot; it should be left empty by humans
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'style': 'display: none !important; opacity: 0; position: absolute; top: -9999px; left: -9999px;',
            'autocomplete': 'off',
            'tabindex': '-1'
        }),
        label="Website"
    )

    def clean(self):
        # Anti-bot verification: if 'website' is filled, it's highly likely a bot
        website = self.cleaned_data.get('website')
        if website:
            # If it's filled, we raise a generic error to not give bots clues
            raise forms.ValidationError(
                "Anti-bot verification failed. Please try again.",
                code="bot_detected",
            )
            
        username = self.cleaned_data.get('username')
        
        # 1. Check if the user is already locked but trying to log in
        if username:
            try:
                user_obj = User.objects.get(login_username=username)
                if not user_obj.has_usable_password():
                    raise forms.ValidationError(
                        "Akun ini terkunci karena terlalu banyak percobaan gagal. Silakan gunakan fitur 'Lupa Sandi' untuk mengatur ulang password.",
                        code="account_locked"
                    )
            except User.DoesNotExist:
                pass
                
        # 2. Attempt standard authentication
        try:
            cleaned_data = super().clean()
            
            # If successful, clear any failed attempts counter for this user
            if username:
                cache.delete(f"login_attempts_{username}")
                
            return cleaned_data
            
        except forms.ValidationError as e:
            # 3. Handle failed authentication ONLY IF it's the standard invalid_login error
            if e.code == 'invalid_login' and username:
                cache_key = f"login_attempts_{username}"
                attempts = cache.get(cache_key, 0) + 1
                
                if attempts >= 5:
                    # Lock the account on the 5th failed attempt
                    try:
                        user_obj = User.objects.get(login_username=username)
                        user_obj.set_unusable_password()
                        user_obj.save(update_fields=['password'])
                        cache.delete(cache_key) # clear counter after lock
                        
                        raise forms.ValidationError(
                            "Anda telah gagal login 5 kali. Demi keamanan, akun Anda dikunci sementara. Silakan gunakan fitur 'Lupa Sandi' untuk mereset menggunakan OTP.",
                            code="account_locked_just_now"
                        )
                    except User.DoesNotExist:
                        # Username doesn't exist, just let standard generic error fall through
                        pass
                else:
                    # Increment counter and store it for 24 hours (86400s) to track consecutive failures
                    cache.set(cache_key, attempts, timeout=86400)
                    
                    remaining = 5 - attempts
                    raise forms.ValidationError(
                        f"Username atau password tidak valid. Perhatian: Anda memiliki {remaining} sisa percobaan sebelum akun dikunci.",
                        code="invalid_login_warning"
                    )
            
            # Propagate validation errors (like bot detection or non-invalid_login errors)
            raise e

