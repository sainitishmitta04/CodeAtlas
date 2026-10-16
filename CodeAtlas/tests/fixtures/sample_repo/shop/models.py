"""Domain models."""


class Item:
    """A sellable item."""

    def __init__(self, name, base_price):
        self.name = name
        self.base_price = base_price

    def base(self):
        return self.base_price

    def price(self):
        return self.base()


class DiscountedItem(Item):
    def price(self):
        return self.base() * 0.9

    def label(self):
        return self.price()
