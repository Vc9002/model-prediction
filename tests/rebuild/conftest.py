"""Rebuild test fixtures.

MODEL_PREDICTION_RUNTIME_ROOT is cleared for all tests by the top-level
conftest.  Tests that need a specific value set it via monkeypatch.setenv().
"""
