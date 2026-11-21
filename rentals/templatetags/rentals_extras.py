from django import template

from rentals.models import counted as _counted

register = template.Library()


@register.filter
def counted(quantity, name):
    """{{ line.quantity|counted:line.product.name }} → "3 dollies"."""
    return _counted(int(quantity), str(name))


@register.filter
def add_days(day, n):
    """{{ some_date|add_days:7 }}"""
    from datetime import timedelta

    return day + timedelta(days=int(n)) if day else day
