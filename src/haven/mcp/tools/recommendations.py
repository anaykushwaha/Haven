"""MCP tool: `get_activity_recommendations`.

A lightweight, deterministic recommendation: given the household's
current context (available time, preferred genres) and what it recently
watched (`haven.memory.activity`, via `MOVIE_NIGHT_PREPARED` history),
suggest a short list of media that fits. This is not the planner —
nothing here proposes a `Plan` or touches a device; it answers "what
should we do tonight?" so the caller can turn a pick into a
`propose_household_plan` goal if they want to act on it.

Ranking is plain, explainable code, not an LLM call: match count against
preferred genres, then whether it was recently watched, then shorter
duration as a last tiebreak. `haven.integrations.media.MediaCatalogEntry`
is what makes genre-aware ranking possible here without pulling genre
into the planning-facing `MediaItem` contract.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from haven.context.resolver import resolve_context
from haven.integrations.media import MediaCatalogEntry
from haven.mcp.schemas import RecommendationsResponse, RecommendationSummary
from haven.mcp.tools.context import build_sources
from haven.memory.activity import ActivityType

DEFAULT_RECOMMENDATION_GOAL = "What should we do tonight?"
MAX_RECOMMENDATIONS = 3
RECENT_HISTORY_LIMIT = 5


def get_activity_recommendations(app, goal: str | None = None) -> RecommendationsResponse:
    """Suggest up to `MAX_RECOMMENDATIONS` media options for tonight.

    `goal` only shapes context resolution (available time, preferences);
    it defaults to a generic "what should we do tonight?" prompt when
    omitted or blank.
    """

    resolved_goal = goal.strip() if goal and goal.strip() else DEFAULT_RECOMMENDATION_GOAL

    household = app.household.get_snapshot()
    media_options = app.media.list_media_items()
    sources = build_sources(app)
    context = resolve_context(resolved_goal, household, media_options, sources)

    recent_titles = _recent_movie_titles(app, household.household_id)
    entries = app.media.list_catalog_entries()

    picks = _rank_media(
        entries=entries,
        available_minutes=context.available_minutes,
        preferred_genres=context.preferred_genres,
        recent_titles=recent_titles,
    )

    recommendations = [
        RecommendationSummary(
            media_id=entry.id,
            title=entry.title,
            duration_minutes=entry.duration_minutes,
            reason=reason,
        )
        for entry, reason in picks
    ]

    return RecommendationsResponse(
        household_id=household.household_id,
        available_minutes=context.available_minutes,
        recommendations=recommendations,
        message=_message(recommendations, context.available_minutes),
    )


def _recent_movie_titles(app, household_id: str, limit: int = RECENT_HISTORY_LIMIT) -> set[str]:
    records = app.memory.list_activity(
        household_id, activity_type=ActivityType.MOVIE_NIGHT_PREPARED, limit=limit
    )
    titles: set[str] = set()
    for record in records:
        title = record.details.get("title")
        if isinstance(title, str):
            titles.add(title)
    return titles


def _rank_media(
    *,
    entries: list[MediaCatalogEntry],
    available_minutes: int | None,
    preferred_genres: list[str],
    recent_titles: set[str],
) -> list[tuple[MediaCatalogEntry, str]]:
    preferred = {g.lower() for g in preferred_genres}

    scored: list[tuple[tuple[int, int, int], MediaCatalogEntry]] = []
    for entry in entries:
        if not entry.is_available:
            continue
        if available_minutes is not None and entry.duration_minutes > available_minutes:
            continue

        genre_matches = len(preferred & {g.lower() for g in entry.genres})
        recently_watched = entry.title in recent_titles
        # Higher is better in every position: more genre matches, not
        # recently watched, and (as a last tiebreak) a shorter runtime.
        score = (genre_matches, 0 if recently_watched else 1, -entry.duration_minutes)
        scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    results: list[tuple[MediaCatalogEntry, str]] = []
    for score, entry in scored[:MAX_RECOMMENDATIONS]:
        genre_matches, not_recent, _ = score
        results.append((entry, _reason(entry, genre_matches, not_recent == 0, preferred)))
    return results


def _reason(
    entry: MediaCatalogEntry, genre_matches: int, recently_watched: bool, preferred: set[str]
) -> str:
    parts = []
    if genre_matches:
        matched = sorted({g for g in entry.genres if g.lower() in preferred})
        label = "genres" if len(matched) != 1 else "genre"
        parts.append(f"matches your preferred {label} ({', '.join(matched)})")
    if recently_watched:
        parts.append("you watched this recently, but it still fits")
    if not parts:
        parts.append(f"fits your available time at {entry.duration_minutes} min")
    return "; ".join(parts).capitalize()


def _message(recommendations: list[RecommendationSummary], available_minutes: int | None) -> str:
    if not recommendations:
        if available_minutes is not None:
            return f"Nothing in the catalog fits {available_minutes} minutes right now."
        return "No matching recommendations right now."

    if available_minutes is not None:
        return (
            f"You have about {available_minutes} minutes — here are "
            f"{len(recommendations)} option(s) that fit."
        )
    return f"Here are {len(recommendations)} option(s) based on your preferences."


def register(mcp) -> None:
    from haven.mcp.server import get_app

    @mcp.tool()
    def get_activity_recommendations_tool(
        ctx: Context, goal: str | None = None
    ) -> RecommendationsResponse:
        """Suggest a short list of things to watch tonight based on
        available time, saved genre preferences, and recent activity —
        without proposing or executing a plan. Pass a goal like 'movie
        night' to steer context resolution, or omit it for a general
        'what should we do tonight?' recommendation.
        """

        return get_activity_recommendations(get_app(ctx), goal)


__all__ = ["get_activity_recommendations", "register"]
