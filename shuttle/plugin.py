from bdh_sdk import PluginSpec, Tile

spec = PluginSpec(
    id="shuttle",
    app="shuttle",
    urls="shuttle.urls",
    status="shuttle.homepage.status",
    tiles=(
        Tile(
            title="Shuttle",
            url_name="shuttle",
            icon="shuttle/hub/icon.svg",
            section="mountain",
            subtitle="Free bus to the Village",
            order=45,
        ),
    ),
)
