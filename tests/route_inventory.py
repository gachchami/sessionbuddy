ASSET_SUFFIXES = (".css", ".js", ".json", ".txt", ".xml", ".svg", ".png")


def document_routes(app) -> set[str]:
    """Walk document routes through the app's included-router wrappers."""
    paths: set[str] = set()
    for wrapper in app.routes:
        router = getattr(wrapper, "original_router", None)
        routes = router.routes if router is not None else [wrapper]
        for route in routes:
            path = getattr(route, "path", None)
            if path is None or getattr(route, "include_in_schema", False):
                continue
            if "/assets/" in path or path.endswith(ASSET_SUFFIXES):
                continue
            paths.add(path)
    assert paths, "route walk found nothing; did _IncludedRouter change shape?"
    return paths
