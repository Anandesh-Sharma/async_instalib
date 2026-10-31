"""Bounded follower/following reads from recorded Android GraphQL list screens."""

from typing import Any, Literal

from ..config.people import MutualFollowersOptions, PeopleListOptions
from ..exceptions import LoginRequired, ProtocolError
from ..models.pagination import Continuation, Page, PageContext
from ..models.people import PersonSummary, _identifier
from ..transport import Transport
from ..transport.graphql import GraphQLRequest

_Operation = Literal["followers", "following"]

# The app sends these optional request fields explicitly null on following-list reads.
_FOLLOWING_NULL_FIELDS = (
    "username",
    "support_new_api",
    "absolute_url_processed",
    "phone_id",
    "target_id",
    "page_size",
    "login_attempt_count",
    "module",
    "limit",
    "ig_sig_key_version",
    "first",
    "device_id",
    "surface",
    "search_client_type",
    "ig_sig",
    "csrfmiddlewaretoken",
    "query",
    "adid",
    "guid",
    "rank_mutual",
    "choice",
    "_uuid",
    "_uid",
    "_csrftoken",
)


class PeopleLists:
    """Return one accessible provider page; never crawl lists or change relationships."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def followers(
        self, user_id: str, options: PeopleListOptions | None = None
    ) -> Page[PersonSummary]:
        """Read one accessible follower page with an account/target/query-bound cursor.

        Query filtering was recorded on the account owner's follower list. The
        caller's target ID remains explicit; accessible results depend on Instagram.
        Provider restriction flags remain in raw. An exhausted accessible page does
        not imply that all of the person's followers are visible. The app itself can
        receive overlapping ranked pages; every returned row is preserved.
        """
        return await self._read("followers", user_id, options)

    async def following(
        self, user_id: str, options: PeopleListOptions | None = None
    ) -> Page[PersonSummary]:
        """Read one following page, optionally using its account/target-bound cursor."""
        return await self._read("following", user_id, options)

    async def mutual_followers(
        self, user_id: str, options: MutualFollowersOptions | None = None
    ) -> Page[PersonSummary]:
        """Read a bounded mutual-follower preview, excluding suggested accounts.

        The provider's See all followers button is a navigation hint, not a mutual
        list cursor. This recorded operation has no continuation contract or total.
        """
        session = self._transport.session
        if not session.authenticated:
            raise LoginRequired("Load an authenticated session before reading mutual followers.")
        identifier = _identifier(user_id)
        settings = MutualFollowersOptions() if options is None else options
        if not isinstance(settings, MutualFollowersOptions):
            raise ValueError("options must be MutualFollowersOptions.")
        result = await self._transport.request(
            "POST",
            "discover/surface_with_su/",
            {
                "module": settings.module,
                "target_id": identifier,
                "_uuid": session.device.id,
                "mutual_followers_limit": str(settings.limit),
            },
        )
        users = result.get("mutual_followers")
        if not isinstance(users, list) or any(not isinstance(user, dict) for user in users):
            raise ProtocolError("Mutual-follower response contained an invalid people list.")
        if result.get("next_max_id") or result.get("has_more") or result.get("more_available"):
            raise ProtocolError("Mutual-follower response requires an unestablished continuation.")
        try:
            return Page(tuple(PersonSummary.from_dict(user) for user in users), raw=result)
        except ValueError:
            raise ProtocolError("Mutual-follower response contained an invalid person.") from None

    async def _read(
        self, operation: _Operation, user_id: str, options: PeopleListOptions | None
    ) -> Page[PersonSummary]:
        session = self._transport.session
        if not session.authenticated or not session.user_id:
            raise LoginRequired("Load an authenticated session before reading person lists.")
        identifier = _identifier(user_id)
        settings = PeopleListOptions() if options is None else options
        if not isinstance(settings, PeopleListOptions):
            raise ValueError("options must be PeopleListOptions.")
        context = PageContext(operation, session.user_id, target=identifier, query=settings.query)
        if settings.cursor is not None:
            settings.cursor.validate_for(context)
        if settings.query is not None:
            if any(ord(character) < 32 or ord(character) == 127 for character in settings.query):
                raise ValueError("List query must not contain control characters.")
        variables = self._variables(operation, identifier, settings)
        doc_id = (
            "284797047911918316998205836755"
            if operation == "followers"
            else "16104639286363954576550227636"
        )
        envelope = await self._transport.graphql(
            GraphQLRequest(
                "FollowersList" if operation == "followers" else "FollowingList",
                variables,
                client_doc_id=doc_id,
            )
        )
        data = envelope.get("data")
        value = (
            data.get(f"xdt_api__v1__friendships__{operation}") if isinstance(data, dict) else None
        )
        if not isinstance(value, dict):
            raise ProtocolError("Person-list response did not contain its list object.")
        users = value.get("users")
        if not isinstance(users, list) or any(not isinstance(user, dict) for user in users):
            raise ProtocolError("Person-list response contained an invalid user list.")
        try:
            people = tuple(PersonSummary.from_dict(user) for user in users)
        except ValueError:
            raise ProtocolError("Person-list response contained an invalid person.") from None
        if "next_max_id" not in value:
            raise ProtocolError("Person-list response omitted its continuation state.")
        cursor = value["next_max_id"]
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise ProtocolError("Person-list response contained an invalid continuation.")
        continuation = Continuation(cursor, context) if cursor is not None else None
        return Page(people, continuation=continuation, raw=value)

    @staticmethod
    def _variables(
        operation: _Operation, identifier: str, settings: PeopleListOptions
    ) -> dict[str, Any]:
        variables: dict[str, Any] = {
            "user_id": identifier,
            "skip_suggested_users": True,
            "skip_more_groups_available": True,
            "skip_friendship_followers_fields": operation == "followers",
            "skip_page_size": True,
            "skip_pending_admins": True,
            "skip_has_more": True,
            "query": settings.query or "",
            "skip_big_list": True,
            # Captured app replies error on these optional, unmodeled fields.
            "include_unseen_count": False,
        }
        if operation == "followers":
            variables.update(
                request_data={"rank_token": "", "enableGroups": True},
                search_surface="follow_list_page",
            )
        else:
            variables.update(
                skip_use_clickable_see_more=True,
                skip_preview_hashtags=True,
                skip_should_limit_list_of_followers=True,
                skip_friend_requests=True,
                include_profile_update_info=False,
                enable_groups=True,
                skip_hashtag_count=True,
                request_data={
                    **dict.fromkeys(_FOLLOWING_NULL_FIELDS),
                    "rank_token": "",
                    "supported_capabilities_new": [],
                    "includes_hashtags": True,
                    "search_surface": "follow_list_page",
                },
            )
        if settings.cursor is not None:
            variables["max_id"] = settings.cursor.value
        return variables
