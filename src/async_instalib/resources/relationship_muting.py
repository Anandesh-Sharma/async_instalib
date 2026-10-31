"""Recorded GraphQL muting forms for activity, map content, and Quick Snap."""

from typing import Any, Literal

from ..config.relationships import MuteOptions
from ..exceptions import APIError, ProtocolError
from ..models.people import Relationship
from ..transport import Transport
from ..transport.graphql import GraphQLRequest

# Each operation was recorded with its own inverse; these are not generated document IDs.
_OPERATIONS = {
    ("activity", "mute"): (
        "MuteMediaRepostUser",
        "414453952317846787797149409804",
        "xdt_mute_media_repost_user",
    ),
    ("activity", "unmute"): (
        "UnmuteMediaRepostUser",
        "10341362783250650041331021829",
        "xdt_unmute_media_repost_user",
    ),
    ("map_notes", "mute"): (
        "FriendsMapUserMute",
        "410922747613505651012983125813",
        "xdt_mute_friend_map_user",
    ),
    ("map_notes", "unmute"): (
        "FriendsMapUserUnmute",
        "291357725114377867684511072045",
        "xdt_unmute_friend_map_user",
    ),
    ("map_location", "mute"): (
        "FriendMapMuteUserLocationMutation",
        "193229921913023103313420678044",
        "xig_mute_friend_map_location",
    ),
    ("map_location", "unmute"): (
        "FriendMapUnmuteUserLocationMutation",
        "7689563045696688235702775609",
        "xig_unmute_friend_map_location",
    ),
}


class GraphQLMuting:
    """Serialize observed mutations without fabricating missing relationship flags."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def change(self, target: str, options: MuteOptions, action: str) -> Relationship:
        operation, document, root = _OPERATIONS[(options.content, action)]
        data: dict[str, Any] = {"user_id": target}
        route: Literal["query", "graphql_www"] = "query"
        if options.content == "activity":
            if action == "mute":
                data.update(tracking_token=None, floating_context_item_type=None)
            else:
                data.update(
                    ranking_info_token=None,
                    inventory_source=None,
                    nav_chain=None,
                    event_source=None,
                    container_module=None,
                    carousel_index=None,
                )
        elif options.content == "map_location":
            route = "graphql_www"
            data = {
                "target_user_igid": target,
                "target_user_id": options.fbid_v2,
                "entry_point": options.map_entry_point,
            }
        data.update(options.parameters)
        result = await self._transport.graphql(
            GraphQLRequest(
                operation,
                {"data": data},
                client_doc_id=document,
                route=route,
                legacy_root_field=root if options.content == "activity" else None,
            )
        )
        try:
            state = result["data"][root]
            if options.content != "activity":
                state = state["friendship_status"]
            expected = {
                "activity": "is_muting_media_reposts",
                "map_notes": "is_muting_friend_map",
                "map_location": "is_muting_friend_map_location",
            }[options.content]
            if not isinstance(state, dict) or type(state.get(expected)) is not bool:
                raise ValueError
            return Relationship.from_dict(state, target)
        except (KeyError, TypeError, ValueError):
            raise ProtocolError(
                "Muting response did not contain the expected relationship flag."
            ) from None

    async def quick_snap(self, target: str, action: str) -> dict[str, Any]:
        operation, document, root = {
            "mute": (
                "IGQuickSnapMuteUserMutation",
                "35558766283600583069591271069",
                "xdt_mute_quick_snap_user",
            ),
            "unmute": (
                "IGQuickSnapUnMuteUserMutation",
                "2175173217533977044046084064",
                "xdt_unmute_quick_snap_user",
            ),
        }[action]
        result = await self._transport.graphql(
            GraphQLRequest(
                operation,
                {"input": {"target_user_id": target}},
                client_doc_id=document,
                route="graphql_www",
            )
        )
        data = result.get("data")
        payload = data.get(root) if isinstance(data, dict) else None
        if not isinstance(payload, dict) or "error" not in payload:
            raise ProtocolError("Quick Snap response did not contain its acknowledgement.")
        if payload["error"] is not None:
            raise APIError("Instagram rejected the Quick Snap muting operation.")
        return result
