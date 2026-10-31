"""Location search with the app's recorded place-picker query."""

from uuid import uuid4

from ..exceptions import ProtocolError
from ..models.pagination import Page
from ..models.posts import Place
from ..transport import Transport
from ..transport.graphql import GraphQLRequest


class Places:
    """Find places to tag on a post, reel, or edit."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def search(self, query: str) -> Page[Place]:
        """Return one page of places matching ``query``; Instagram chooses the count."""
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("query must be nonempty text of at most 200 characters.")
        result = await self._transport.graphql(
            GraphQLRequest(
                "IGPlaceSearchQuery",
                {
                    "request": {
                        "entry_point": "DEFAULT",
                        "pinned_meta_place_id": None,
                        "latitude": None,
                        "longitude": None,
                        "rank_token": str(uuid4()),
                        "search_query": query,
                    }
                },
                client_doc_id="182496963816957186458261742509",
                route="graphql_www",
                legacy_root_field="ig_place_search",
            )
        )
        try:
            places = result["data"]["ig_place_search"]["meta_places"]
            if not isinstance(places, list):
                raise ValueError
            return Page(tuple(Place.from_dict(item) for item in places), raw=result)
        except (KeyError, TypeError, ValueError):
            raise ProtocolError("Place search response did not contain places.") from None
