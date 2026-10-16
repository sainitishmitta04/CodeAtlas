"""Pricing rules."""
from .models import Item, DiscountedItem
from shop.utils import fmt


def _round(value):
    return round(value, 2)


def total(items):
    subtotal = sum(item.price() for item in items)
    return fmt(_round(subtotal))


def make_items():
    def build(name):
        return Item(name, 1.0)

    return [build("a"), DiscountedItem("b", 2.0)]
