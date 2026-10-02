"""Adapters for the physical ECU-7 bench (real I/O and 1553 cards).

These are templates: this machine has no bench hardware, so each adapter checks for its vendor
driver and reports the target as unavailable if it is missing. Fill in the TODOs with calls to
your card's SDK; the rest of RDTG (targets, generated tests) does not change.
"""
from __future__ import annotations

import importlib

from rdtg.harness import RigError, TransportAdapter


def _require(module: str, product: str):
    try:
        return importlib.import_module(module)
    except ImportError:
        raise RigError(f"{product} driver not found (python module '{module}' is not installed "
                       f"on this machine)") from None


class DiscreteIOCard(TransportAdapter):
    """NI PXI digital/analog I/O card driving ECU-7's discrete and analog channels."""

    def __init__(self, device: str = "PXI1Slot2"):
        self.nidaqmx = _require("nidaqmx", "NI-DAQmx")
        self.device = device
        # TODO: create DO/DI/AO tasks for the channels in the ICD §1.1 table.

    def write(self, name, value, spec):
        raise NotImplementedError("TODO: write the channel through NI-DAQmx")

    def read(self, name, spec):
        raise NotImplementedError("TODO: read the channel through NI-DAQmx")

    def reset(self):
        raise NotImplementedError("TODO: power-cycle the ECU via the bench power relay")


class Ddc1553Card(TransportAdapter):
    """DDC BU-67112 MIL-STD-1553 card acting as bus controller for ECU-7 (RT 5)."""

    def __init__(self, card: int = 0):
        self.sdk = _require("ddc_bustools", "DDC BusTools/1553")
        self.card = card
        # TODO: open the card, configure BC mode, build the MODE_CMD (RT5 SA3) message.

    def send_message(self, message, fields):
        raise NotImplementedError("TODO: schedule a BC->RT transfer with the encoded data words")
