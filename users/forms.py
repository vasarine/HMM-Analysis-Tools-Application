from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.utils.safestring import mark_safe
from django import forms


class RegisterForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        label="Email",
        help_text="Required. Enter a valid email address for password recovery."
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")
        labels = {
            "username": "Username",
            "email": "Email",
            "password1": "Password",
            "password2": "Password confirmation",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["username"].help_text = "Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only."

        pw_help = mark_safe(
            "<ul class='helptext-list'>"
            "<li>Your password can't be too similar to your other personal information.</li>"
            "<li>Your password must contain at least 8 characters.</li>"
            "<li>Your password can't be a commonly used password.</li>"
            "<li>Your password can't be entirely numeric.</li>"
            "</ul>"
        )
        self.fields["password1"].help_text = pw_help

        self.fields["password2"].help_text = "Enter the same password as before, for verification."

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email address already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class ProjectSharingForm(forms.Form):
    VISIBILITY_CHOICES = [
        ('private', 'Private - only me'),
        ('link', 'Link - anyone with the link can view'),
        ('public', 'Public - visible to everyone'),
    ]

    visibility = forms.ChoiceField(
        choices=VISIBILITY_CHOICES,
        widget=forms.RadioSelect,
        label='Visibility',
        initial='private'
    )

    share_outputs = forms.BooleanField(
        required=False,
        initial=True,
        label='Output / result files',
    )

    share_inputs = forms.BooleanField(
        required=False,
        initial=False,
        label='Input files',
    )

    email_to_share = forms.EmailField(
        required=False,
        label='Share with specific user',
        help_text='Enter email address of user to share with',
        widget=forms.EmailInput(attrs={
            'placeholder': 'user@example.com',
            'class': 'email-share-input'
        })
    )
