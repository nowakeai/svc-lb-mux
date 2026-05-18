"""Small value objects used by reconciliation logic."""

from collections import namedtuple


class MuxPort(namedtuple("MuxPort", ["name", "port", "protocol"])):
    pass


class MuxEp(namedtuple("MuxEp", ["ip", "port", "protocol"])):
    pass
