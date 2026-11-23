from datetime import timedelta

from django import forms
from django.utils import timezone


class Form(forms.Form):
    """Our base form: labels without a trailing colon."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)


class PackageForm(Form):
    """Step 1: which package, for how long, starting when."""

    package = forms.ModelChoiceField(queryset=None, widget=forms.RadioSelect, empty_label=None,
                                     label="Choose a package")
    weeks = forms.TypedChoiceField(coerce=int, label="For how long?")
    dropoff_date = forms.DateField(label="Dropoff date", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, min_notice_days=3, min_weeks=2, max_weeks=4, **kwargs):
        from .models import Package

        super().__init__(*args, **kwargs)
        self.fields["package"].queryset = Package.objects.filter(active=True)
        self.fields["weeks"].choices = [(n, f"{n} weeks") for n in range(min_weeks, max_weeks + 1)]
        self.fields["weeks"].initial = min_weeks
        self.earliest = timezone.localdate() + timedelta(days=min_notice_days)
        self.fields["dropoff_date"].widget.attrs["min"] = self.earliest.isoformat()
        self.fields["dropoff_date"].initial = self.earliest

    def clean_dropoff_date(self):
        d = self.cleaned_data["dropoff_date"]
        if d < self.earliest:
            raise forms.ValidationError(
                f"We need at least 3 days' notice. The earliest dropoff is {self.earliest:%A, %B %-d}. "
                "For something sooner, please contact us."
            )
        return d


class ExtrasForm(Form):
    """Step 2: extra totes, rental extras and items for sale (one field per product)."""

    addon_totes = forms.IntegerField(min_value=0, max_value=200, initial=0, required=False,
                                     label="Extra totes")

    def __init__(self, *args, products=(), upgrade=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.products = list(products)
        self.upgrade = upgrade
        if upgrade is not None:
            self.fields["upgrade"] = forms.BooleanField(required=False, label=f"Add the {upgrade.name}")
        for p in self.products:
            self.fields[f"x_{p.pk}"] = forms.IntegerField(
                min_value=0, max_value=50, initial=0, required=False, label=p.name,
                widget=forms.NumberInput(attrs={"inputmode": "numeric", "class": "qty"}))

    def extras(self):
        """{product id (str): quantity} for everything with a quantity."""
        return {str(p.pk): self.cleaned_data.get(f"x_{p.pk}") or 0
                for p in self.products if self.cleaned_data.get(f"x_{p.pk}")}

    def rows(self, kind):
        return [(p, self[f"x_{p.pk}"]) for p in self.products if p.kind == kind]


class DeliveryForm(Form):
    """Step 4: porch or present, and an optional time request."""

    presence = forms.ChoiceField(
        widget=forms.RadioSelect, label="At dropoff and pickup",
        choices=[("porch", "Leave the totes at my door or porch (at my own risk)"),
                 ("present", "I'll be there: I've noted a time that works below")],
        initial="porch",
    )
    time_request = forms.CharField(
        required=False, max_length=200, label="Prefer a specific time? (optional)",
        widget=forms.TextInput(attrs={"placeholder": "e.g. after 10 AM on dropoff day"}),
        help_text="We'll do our best; we'll always arrive by the guaranteed time.",
    )

    def clean(self):
        data = super().clean()
        if data.get("presence") == "present" and not data.get("time_request"):
            self.add_error("time_request", "Let us know when you'll be there.")
        return data


US_STATES_NEARBY = [("MD", "Maryland"), ("PA", "Pennsylvania"), ("DE", "Delaware"),
                    ("VA", "Virginia"), ("DC", "Washington, DC")]


class AddressForm(Form):
    line1 = forms.CharField(label="Street address", max_length=200,
                            widget=forms.TextInput(attrs={"autocomplete": "address-line1"}))
    line2 = forms.CharField(label="Apt / unit", max_length=200, required=False,
                            widget=forms.TextInput(attrs={"autocomplete": "address-line2"}))
    city = forms.CharField(max_length=100,
                           widget=forms.TextInput(attrs={"autocomplete": "address-level2"}))
    state = forms.ChoiceField(choices=US_STATES_NEARBY, initial="MD")
    zip_code = forms.RegexField(label="ZIP", regex=r"^\d{5}(-\d{4})?$",
                                error_messages={"invalid": "Enter a 5-digit ZIP code."},
                                widget=forms.TextInput(attrs={"autocomplete": "postal-code",
                                                              "inputmode": "numeric"}))


class PickupAddressForm(AddressForm):
    """Same as AddressForm, but can be left blank if the renter ticks 'not yet'."""

    unknown = forms.BooleanField(
        required=False, label="I don't know my new address yet (I'll add it before dropoff)"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("line1", "city", "state", "zip_code"):
            self.fields[name].required = False

    def clean(self):
        data = super().clean()
        if not data.get("unknown"):
            for name in ("line1", "city", "zip_code"):
                if not data.get(name) and name not in self.errors:
                    self.add_error(name, "Required (or tick the box above if you don't know yet).")
        return data


class TimesForm(Form):
    dropoff_window = forms.TypedChoiceField(coerce=int, widget=forms.RadioSelect,
                                            label="Dropoff time")
    pickup_window = forms.TypedChoiceField(coerce=int, widget=forms.RadioSelect,
                                           label="Pickup time")

    def __init__(self, *args, dropoff_windows=(), pickup_windows=(), **kwargs):
        super().__init__(*args, **kwargs)
        fmt = "{:%-I:%M %p} – {:%-I:%M %p}"
        self.fields["dropoff_window"].choices = [(w.pk, fmt.format(w.start, w.end)) for w in dropoff_windows]
        self.fields["pickup_window"].choices = [(w.pk, fmt.format(w.start, w.end)) for w in pickup_windows]


class ContactForm(Form):
    name = forms.CharField(label="Full name", max_length=150,
                           widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    phone = forms.CharField(max_length=30, help_text="For day-of coordination only.",
                            widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel", "data-phone": ""}))
    access_notes = forms.CharField(
        label="Anything we should know?", required=False, max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Where to leave the bins, gate codes, stairs, parking…",
    )


class FindBookingForm(Form):
    email = forms.EmailField(label="Email you booked with")


class SignForm(Form):
    """The agreement checkbox and typed signature."""

    agree = forms.BooleanField(
        label="I've read and agree to the Sprout Tote Co. Rental Agreement, including the authorization "
              "in section 3 to charge my card for amounts due under it.",
        error_messages={"required": "Please tick the box to agree."},
    )
    typed_name = forms.CharField(
        label="Type your full name to sign", max_length=150,
        help_text="Use the name on the card you'll pay with.",
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )

    def clean_typed_name(self):
        from .signing import _name_tokens

        name = self.cleaned_data["typed_name"].strip()
        if len(_name_tokens(name)) < 2:
            raise forms.ValidationError("Please type your first and last name.")
        return name
