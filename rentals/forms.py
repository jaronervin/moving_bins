from datetime import timedelta

from django import forms
from django.utils import timezone


class QuoteForm(forms.Form):
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

    def __init__(self, *args, minimum_quantity=20, **kwargs):
        super().__init__(*args, **kwargs)
        self.minimum_quantity = minimum_quantity
        self.fields["quantity"].min_value = minimum_quantity
        self.fields["quantity"].widget.attrs["min"] = minimum_quantity
        tomorrow = timezone.localdate() + timedelta(days=1)
        self.fields["dropoff_date"].widget.attrs["min"] = tomorrow.isoformat()
        self.fields["dropoff_date"].initial = tomorrow

    def clean_quantity(self):
        q = self.cleaned_data["quantity"]
        if q < self.minimum_quantity:
            raise forms.ValidationError(f"The minimum order is {self.minimum_quantity} bins.")
        return q

    def clean_dropoff_date(self):
        d = self.cleaned_data["dropoff_date"]
        if d <= timezone.localdate():
            raise forms.ValidationError("Choose a date from tomorrow on.")
        return d
