#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility entry point used by Uvicorn and existing installations."""

from haudio.app import APP

__all__ = ["APP"]
