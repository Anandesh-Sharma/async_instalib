"""Person search backed by recorded Accounts and typeahead request shapes."""

from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from ..config.content import ProfileOptions
from ..config.people import SearchOptions
from ..exceptions import LoginRequired, ProtocolError
from ..models.pagination import Continuation, Page, PageContext
from ..models.people import PersonProfile, PersonSummary
from ..transport import Transport
from ..transport.graphql import GraphQLRequest
from .content import ProfileContent
from .people_lists import PeopleLists
from .profiles import Profiles
from .relationships import Relationships


class People:
    """Read person search results through the client's shared authenticated transport."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self.profiles = Profiles(transport)
        self.content = ProfileContent(transport)
        self.relationships = Relationships(transport)
        self.lists = PeopleLists(transport)

    async def by_username(
        self, username: str, options: ProfileOptions | None = None
    ) -> PersonProfile:
        """Read a username directly, accepting @ and case-insensitive spelling.

        With no options, sends one username-stream request. Explicit ProfileOptions
        retain their ID-profile semantics through one additional profile read.
        """
        return await self.profiles.by_username(username, options)

    async def search(self, query: str, options: SearchOptions | None = None) -> Page[PersonSummary]:
        """Read one Accounts search page, with an explicit account/query-bound continuation.

        ``limit`` is a requested server count, not a guarantee. Each call makes one
        request; rank, page index, and both search session IDs persist across pages.
        """
        account_id = self._validate_query(query)
        options = SearchOptions() if options is None else options
        if not isinstance(options, SearchOptions):
            raise ValueError("options must be SearchOptions.")
        context = PageContext("search", account_id, query=query)
        cursor = options.cursor
        if cursor is not None:
            cursor.validate_for(context)
            if (
                cursor.search_session_id is None
                or cursor.serp_session_id is None
                or cursor.rank_token is None
                or cursor.page_index is None
            ):
                raise ValueError("Cursor is missing search continuation metadata.")
            search_session_id = cursor.search_session_id
            serp_session_id = cursor.serp_session_id
            page_index = cursor.page_index
        else:
            search_session_id, serp_session_id = str(uuid4()), str(uuid4())
            page_index = 0
        parameters = {
            "search_surface": "user_serp",
            "serp_session_id": serp_session_id,
            "search_session_id": search_session_id,
            "timezone_offset": str(self._transport.session.profile.timezone_offset),
            "enable_metadata": "false",
            "serp_origination": "keyboard",
            "keyword_context": "null",
            "count": str(options.limit),
            "query": query,
            "session_state": "typeahead",
            "disable_ads": "true",
            "enable_lightweight_query_with_basic_mmc_and_media_grid": "true",
        }
        if cursor is not None:
            parameters.update(
                next_max_id=cursor.value,
                page_token=cursor.value,
                rank_token=cursor.rank_token or "",
                page_index=str(page_index),
            )
        result = await self._transport.request(
            "GET", "fbsearch/account_serp/?" + urlencode(parameters)
        )
        has_more = result.get("has_more")
        if type(has_more) is not bool:
            raise ProtocolError("Accounts search response lacks a boolean has_more field.")
        items = self._parse_users(result.get("users"))
        continuation = None
        if has_more:
            token, rank_token = result.get("page_token"), result.get("rank_token")
            if (
                not isinstance(token, str)
                or not token
                or not isinstance(rank_token, str)
                or not rank_token
            ):
                raise ProtocolError("Accounts search response lacks its continuation tokens.")
            continuation = Continuation(
                token,
                context,
                rank_token=rank_token,
                search_session_id=search_session_id,
                serp_session_id=serp_session_id,
                page_index=page_index + 1,
            )
        # Final pages may still carry a token; has_more determines whether it is usable.
        return Page(items=items, continuation=continuation, raw=result)

    async def suggest(self, query: str) -> Page[PersonSummary]:
        """Read person-only typeahead suggestions; keyword and hashtag rows are excluded.

        Instagram chooses the result count. This recorded query has no pagination or
        count input; use search() for the Accounts results screen.
        """
        self._validate_query(query)
        variables = {
            "skip_over_fetched_keyword_user_fields": True,
            "skip_over_fetched_keyword_fields_batch_3": True,
            "search_type": "SEARCH",
            "skip_over_fetched_user_fields": True,
            "search_surface": "TYPEAHEAD_SEARCH",
            "request_data": {
                "timezone_offset": self._transport.session.profile.timezone_offset,
                "search_session_context": None,
                "search_session_id": str(uuid4()),
                "requested_result_types": ["KEYWORD", "USER", "HASHTAG", "OVERFETCHED_KEYWORDS"],
                "lng": None,
                "typeahead_session_id": str(uuid4()),
                "query": query,
                "enable_account_super_keywords": True,
                "lat": None,
                "client_uuid_rank_token": str(uuid4()),
            },
            "include_search_social_context": False,
            "include_live_broadcast_id": False,
            "include_profile_update_info": True,
        }
        result = await self._transport.graphql(
            GraphQLRequest(
                "TypeaheadSearchQuery", variables, client_doc_id="702998554512640115307956645"
            )
        )
        data = result.get("data")
        stream = data.get("xdt_fbsearch__typeahead_stream") if isinstance(data, dict) else None
        rows = stream.get("results") if isinstance(stream, dict) else None
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("__typename"), str) for row in rows
        ):
            raise ProtocolError("Typeahead response lacks its result list.")
        users = [
            row.get("user") for row in rows if row.get("__typename") == "XDTTypeaheadSearchUser"
        ]
        return Page(items=self._parse_users(users), raw=result)

    def _validate_query(self, query: str) -> str:
        session = self._transport.session
        if not session.authenticated or not session.user_id:
            raise LoginRequired("Log in or load your own saved session before searching people.")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a nonempty string.")
        if any(ord(character) < 32 or ord(character) == 127 for character in query):
            raise ValueError("query must not contain control characters.")
        return session.user_id

    @staticmethod
    def _parse_users(users: Any) -> tuple[PersonSummary, ...]:
        if not isinstance(users, list) or any(not isinstance(user, dict) for user in users):
            raise ProtocolError("Person search response contains an invalid user list.")
        try:
            return tuple(PersonSummary.from_dict(user) for user in users)
        except ValueError:
            raise ProtocolError("Person search response contains an invalid person.") from None
