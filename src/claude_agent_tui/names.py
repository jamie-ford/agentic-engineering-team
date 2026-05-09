import random
from itertools import cycle

NAMES = [
    "Joe", "Mary", "Jack", "Millie", "Sam", "Rosa", "Tom", "Priya",
    "Ben", "Layla", "Alex", "Zoe", "Kai", "Nadia", "Leo", "Iris",
    "Dan", "Freya", "Eli", "Sasha", "Omar", "Cleo", "Finn", "Remy", "Vera",
]

COLOURS = [
    "cyan", "magenta", "yellow", "green", "blue", "red",
    "bright_cyan", "bright_magenta", "bright_green", "bright_yellow",
    "orange3", "deep_sky_blue1", "chartreuse3", "hot_pink",
]

_colour_iter = cycle(COLOURS)
_name_colours: dict[str, str] = {}
_used: set[str] = set()
# Shuffle once at import time so each session gets a different order
_name_pool = random.sample(NAMES, len(NAMES))


def next_name() -> str:
    for name in _name_pool:
        if name not in _used:
            _used.add(name)
            return name
    i = len(_used) + 1
    name = f"Agent{i}"
    _used.add(name)
    return name


def colour_for(name: str) -> str:
    if name not in _name_colours:
        _name_colours[name] = next(_colour_iter)
    return _name_colours[name]


def release_name(name: str) -> None:
    _used.discard(name)
