"""Explicit relationship reads and changes derived from controlled app recordings."""

import json
from copy import deepcopy
from dataclasses import dataclass, field, fields
from typing import Any, Literal
from uuid import uuid4

from ..config.relationships import (
    ActionOptions,
    BlockOptions,
    CloseFriendsOptions,
    FavoritesOptions,
    MuteOptions,
    NotificationOptions,
)
from ..exceptions import APIError, LoginRequired, ProtocolError
from ..models.people import PersonSummary, Relationship, _identifier
from ..transport import Transport
from .relationship_lists import RelationshipLists
from .relationship_muting import GraphQLMuting


@dataclass(frozen=True, slots=True)
class RelationshipActionResult:
    """Server acknowledgement for actions that return no relationship state."""

    user_id: str
    action: str
    status: Literal["ok"] = "ok"
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", deepcopy(self.raw))


class Relationships:
    """Issue one relationship operation per call, without mutation retries."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self.lists = RelationshipLists(transport)
        self._graphql_muting = GraphQLMuting(transport)

    async def get(self, user_id: str) -> Relationship:
        """Read account-relative state for a numeric person ID."""
        target = self._target(user_id)
        result = await self._transport.request("GET", f"friendships/show/{target}/")
        return self._relationship(result, target)

    async def follow(self, user_id: str, options: ActionOptions | None = None) -> Relationship:
        """Follow or request to follow; return the server's actual resulting state."""
        return await self._following(user_id, self._action_options(options), "create")

    async def unfollow(self, user_id: str, options: ActionOptions | None = None) -> Relationship:
        """Unfollow once and return the server's resulting relationship state."""
        settings = (
            ActionOptions(container_module="following_sheet")
            if options is None
            else self._action_options(options)
        )
        return await self._following(user_id, settings, "destroy")

    async def cancel_request(
        self, user_id: str, options: ActionOptions | None = None
    ) -> Relationship:
        """Cancel an outgoing follow request with the recorded pending-profile context.

        Instagram shares this operation with unfollowing: if the request has already
        been accepted, it can remove the follow instead. No preflight read, retry,
        or inferred state is added; the returned flags are the server's response.
        """
        settings = (
            ActionOptions(container_module="friending_center")
            if options is None
            else self._action_options(options)
        )
        return await self._following(user_id, settings, "destroy")

    async def mute(self, user_id: str, options: MuteOptions | None = None) -> Relationship:
        """Mute one recorded content category and return the server's relationship state."""
        return await self._muting(user_id, options, "mute")

    async def unmute(self, user_id: str, options: MuteOptions | None = None) -> Relationship:
        """Unmute one recorded content category and return the server's relationship state."""
        return await self._muting(user_id, options, "unmute")

    async def mute_quick_snap(self, user_id: str) -> RelationshipActionResult:
        """Mute Quick Snap; its response acknowledges success without returning state."""
        target = self._target(user_id)
        result = await self._graphql_muting.quick_snap(target, "mute")
        return RelationshipActionResult(target, "mute_quick_snap", raw=result)

    async def unmute_quick_snap(self, user_id: str) -> RelationshipActionResult:
        """Unmute Quick Snap without inferring a relationship flag absent from the reply."""
        target = self._target(user_id)
        result = await self._graphql_muting.quick_snap(target, "unmute")
        return RelationshipActionResult(target, "unmute_quick_snap", raw=result)

    async def notifications(
        self, user_id: str, options: NotificationOptions
    ) -> RelationshipActionResult:
        """Change one category's notifications and return the server acknowledgement."""
        target = self._target(user_id)
        if not isinstance(options, NotificationOptions):
            raise ValueError("options must be NotificationOptions.")
        session = self._transport.session
        preference = {"all": "all", "relevant": "default", "off": "none"}[options.mode]
        if options.content == "live":
            result = await self._post_form(
                f"live/{target}/set_subscription_preference/",
                {**options.parameters, "preference": preference, "_uuid": session.device.id},
            )
            return RelationshipActionResult(target, "notifications", raw=result)
        endpoints = {
            "posts": "favorite",
            "stories": "favorite_for_stories",
            "reels": "favorite_for_clips",
        }
        result = await self._post(
            f"friendships/{endpoints[options.content]}/{target}/",
            {
                **options.parameters,
                "preference": preference,
                "user_id": target,
                "_uid": session.user_id or "",
                "_uuid": session.device.id,
            },
        )
        return RelationshipActionResult(target, "notifications", raw=result)

    async def restrict(self, user_id: str, options: ActionOptions | None = None) -> Relationship:
        """Restrict one person and return the matching server relationship state."""
        target = self._target(user_id)
        settings = self._action_options(options)
        session = self._transport.session
        result = await self._post(
            "restrict_action/restrict_many/",
            {
                **settings.parameters,
                "user_ids": target,
                "_uid": session.user_id or "",
                "_uuid": session.device.id,
                "container_module": settings.container_module,
            },
        )
        return self._returned_relationship(result, target)

    async def unrestrict(self, user_id: str, options: ActionOptions | None = None) -> Relationship:
        """Unrestrict one person using the recorded unsigned form."""
        target = self._target(user_id)
        settings = self._action_options(options)
        result = await self._post_form(
            "restrict_action/unrestrict/",
            {
                **settings.parameters,
                "_uuid": self._transport.session.device.id,
                "target_user_id": target,
                "container_module": settings.container_module,
            },
        )
        return self._returned_relationship(result, target)

    async def block(self, user_id: str, options: BlockOptions | None = None) -> Relationship:
        """Block a person and return server state, preserving the chosen blocking scope."""
        target = self._target(user_id)
        settings = BlockOptions() if options is None else options
        if not isinstance(settings, BlockOptions):
            raise ValueError("options must be BlockOptions.")
        session = self._transport.session
        result = await self._post(
            f"friendships/block/{target}/",
            {
                **settings.parameters,
                "surface": settings.surface,
                "is_auto_block_enabled": str(settings.auto_block).lower(),
                "user_id": target,
                "_uid": session.user_id or "",
                "_uuid": session.device.id,
                "container_module": settings.container_module,
            },
        )
        return self._relationship(result.get("friendship_status"), target)

    async def unblock(self, user_id: str, options: ActionOptions | None = None) -> Relationship:
        """Unblock one person with a fresh request ID and return server state."""
        target = self._target(user_id)
        settings = (
            ActionOptions(container_module="blocked_list")
            if options is None
            else self._action_options(options)
        )
        session = self._transport.session
        result = await self._post(
            f"friendships/unblock/{target}/",
            {
                **settings.parameters,
                "client_request_id": str(uuid4()),
                "user_id": target,
                "_uid": session.user_id or "",
                "_uuid": session.device.id,
                "container_module": settings.container_module,
            },
        )
        return self._relationship(result.get("friendship_status"), target)

    async def add_close_friend(
        self, user_id: str, options: CloseFriendsOptions | None = None
    ) -> RelationshipActionResult:
        """Add one close friend; preserve all unrelated list members."""
        return await self._close_friend(user_id, options, "add_member")

    async def remove_close_friend(
        self, user_id: str, options: CloseFriendsOptions | None = None
    ) -> RelationshipActionResult:
        """Remove only the chosen close friend without modifying other members."""
        return await self._close_friend(user_id, options, "remove_member")

    async def add_favorite(
        self, user_id: str, options: FavoritesOptions | None = None
    ) -> Relationship:
        """Add one feed favorite; this is distinct from post notifications."""
        return await self._favorite(user_id, options, "add")

    async def remove_favorite(
        self, user_id: str, options: FavoritesOptions | None = None
    ) -> Relationship:
        """Remove one feed favorite while preserving all other favorites."""
        settings = FavoritesOptions(module="favorites_management") if options is None else options
        return await self._favorite(user_id, settings, "remove")

    async def approve_request(
        self, user_id: str, options: ActionOptions | None = None
    ) -> Relationship:
        """Approve this person's incoming follow request once.

        The captured consent/friction fields remain explicit parameter overrides in
        ActionOptions; defaults reproduce the observed notification-screen request.
        """
        target = self._target(user_id)
        settings = (
            ActionOptions(container_module="newsfeed_you")
            if options is None
            else self._action_options(options)
        )
        session = self._transport.session
        result = await self._post(
            f"friendships/approve/{target}/",
            {
                "include_follow_friction_check": "0",
                "check_pastis": "true",
                "has_seen_uk_osa_prompt": "true",
                **settings.parameters,
                "user_id": target,
                "radio_type": settings.radio_type,
                "_uid": session.user_id or "",
                "_uuid": session.device.id,
                "container_module": settings.container_module,
            },
        )
        return self._relationship(result.get("friendship_status"), target)

    async def remove_follower(
        self, user_id: str, options: ActionOptions | None = None
    ) -> Relationship:
        """Remove this person from your followers and return actual server state."""
        settings = (
            ActionOptions(container_module="self_followers")
            if options is None
            else self._action_options(options)
        )
        return await self._following(user_id, settings, "remove_follower")

    async def decline_request(
        self, user_id: str, options: ActionOptions | None = None
    ) -> Relationship:
        """Decline this person's incoming follow request without changing other requests."""
        settings = (
            ActionOptions(container_module="friending_center")
            if options is None
            else self._action_options(options)
        )
        return await self._following(user_id, settings, "ignore")

    async def _favorite(
        self, user_id: str, options: FavoritesOptions | None, action: str
    ) -> Relationship:
        target = self._target(user_id)
        settings = FavoritesOptions() if options is None else options
        if not isinstance(settings, FavoritesOptions):
            raise ValueError("options must be FavoritesOptions.")
        session = self._transport.session
        result = await self._post(
            "friendships/update_feed_favorites/",
            {
                **settings.parameters,
                "block_on_empty_thread_creation": str(
                    settings.block_on_empty_thread_creation
                ).lower(),
                "module": settings.module,
                "_uid": session.user_id or "",
                "_uuid": session.device.id,
                "add": [target] if action == "add" else [],
                "remove": [target] if action == "remove" else [],
            },
        )
        try:
            rows = result["user_id_and_relationship_infos"]
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError
            row = rows[0]
            if not isinstance(row, dict) or _identifier(row["user_id"]) != target:
                raise ValueError
            return self._relationship(row["relationship_info"], target)
        except (KeyError, ValueError):
            raise ProtocolError(
                "Favorite response did not contain the requested person's state."
            ) from None

    async def _close_friend(
        self, user_id: str, options: CloseFriendsOptions | None, action: str
    ) -> RelationshipActionResult:
        target = self._target(user_id)
        settings = CloseFriendsOptions() if options is None else options
        if not isinstance(settings, CloseFriendsOptions):
            raise ValueError("options must be CloseFriendsOptions.")
        result = await self._post_form(
            f"stories/private_stories/{action}/",
            {
                **settings.parameters,
                "module": settings.module,
                "source": settings.source,
                "user_id": target,
                "_uuid": self._transport.session.device.id,
            },
        )
        method = "add_close_friend" if action == "add_member" else "remove_close_friend"
        return RelationshipActionResult(target, method, raw=result)

    async def _following(self, user_id: str, settings: ActionOptions, action: str) -> Relationship:
        target = self._target(user_id)
        session = self._transport.session
        fields = {
            **settings.parameters,
            "include_follow_friction_check": settings.parameters.get(
                "include_follow_friction_check",
                "0" if action in {"remove_follower", "ignore"} else "1",
            ),
            "user_id": target,
            "radio_type": settings.radio_type,
            "_uid": session.user_id or "",
            "_uuid": session.device.id,
            "container_module": settings.container_module,
        }
        if action == "create":
            fields["device_id"] = session.device.android_id
        result = await self._post(f"friendships/{action}/{target}/", fields)
        return self._relationship(result.get("friendship_status"), target)

    async def _muting(self, user_id: str, options: MuteOptions | None, action: str) -> Relationship:
        target = self._target(user_id)
        settings = MuteOptions() if options is None else options
        if not isinstance(settings, MuteOptions):
            raise ValueError("options must be MuteOptions.")
        if settings.content in {"activity", "map_notes", "map_location"}:
            return await self._graphql_muting.change(target, settings, action)
        field = "target_posts_author_id" if settings.content == "posts" else "target_reel_author_id"
        endpoint = f"friendships/{action}_posts_or_story_from_follow/"
        if settings.content == "notes":
            field = "user_id"
            endpoint = f"friendships/{action}_note/"
        session = self._transport.session
        fields = {
            **settings.parameters,
            "_uid": session.user_id or "",
            "_uuid": session.device.id,
            field: target,
            "container_module": settings.container_module,
        }
        result = await self._post(endpoint, fields)
        return self._relationship(result.get("friendship_status"), target)

    def _target(self, user_id: str) -> str:
        if not self._transport.session.authenticated:
            raise LoginRequired(
                "Load your own authenticated session before changing relationships."
            )
        if (
            not isinstance(user_id, str)
            or not user_id.isascii()
            or not user_id.isdigit()
            or int(user_id) <= 0
        ):
            raise ValueError("user_id must be a positive numeric ASCII string.")
        return str(int(user_id))

    @staticmethod
    def _action_options(options: ActionOptions | None) -> ActionOptions:
        settings = ActionOptions() if options is None else options
        if not isinstance(settings, ActionOptions):
            raise ValueError("options must be ActionOptions.")
        return settings

    async def _post(self, endpoint: str, fields: dict[str, Any]) -> dict[str, Any]:
        return await self._post_form(
            endpoint,
            {"signed_body": "SIGNATURE." + json.dumps(fields, separators=(",", ":"))},
        )

    async def _post_form(self, endpoint: str, fields: dict[str, str]) -> dict[str, Any]:
        result = await self._transport.request("POST", endpoint, fields)
        if result.get("error") is not None:
            raise APIError("Instagram returned an error for the relationship change.")
        if result.get("status") != "ok":
            raise ProtocolError("Relationship change did not confirm successful status.")
        return result

    @classmethod
    def _returned_relationship(cls, result: dict[str, Any], user_id: str) -> Relationship:
        users = result.get("users")
        if not isinstance(users, list) or len(users) != 1:
            raise ProtocolError("Relationship response did not return the requested person.")
        try:
            person = PersonSummary.from_dict(users[0])
        except ValueError:
            raise ProtocolError("Relationship response contained an invalid person.") from None
        if person.id != user_id or person.relationship is None:
            raise ProtocolError("Relationship response did not match the requested person.")
        return cls._relationship(person.relationship.raw, user_id)

    @staticmethod
    def _relationship(value: Any, user_id: str) -> Relationship:
        state_fields = {field.name for field in fields(Relationship)} - {"user_id", "raw"}
        if not isinstance(value, dict) or not state_fields.intersection(value):
            raise ProtocolError("Relationship response did not contain relationship state.")
        try:
            return Relationship.from_dict(value, user_id)
        except ValueError:
            raise ProtocolError("Relationship response contained invalid state.") from None
