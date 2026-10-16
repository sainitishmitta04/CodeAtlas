import json
import shop.pricing as pricing
from shop import utils
from . import models


def main():
    items = pricing.make_items()
    print(utils.fmt(1))
    print(utils.fmt(2))
    run_report()
    process()
    json.dumps({})
    return pricing.total(items), models.Item("c", 3.0)
