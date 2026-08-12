from sessionbuddy.api.app import app


def test_scheduling_routes_publish_explicit_response_schemas() -> None:
    document = app.openapi()
    contracts = {
        ("get", "/api/v1/admin/events/{event_id}/agenda", "200"): "AdminAgendaView",
        ("post", "/api/v1/admin/events/{event_id}/sessions", "201"): "AdminAgendaView",
        ("post", "/api/v1/admin/events/{event_id}/agenda/setup", "201"): "AdminAgendaView",
        ("post", "/api/v1/admin/events/{event_id}/agenda/rooms", "200"): "AdminAgendaView",
        (
            "patch",
            "/api/v1/admin/events/{event_id}/agenda/rooms/{room_id}",
            "200",
        ): "AdminAgendaView",
        ("post", "/api/v1/admin/events/{event_id}/agenda/tracks", "200"): "AdminAgendaView",
        (
            "patch",
            "/api/v1/admin/events/{event_id}/agenda/tracks/{track_id}",
            "200",
        ): "AdminAgendaView",
        (
            "post",
            "/api/v1/admin/events/{event_id}/agenda/auto-schedule",
            "200",
        ): "AutoScheduledAgendaView",
        ("post", "/api/v1/admin/events/{event_id}/agenda/preview", "200"): "AgendaPreviewView",
        ("post", "/api/v1/admin/events/{event_id}/agenda/items", "201"): "AgendaItemView",
        (
            "patch",
            "/api/v1/admin/events/{event_id}/agenda/items/{item_id}",
            "200",
        ): "AgendaItemView",
        ("post", "/api/v1/admin/events/{event_id}/agenda/publish", "200"): "AgendaPublishView",
        ("get", "/api/v1/events/{event_id}/schedule", "200"): "ScheduleView",
        ("get", "/api/v1/public/events/{event_id}/schedule", "200"): "PublicScheduleView",
    }

    for (method, path, status), model_name in contracts.items():
        schema = document["paths"][path][method]["responses"][status]["content"][
            "application/json"
        ]["schema"]
        assert schema == {"$ref": f"#/components/schemas/{model_name}"}

    components = document["components"]["schemas"]
    for model_name in set(contracts.values()):
        assert components[model_name]["additionalProperties"] is False


def test_scheduling_models_keep_established_items_keys() -> None:
    document = app.openapi()
    components = document["components"]["schemas"]

    for model_name in (
        "AdminAgendaView",
        "AutoScheduledAgendaView",
        "ScheduleView",
        "PublicScheduleView",
    ):
        assert "items" in components[model_name]["properties"]
        assert "items" in components[model_name]["required"]

    assert set(components["AgendaItemView"]["properties"]) == {
        "id",
        "session_id",
        "title",
        "start_at_ms",
        "end_at_ms",
        "room_id",
        "room_name",
        "track_id",
        "track_name",
        "version",
    }
