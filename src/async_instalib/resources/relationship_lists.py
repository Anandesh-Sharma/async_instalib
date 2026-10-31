"""Account-owned relationship lists, restricted to recorded Android read contracts."""

import json
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from ..config.muted import MutedDecodeOptions
from ..config.relationship_lists import (
    CloseFriendsListOptions,
    FollowRequestsListOptions,
    MutedListOptions,
)
from ..exceptions import LoginRequired, ProtocolError
from ..models.muted import MutedPerson
from ..models.pagination import Page
from ..models.people import PersonSummary, _identifier
from ..transport import Transport
from ..transport.graphql import GraphQLRequest, JSONStreamDecoder
from .muted_metadata import MutedAccountsDecoder


class RelationshipLists:
    """Read the signed-in account's relationship lists without changing their membership."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def blocked(self) -> Page[PersonSummary]:
        """Read the recorded blocked-account list, preserving block metadata in raw.

        The captured request has no query, count, or cursor input. An incomplete
        response fails explicitly rather than inventing a continuation request.
        """
        return await self._counted_list("users/blocked_list/", "blocked_list", "blocked_count")

    async def restricted(self) -> Page[PersonSummary]:
        """Read the recorded restricted-account list without changing restrictions."""
        return await self._counted_list(
            "restrict_action/get_restricted_users/", "users", "user_count"
        )

    async def close_friends(
        self, options: CloseFriendsListOptions | None = None
    ) -> Page[PersonSummary]:
        """Read current members, excluding suggestions; no implicit continuation.

        fb_close_friends_count is retained in raw and is not treated as the number
        of Instagram members. A next-page response requires an established contract.
        """
        self._require_authentication()
        settings = CloseFriendsListOptions() if options is None else options
        if not isinstance(settings, CloseFriendsListOptions):
            raise ValueError("options must be CloseFriendsListOptions.")
        query = urlencode(
            {
                "is_list_creation": "false",
                "pagination_enabled": "true",
                "page_size": str(settings.page_size),
            }
        )
        result = await self._transport.request("GET", f"stories/private_stories/members/?{query}")
        return self._first_page(result, "members")

    async def favorites(self) -> Page[PersonSummary]:
        """Read feed Favorites members; this is separate from post notification settings."""
        self._require_authentication()
        result = await self._transport.request("GET", "friendships/feed_favorites/")
        if type(result.get("has_more")) is not bool:
            raise ProtocolError("Favorites response omitted its continuation state.")
        return self._first_page(result, "users")

    async def muted(self, options: MutedListOptions | None = None) -> dict[str, Any]:
        """Read raw muted-screen metadata; this does not return a typed people page.

        Preserves either observed provider envelope and adds decoded_payload, a
        bounded inert layout object. No expressions run and no users or mute flags are inferred
        from compressed UI components. No UI action or follow-up request is sent.
        """
        self._require_authentication()
        settings = MutedListOptions() if options is None else options
        if not isinstance(settings, MutedListOptions):
            raise ValueError("options must be MutedListOptions.")
        session = self._transport.session
        context = {
            "bloks_version": session.profile.bloks_version,
            "styles_id": settings.styles_id,
            "theme_params": [
                {"value": [settings.theme], "design_system_name": settings.design_system}
            ],
        }
        result = await self._transport.request(
            "POST",
            "bloks/apps/com.instagram.growth.screens.muted_users/",
            {
                "_uuid": session.device.id,
                "bk_client_context": json.dumps(context, separators=(",", ":")),
                "bloks_versioning_id": session.profile.bloks_version,
            },
        )
        try:
            if "payload" in result and "layout" not in result:
                payload = result["payload"]
                if not isinstance(payload, str):
                    raise ValueError
            elif "payload" not in result and isinstance(result.get("layout"), dict):
                payload = json.dumps(
                    {"layout": result["layout"]},
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
            else:
                raise ValueError
            content = payload.encode("utf-8")
        except (UnicodeError, TypeError, ValueError, RecursionError):
            raise ProtocolError(
                "Muted-screen response did not contain a supported payload."
            ) from None
        decoded = JSONStreamDecoder(
            self._transport.options.max_response_bytes, max_documents=1
        ).decode(content)[0]
        try:
            tree = decoded["layout"]["bloks_payload"]["tree"]
            if not isinstance(tree, dict):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ProtocolError("Muted-screen payload did not contain its Bloks layout.") from None
        return {**result, "decoded_payload": decoded}

    async def muted_accounts(
        self,
        options: MutedListOptions | None = None,
        decode_options: MutedDecodeOptions | None = None,
    ) -> Page[MutedPerson]:
        """Read verified numeric identities and nullable mute categories from the screen.

        Current switch states must bind to the same identity as profile navigation.
        Unknown layouts fail explicitly. No embedded action runs and no profile or
        continuation request is sent; raw screen metadata remains in Page.raw.
        """
        decoder = MutedAccountsDecoder(decode_options)
        return decoder.decode(await self.muted(options))

    async def incoming_requests(
        self, options: FollowRequestsListOptions | None = None
    ) -> Page[PersonSummary]:
        """Read the native incoming-request category without approving or ignoring anyone.

        The recorded response is FULLY_LOADED with a null cursor. Other paging
        states fail explicitly until their continuation request is established.
        """
        self._require_authentication()
        settings = FollowRequestsListOptions() if options is None else options
        if not isinstance(settings, FollowRequestsListOptions):
            raise ValueError("options must be FollowRequestsListOptions.")
        account_id = _identifier(self._transport.session.user_id)
        result = await self._transport.graphql(
            GraphQLRequest(
                "IGFriendingCenterTypedQuery",
                {
                    "requestUuid": str(uuid4()),
                    "isTopCategoryFullyExpanded": settings.expand_top_category,
                    "forcedIds": [],
                    "entryPoint": settings.entry_point,
                    "user_id": account_id,
                    "category": "follow_requests",
                },
                client_doc_id="42895048516799972088067729645",
                purpose="fetch",
            )
        )
        try:
            owner = result["data"]["fetch__XDTUserDict"]
            if _identifier(owner["id"]) != account_id:
                raise ValueError
            categories = owner["friending_center_info"]["categories"]
            if not isinstance(categories, list) or any(
                not isinstance(category, dict) for category in categories
            ):
                raise ValueError
            matching = [
                category for category in categories if category.get("name") == "follow_requests"
            ]
            if len(matching) != 1:
                raise ValueError
            category = matching[0]
            if (
                category.get("load_more_style") != "FULLY_LOADED"
                or category.get("cursor") is not None
            ):
                raise ProtocolError(
                    "Follow-request response requires an unestablished continuation."
                )
            cards = category["typed_suggestion_cards"]
            if not isinstance(cards, list):
                raise ValueError
            people = self._people([card["user_card"]["user"] for card in cards])
            count = category.get("total_count")
            if count is not None and (type(count) is not int or count != len(people)):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ProtocolError(
                "Follow-request response contained invalid account or category data."
            ) from None
        return Page(people, total_count=count, raw=result)

    def _require_authentication(self) -> None:
        if not self._transport.session.authenticated:
            raise LoginRequired("Load an authenticated session before reading relationship lists.")

    async def _counted_list(
        self, endpoint: str, users_key: str, count_key: str
    ) -> Page[PersonSummary]:
        self._require_authentication()
        result = await self._transport.request("GET", endpoint)
        people = self._people(result.get(users_key))
        count = result.get(count_key)
        if type(count) is not int or count < 0:
            raise ProtocolError("Relationship-list response contained an invalid count.")
        if count != len(people) or result.get("next_max_id") or result.get("has_more"):
            raise ProtocolError(
                "Relationship-list response requires an unestablished continuation."
            )
        return Page(people, total_count=count, raw=result)

    @classmethod
    def _first_page(cls, result: dict[str, Any], users_key: str) -> Page[PersonSummary]:
        if result.get("next_max_id") or result.get("has_more") or result.get("more_available"):
            raise ProtocolError(
                "Relationship-list response requires an unestablished continuation."
            )
        return Page(cls._people(result.get(users_key)), raw=result)

    @staticmethod
    def _people(users: Any) -> tuple[PersonSummary, ...]:
        if not isinstance(users, list) or any(not isinstance(user, dict) for user in users):
            raise ProtocolError("Relationship-list response contained an invalid people list.")
        try:
            return tuple(PersonSummary.from_dict(user) for user in users)
        except ValueError:
            raise ProtocolError("Relationship-list response contained an invalid person.") from None
