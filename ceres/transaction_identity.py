"""Identity attached to newly prepared Ceres custom_json transactions."""


def app_identity() -> str:
    from .routes_board import released_version

    return f"ceres/{released_version()}"
