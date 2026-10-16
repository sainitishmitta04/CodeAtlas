import os
import os
from os import path


def fmt(value):
    return f"{value:.2f}"


def helper():
    return path.join(os.getcwd(), "x")
