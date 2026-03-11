from django.contrib.auth.forms import AuthenticationForm
from django import forms

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
        return super().clean()
