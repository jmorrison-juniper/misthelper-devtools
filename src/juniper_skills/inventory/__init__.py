"""Inventory tools for the Juniper documentation skill factory."""

from src.juniper_skills.inventory.engine import InventoryBuilder  # Export the builder as the package entry point.

__all__ = ["InventoryBuilder"]  # Keep the public surface small for factory imports.
