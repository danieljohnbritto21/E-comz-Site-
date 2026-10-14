from django import forms
from django.contrib.auth import authenticate
from django.utils.text import slugify
from apps.accounts.models import User, BuyerProfile, SellerProfile, DeliveryProfile


class LoginForm(forms.Form):
    role = forms.CharField(
        required=True,
        widget=forms.HiddenInput(attrs={'id': 'hidden-role-input'})
    )
    username = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username or Email', 'autofocus': True})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Password'})
    )

    def clean_role(self):
        role_raw = str(self.cleaned_data.get('role', '')).strip().upper()
        if role_raw in ['CUSTOMER', 'BUYER']:
            return 'BUYER'
        elif role_raw in ['SELLER', 'VENDOR']:
            return 'SELLER'
        elif role_raw in ['DELIVERY', 'DELIVERY_PERSON', 'DRIVER', 'AGENT']:
            return 'DELIVERY'
        elif role_raw in ['ADMIN', 'ADMINISTRATOR']:
            return 'ADMIN'
        raise forms.ValidationError("Invalid role selected. Please choose a valid login tab.")


class BuyerRegistrationForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Create strong password'})
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm your password'})
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone_number']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. rahul_fresh'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'you@example.com'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+91 9876543210'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('password')
        p2 = cleaned_data.get('confirm_password')
        if p1 and p2 and p1 != p2:
            self.add_error('confirm_password', 'Passwords do not match.')
        return cleaned_data


class SellerRegistrationForm(forms.ModelForm):
    store_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Green Valley Organics'})
    )
    pickup_address = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Full pickup warehouse/store address'})
    )
    city = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'City'})
    )
    pin_code = forms.CharField(
        max_length=10,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Pin code'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Create strong password'})
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm password'})
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone_number']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Contact Person First Name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'business@store.com'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Store Phone Number'}),
        }

    def clean_store_name(self):
        name = self.cleaned_data.get('store_name')
        if SellerProfile.objects.filter(store_name__iexact=name).exists():
            raise forms.ValidationError("A store with this name already exists. Please choose a unique store name.")
        return name

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('password')
        p2 = cleaned_data.get('confirm_password')
        if p1 and p2 and p1 != p2:
            self.add_error('confirm_password', 'Passwords do not match.')
        return cleaned_data
