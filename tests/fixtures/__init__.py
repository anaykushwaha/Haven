"""Shared test fixtures and builders.

`simulators` holds the pytest fixtures (a fresh household/media simulator,
memory, calendar, resolved context and plan factories, an isolated MCP
`AppContext`). They are registered for the whole suite by
``pytest_plugins`` in ``tests/conftest.py``. `plans` holds plain builder
functions for hand-made plans and a deliberately unreliable device
backend, usable from any test without fixtures.

Every fixture builds a *new* simulator per test, so no test can see
another's lights, locks or saved preferences.
"""
