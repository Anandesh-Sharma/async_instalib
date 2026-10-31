"""Read-only profile grid requests derived from the Android profile screen."""

import json
from typing import Any
from urllib.parse import urlencode

from ..config.content import (
    ContentOptions,
    MetadataOptions,
    ReelsOptions,
    RepostsOptions,
    TaggedOptions,
)
from ..config.metadata import HighlightItemsOptions
from ..exceptions import LoginRequired, ProtocolError
from ..models.content import Media
from ..models.pagination import Continuation, Page, PageContext
from ..models.people import _identifier
from ..models.stories import HighlightItems, HighlightTray, StoryCollection
from ..transport import Transport
from ..transport.graphql import GraphQLRequest
from .profile_metadata import HighlightReader

# Public feature flags from IGProfileTimelineQuery, Android 452.1.0.53.64.
_PROFILE_VARIABLES: dict[str, Any] = {
    "request_media_chunk": True,
    "skip_ig_play_count": True,
    "enable_carousel_media_count_in_deferred": True,
    "skip_clips_captions_fields": False,
    "request_hints_chunk": True,
    "num_previews_for_associated_highlights": 3,
    "enable_profile_fm_integration": False,
    "include_associated_highlights": False,
    "include_profile_grid_rendering_option": False,
    "include_fb_mentioned_users": True,
    "include_media_notes_fields": True,
    "include_accessibility_caption_for_carousel": True,
    "include_eligible_insights_entrypoints": True,
    "include_videos_for_associated_highlights": False,
    "exclude_user": True,
    "exclude_collaborative_posts": False,
    "include_is_photo_comments_composer_enabled_for_author": True,
    "initial_count_carousel_media": 1,
    "fetch_profile_grid_items": True,
    "include_horizon_attribution_info": False,
    "force_compute_user_tags": True,
    "exclude_pinned_posts": False,
    "include_ring_creator_fields": True,
    "exclude_highlights": False,
    "include_is_unseen_by_viewer": True,
    "include_timeline_ordered_edge": False,
    "exclude_besties_content": False,
    "include_hidden_feed_product_tags": False,
    "enabled_featured_pin_user_ids": False,
    "lightweight_carousel_type": "SUB_LIST",
    "exclude_comment": "true",
    "include_hidden_reel_product_tags": False,
    "defer_hints_chunk": True,
}


