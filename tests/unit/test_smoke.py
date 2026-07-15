"""Smoke test — proves pytest finds the installed blindfold package."""


def test_import_blindfold():
    import blindfold  # noqa: F401
