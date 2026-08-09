"""The telescope kernel — the domain-agnostic half of the observatory.

A "telescope" watches a noisy landscape through several independent public
signals, joins them onto one entity identity, blends them into a tunable
ranking, and remembers what it saw last time so it can announce what changed.

Hubble (LLMs) was the first. This package is that pattern with the LLM parts
taken out; `observatories/` holds the domain packs that plug into it.
"""
from telescope.base import Column, Telescope
from telescope.ranking import Signal

__all__ = ["Telescope", "Column", "Signal"]