class ProfileContent:
    """Fetch profile content without playing media or marking stories as seen."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._highlights = HighlightReader(transport)

    async def posts(self, user_id: str, options: ContentOptions | None = None) -> Page[Media]:
        """Fetch one grid page; return media in the provider's display order."""
        if options is not None and not isinstance(options, ContentOptions):
            raise ValueError("options must be ContentOptions or None.")
        identifier = _identifier(user_id)
        session = self._transport.session
        if not session.authenticated:
            raise LoginRequired("Load an authenticated session before reading profile content.")
        settings = options or ContentOptions()
        context = PageContext(
            "profile_posts",
            session.user_id or "",
            target=identifier,
            query=json.dumps(settings.variables, sort_keys=True, separators=(",", ":")),
        )
        variables = dict(_PROFILE_VARIABLES)
        if settings.cursor is not None:
            settings.cursor.validate_for(context)
            variables.update(self._read_cursor(settings.cursor))
            # The app requests complete grid items directly on continuation pages.
            variables.update(
                request_media_chunk=False, enable_carousel_media_count_in_deferred=False
            )
            variables.pop("lightweight_carousel_type", None)
        variables.update(settings.variables)
        variables.update(user_id=identifier, count=settings.limit)
        result = await self._transport.graphql(
            GraphQLRequest(
                friendly_name="IGProfileTimelineQuery",
                client_doc_id="56030350817067281989573632695",
                variables=variables,
            )
        )
        try:
            timeline = result["data"]["xdt_api__v1__profile_timeline"]
            if not isinstance(timeline, dict):
                raise ValueError
            grid = timeline["profile_grid_items"]
            if not isinstance(grid, list):
                raise ValueError
            items = tuple(Media.from_dict(row["media"]) for row in grid)
            more = timeline.get("more_available")
            if type(more) is not bool:
                raise ValueError
            continuation = self._make_cursor(timeline, context) if more else None
            return Page(items, continuation=continuation, raw=timeline)
        except (KeyError, TypeError, ValueError):
            raise ProtocolError("Profile timeline contained an invalid media page.") from None

    async def reels(self, user_id: str, options: ReelsOptions | None = None) -> Page[Media]:
        """Read one streamed profile-reel page; fetching never plays a reel."""
        if options is not None and not isinstance(options, ReelsOptions):
            raise ValueError("options must be ReelsOptions or None.")
        identifier = self._authenticated_id(user_id)
        settings = options or ReelsOptions()
        context = PageContext(
            "profile_reels",
            self._transport.session.user_id or "",
            target=identifier,
            query=json.dumps(
                {
                    "sort_by_views": settings.sort_by_views,
                    "include_feed_video": settings.include_feed_video,
                    "variables": settings.variables,
                    "parameters": settings.parameters,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        if settings.cursor is not None:
            settings.cursor.validate_for(context)
        variables = {
            "use_stream": True,
            "use_defer_for_paging_info": True,
            "use_defer": False,
            "enable_clips_metadata_in_light_media": False,
            "enable_likers_in_full_media": False,
            "exclude_main_user_field": False,
            "initial_stream_count": settings.chunk_size,
            "exclude_caption_user_field": False,
            "enable_video_versions_in_light_media": True,
            "batch_defer_enabled": False,
            "stream_use_customized_batch": True,
            "enable_thumbnails_in_light_media": False,
            "enable_audience_in_light_media": False,
            **settings.variables,
            "data": {
                "target_user_id": identifier,
                "sort_by_views": settings.sort_by_views,
                "page_size": settings.limit,
                "no_of_medias_in_each_chunk": settings.chunk_size,
                "should_stream_response": True,
                "max_id": settings.cursor.value if settings.cursor else None,
                "include_feed_video": settings.include_feed_video,
                "exclude_media_ids": [],
                "should_have_max_id_in_last_chunk_only": None,
                "max_media_id": None,
                "exclude_carousel_media": None,
                "is_request_from_viewer": None,
                "audience": None,
                **settings.parameters,
            },
        }
        result = await self._transport.graphql(
            GraphQLRequest(
                friendly_name="ClipsProfileQuery",
                client_doc_id="209049231616530359161180147572",
                variables=variables,
            )
        )
        try:
            clips = result["data"]["xdt_user_clips_graphql"]
            rows = clips["items"]
            paging = clips["paging_info"]
            if not isinstance(rows, list) or not isinstance(paging, dict):
                raise ValueError
            more = paging.get("more_available")
            if type(more) is not bool:
                raise ValueError
            continuation = None
            if more:
                cursor = paging.get("max_id")
                if not isinstance(cursor, str) or not cursor:
                    raise ValueError
                continuation = Continuation(cursor, context)
            return Page(
                tuple(Media.from_dict(row["media"]) for row in rows),
                continuation=continuation,
                raw=clips,
            )
        except (KeyError, TypeError, ValueError):
            raise ProtocolError("Profile reels response contained an invalid media page.") from None

    async def tagged(self, user_id: str, options: TaggedOptions | None = None) -> Page[Media]:
        """Read one page of visible media tagging this person."""
        if options is not None and not isinstance(options, TaggedOptions):
            raise ValueError("options must be TaggedOptions or None.")
        identifier = self._authenticated_id(user_id)
        settings = options or TaggedOptions()
        context = PageContext(
            "profile_tagged",
            self._transport.session.user_id or "",
            target=identifier,
            query=json.dumps(settings.parameters, sort_keys=True, separators=(",", ":")),
        )
        parameters = {**settings.parameters, "count": str(settings.limit)}
        if settings.cursor is not None:
            settings.cursor.validate_for(context)
            parameters["max_id"] = settings.cursor.value
        result = await self._transport.request(
            "GET", f"usertags/{identifier}/feed/?{urlencode(parameters)}"
        )
        try:
            rows = result["items"]
            more = result.get("more_available")
            if not isinstance(rows, list) or type(more) is not bool:
                raise ValueError
            continuation = None
            if more:
                cursor = result.get("next_max_id")
                if type(cursor) is int and cursor > 0:
                    cursor = str(cursor)
                if not isinstance(cursor, str) or not cursor:
                    raise ValueError
                continuation = Continuation(cursor, context)
            return Page(
                tuple(Media.from_dict(row) for row in rows),
                continuation=continuation,
                total_count=result.get("total_count"),
                raw=result,
            )
        except (KeyError, TypeError, ValueError):
            raise ProtocolError("Tagged-media response contained an invalid page.") from None

    async def reposts(self, user_id: str, options: RepostsOptions | None = None) -> Page[Media]:
        """Read one profile repost page without fetching or playing its media files."""
        if options is not None and not isinstance(options, RepostsOptions):
            raise ValueError("options must be RepostsOptions or None.")
        identifier = self._authenticated_id(user_id)
        settings = options or RepostsOptions()
        authors = (
            list(settings.prepend_author_ids)
            if settings.prepend_author_ids is not None
            else [identifier]
        )
        context = PageContext(
            "profile_reposts",
            self._transport.session.user_id or "",
            target=identifier,
            query=json.dumps(
                {"authors": authors, "variables": settings.variables},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        variables = {
            **settings.variables,
            "id": identifier,
            "prepend_media_repost_author_ids": authors,
        }
        if settings.cursor is not None:
            settings.cursor.validate_for(context)
            variables["max_id"] = settings.cursor.value
        result = await self._transport.graphql(
            GraphQLRequest(
                friendly_name="FetchUserRepostsQuery",
                client_doc_id="391529631615942364796299285893",
                variables=variables,
            )
        )
        try:
            user = result["data"]["fetch__XDTUserDict"]
            if _identifier(user["id"]) != identifier:
                raise ValueError
            timeline = user["user_reposts_timeline"]
            rows = timeline["repost_grid_items"]
            more = timeline.get("repost_more_available")
            if not isinstance(rows, list) or type(more) is not bool:
                raise ValueError
            continuation = None
            if more:
                cursor = timeline.get("repost_next_max_id")
                if not isinstance(cursor, str) or not cursor:
                    raise ValueError
                continuation = Continuation(cursor, context)
            return Page(
                tuple(Media.from_dict(row["media"]) for row in rows),
                continuation=continuation,
                raw=timeline,
            )
        except (KeyError, TypeError, ValueError):
            raise ProtocolError(
                "Profile reposts response contained an invalid media page."
            ) from None

    async def stories(self, user_id: str, options: MetadataOptions | None = None) -> dict[str, Any]:
        """Read the story envelope without sending a seen acknowledgement.

        The provider's ``reel`` may be null when no accessible stories exist.
        No story playback, viewer update, or implicit follow-up request occurs.
        """
        if options is not None and not isinstance(options, MetadataOptions):
            raise ValueError("options must be MetadataOptions or None.")
        identifier = self._authenticated_id(user_id)
        query = urlencode((options or MetadataOptions()).parameters)
        endpoint = f"feed/user/{identifier}/story/"
        return await self._metadata(endpoint + (f"?{query}" if query else ""))

    async def highlights(
        self, user_id: str, options: MetadataOptions | None = None
    ) -> dict[str, Any]:
        """Read highlight metadata; item payloads remain provider-native dictionaries."""
        if options is not None and not isinstance(options, MetadataOptions):
            raise ValueError("options must be MetadataOptions or None.")
        identifier = self._authenticated_id(user_id)
        parameters = {
            "entry_point": "SEARCH",
            "max_highlights_to_fetch_on_pagination": "10",
            "check_highlights_subtab_gating": "false",
            "fetch_high_res_cover_image": "true",
            **(options or MetadataOptions()).parameters,
        }
        return await self._metadata(
            f"highlights/{identifier}/highlights_tray/?{urlencode(parameters)}"
        )

    async def story_collection(
        self, user_id: str, options: MetadataOptions | None = None
    ) -> StoryCollection:
        """Read typed story metadata without playing media or marking it seen."""
        identifier = _identifier(user_id)
        response = await self.stories(identifier, options)
        try:
            collection = StoryCollection.from_dict(response)
            reel = collection.reel
            if reel is not None and (
                reel.id != identifier or (reel.user is not None and reel.user.id != identifier)
            ):
                raise ValueError
            return collection
        except ValueError:
            raise ProtocolError("Story response contained invalid collection metadata.") from None

    async def highlight_tray(
        self, user_id: str, options: MetadataOptions | None = None
    ) -> HighlightTray:
        """Read typed highlight covers and counts, without fetching their story items."""
        identifier = _identifier(user_id)
        response = await self.highlights(identifier, options)
        try:
            tray = HighlightTray.from_dict(response)
            if any(item.user is not None and item.user.id != identifier for item in tray.items):
                raise ValueError
            return tray
        except ValueError:
            raise ProtocolError("Highlight response contained invalid tray metadata.") from None

    async def highlight_items(
        self, highlight_id: str, options: HighlightItemsOptions | None = None
    ) -> HighlightItems:
        """Read one selected highlight's items without playback or a seen acknowledgement."""
        return await self._highlights.items(highlight_id, options)

    def _authenticated_id(self, user_id: str) -> str:
        identifier = _identifier(user_id)
        if not self._transport.session.authenticated:
            raise LoginRequired("Load an authenticated session before reading profile content.")
        return identifier

    @staticmethod
    def _read_cursor(cursor: Continuation) -> dict[str, str]:
        try:
            value = json.loads(cursor.value)
            expected = {"max_id", "profile_grid_items_cursor", "pinned_profile_grid_items_ids"}
            if not isinstance(value, dict) or set(value) != expected:
                raise ValueError
            if any(not isinstance(item, str) for item in value.values()):
                raise ValueError
            if not value["max_id"] or not value["profile_grid_items_cursor"]:
                raise ValueError
            return {key: str(item) for key, item in value.items()}
        except (ValueError, TypeError):
            raise ValueError(
                "Invalid profile-grid continuation; use the previous page's cursor."
            ) from None

    @staticmethod
    def _make_cursor(timeline: dict[str, Any], context: PageContext) -> Continuation:
        fields = {
            "max_id": timeline.get("next_max_id"),
            "profile_grid_items_cursor": timeline.get("profile_grid_items_cursor"),
            "pinned_profile_grid_items_ids": timeline.get("pinned_profile_grid_items_ids"),
        }
        if any(not isinstance(value, str) for value in fields.values()):
            raise ValueError
        if not fields["max_id"] or not fields["profile_grid_items_cursor"]:
            raise ValueError
        return Continuation(json.dumps(fields, separators=(",", ":")), context)

    async def _metadata(self, endpoint: str) -> dict[str, Any]:
        records = await self._transport.request_stream("GET", endpoint)
        result: dict[str, Any] = {}
        # Highlight responses send initial hints before replacing the complete tray.
        for record in records:
            result.update(record)
        return result
