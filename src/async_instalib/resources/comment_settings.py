"""Account-wide comment settings, sent as the Bloks requests the settings screens make."""

import json
from typing import Any

from ..config.about import AboutOptions
from ..config.comments import CommentAudience
from ..exceptions import LoginRequired, ProtocolError
from ..session import Session
from ..transport import Transport
from ..transport.graphql import GraphQLRequest

_APP = "com.bloks.www.com.instagram.settings.privacy.comments."
_LABELS = {
    "followers": "Your followers",
    "mutual_followers": "Followers you follow back",
    "off": "Off",
}
# Recorded values: posts name the audience, stories number it.
_POST_AUDIENCE = {"followers": "follower", "mutual_followers": "mutual_followers", "off": "off"}
_STORY_AUDIENCE = {"followers": 1, "mutual_followers": 3, "off": 7}


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


class CommentSettings:
    """Who can comment, which comment types are allowed, and blocked commenters.

    These change the whole account, not one post. The recorded screens offered the
    audiences of a private account; "everyone" was not offered and is not supported.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def allow_comments_from(self, audience: CommentAudience) -> None:
        """Choose who can comment on your posts and reels."""
        self._audience(audience)
        await self._handler(
            "set_audience_control",
            {
                "audience_control": _POST_AUDIENCE[audience],
                "updated_value": _LABELS[audience],
                "should_update_carbon_state": 0,
            },
        )

    async def allow_story_comments_from(self, audience: CommentAudience) -> None:
        """Choose who can comment on your stories."""
        self._audience(audience)
        await self._handler(
            "set_hype_audience_control_handler",
            {
                "audience_control": _STORY_AUDIENCE[audience],
                "updated_value": _LABELS[audience],
                "should_update_global_state": 0,
            },
        )

    async def allow_gif_comments(self, enabled: bool) -> None:
        """Allow or refuse GIF comments on your posts and reels."""
        self._flag(enabled)
        await self._handler(
            "set_gif_comment_settings_handler", {"are_gif_comments_disabled": int(not enabled)}
        )

    async def allow_photo_comments(self, enabled: bool) -> None:
        """Allow or refuse photo comments on your posts and reels."""
        self._flag(enabled)
        await self._handler(
            "set_photo_comment_settings_handler", {"enable_photo_comments": int(enabled)}
        )

    async def block_commenter(self, user_id: str) -> None:
        """Hide this person's new comments from everyone but them."""
        await self._blocked_commenter(user_id, "block")

    async def unblock_commenter(self, user_id: str) -> None:
        """Let this person's comments show again."""
        await self._blocked_commenter(user_id, "unblock")

    async def _handler(self, name: str, server_params: dict[str, Any]) -> None:
        session = self._session()
        variables = {
            "params": {
                "params": _json({"params": _json({"server_params": server_params})}),
                "bloks_versioning_id": session.profile.bloks_version,
                "infra_params": {"device_id": session.device.id},
                "app_id": _APP + name,
            },
            "bk_context": AboutOptions().bloks_context(),
        }
        result = await self._transport.graphql(
            GraphQLRequest(
                "IGBloksAppRootQuery-" + _APP + name,
                variables,
                client_doc_id="356548512611906258864738750706",
                route="graphql_www",
                purpose="fetch",
                legacy_root_field="bloks_action",
            )
        )
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("bloks_action"), dict):
            raise ProtocolError("Comment setting response did not contain its action.")

    async def _blocked_commenter(self, user_id: str, status: str) -> None:
        if not isinstance(user_id, str) or not user_id.isascii() or not user_id.isdigit():
            raise ValueError("user_id must be a positive numeric ASCII string.")
        session = self._session()
        version = session.profile.bloks_version
        form = {"block_status": status, "user_id": str(int(user_id)), "_uuid": session.device.id}
        if status == "block":
            form["should_revalidate_blocked_commenters_screen"] = "0"
        context = AboutOptions()
        form["bk_client_context"] = _json(
            {
                "bloks_version": version,
                "styles_id": "instagram",
                "theme_params": [
                    {"value": [context.theme], "design_system_name": context.design_system}
                ],
            }
        )
        form["bloks_versioning_id"] = version
        endpoint = "bloks/apps/com.instagram.settings.privacy.comments.set_blocked_commenters/"
        result = await self._transport.request("POST", endpoint, form)
        # The app asks for a serialized "payload"; without that header the screen is "layout".
        if result.get("status") != "ok" or not isinstance(result.get("layout"), dict):
            raise ProtocolError("Blocked commenters response did not contain its screen.")

    def _session(self) -> Session:
        session = self._transport.session
        if not session.authenticated or not session.user_id:
            raise LoginRequired("Load your own authenticated session before changing settings.")
        return session

    @staticmethod
    def _audience(value: object) -> None:
        if value not in _LABELS:
            raise ValueError("audience must be followers, mutual_followers, or off.")

    @staticmethod
    def _flag(value: object) -> None:
        if type(value) is not bool:
            raise ValueError("enabled must be a boolean.")
