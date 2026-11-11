from datetime import timedelta

from django import forms
from django.utils import timezone


class Form(forms.Form):
    """Our base form: labels without a trailing colon."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)


class QuoteForm(Form):
    quantity = forms.IntegerField(min_value=1, initial=40, label="How many bins?")
    dropoff_date = forms.DateField(
        label="Dropoff date", widget=forms.DateInput(attrs={"type": "date"})
    )
    weeks = forms.TypedChoiceField(
        coerce=int,
        choices=[(n, f"{n} week{'s' if n > 1 else ''}") for n in range(1, 5)],
        initial=1,
        label="For how long?",
    )

    def __init__(self, *args, minimum_quantity=20, min_notice_days=3, **kwargs):
        super().__init__(*args, **kwargs)
        self.minimum_quantity = minimum_quantity
        self.fields["quantity"].min_value = minimum_quantity
        self.fields["quantity"].widget.attrs["min"] = minimum_quantity
        self.earliest = timezone.localdate() + timedelta(days=min_notice_days)
        self.fields["dropoff_date"].widget.attrs["min"] = self.earliest.isoformat()
        self.fields["dropoff_date"].initial = self.earliest

    def clean_quantity(self):
        q = self.cleaned_data["quantity"]
        if q < self.minimum_quantity:
            raise forms.ValidationError(f"The minimum order is {self.minimum_quantity} bins.")
        return q

    def clean_dropoff_date(self):
        d = self.cleaned_data["dropoff_date"]
        if d < self.earliest:
            raise forms.ValidationError(
                f"We need a few days' notice. The earliest dropoff is {self.earliest:%A, %B %-d}."
            )
        return d


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
                            widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}))
    access_notes = forms.CharField(
        label="Anything we should know?", required=False, max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Where to leave the bins, gate codes, stairs, parking…",
    )


class FindBookingForm(Form):
    email = forms.EmailField(label="Email you booked with")
