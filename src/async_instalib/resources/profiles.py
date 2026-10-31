"""Profile reads from the recorded Android info_stream endpoint."""

import json
from typing import Any
from urllib.parse import urlencode

from ..config.about import AboutOptions
from ..config.content import ProfileOptions, RelatedOptions
from ..config.metadata import AboutDecodeOptions
from ..exceptions import APIError, LoginRequired, PersonNotFound, ProtocolError
from ..models.about import AboutAccount
from ..models.pagination import Page
from ..models.people import PersonProfile, PersonSummary, _identifier
from ..transport import Transport
from ..transport.graphql import GraphQLRequest


class Profiles:
    """Merge progressive profile documents without losing identity or partial fields."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def by_id(self, user_id: str, options: ProfileOptions | None = None) -> PersonProfile:
        """Read an accessible profile by numeric ID; never changes its relationship."""
        if options is not None and not isinstance(options, ProfileOptions):
            raise ValueError("options must be ProfileOptions or None.")
        identifier = _identifier(user_id)
        session = self._transport.session
        if not session.authenticated:
            raise LoginRequired("Load an authenticated session before reading profiles.")
        settings = options or ProfileOptions()
        records = await self._transport.request_stream(
            "POST",
            f"users/{identifier}/info_stream/",
            {
                "module": settings.module,
                "entry_point": settings.entry_point,
                "_uuid": session.device.id,
                "push_disabled": str(settings.push_disabled).lower(),
            },
        )
        return self._profile(records, user_id=identifier)

    async def by_username(
        self, username: str, options: ProfileOptions | None = None
    ) -> PersonProfile:
        """Read the recorded username stream without a search request or guessed ID.

        Explicit ProfileOptions apply to an additional by-ID read, preserving their
        existing module/entry/push-disabled behavior. A plain lookup uses one request.
        An ordinary HTTP 404 raises PersonNotFound; authentication errors stay typed.
        """
        if not isinstance(username, str):
            raise ValueError("username must be a string.")
        if options is not None and not isinstance(options, ProfileOptions):
            raise ValueError("options must be ProfileOptions.")
        name = username.removeprefix("@")
        if (
            not name
            or name in {".", ".."}
            or not name.isascii()
            or any(not (character.isalnum() or character in "_.") for character in name)
        ):
            raise ValueError("username must contain ASCII letters, digits, dots, or underscores.")
        name = name.lower()
        session = self._transport.session
        if not session.authenticated:
            raise LoginRequired("Load an authenticated session before reading profiles.")
        try:
            records = await self._transport.request_stream(
                "POST",
                f"users/{name}/usernameinfo_stream/",
                {
                    "entry_point": "profile",
                    "from_module": "deep_link_util",
                    "_uuid": session.device.id,
                },
            )
        except APIError as error:
            if error.info.status_code == 404:
                raise PersonNotFound(
                    "No accessible profile was returned for this username.", error.info
                ) from None
            raise
        result = self._profile(records, username=name)
        return await self.by_id(result.id, options) if options is not None else result

    async def related(
        self, user_id: str, options: RelatedOptions | None = None
    ) -> Page[PersonSummary]:
        """Read one set of suggested accounts shown alongside an accessible profile.

        ``has_chaining`` describes recommendation availability, not pagination.
        This operation does not follow suggested accounts or request more pages.
        """
        if options is not None and not isinstance(options, RelatedOptions):
            raise ValueError("options must be RelatedOptions or None.")
        identifier = _identifier(user_id)
        if not self._transport.session.authenticated:
            raise LoginRequired("Load an authenticated session before reading related accounts.")
        settings = options or RelatedOptions()
        parameters = {
            "consumption_surface": "profile_hscroll",
            "module": "profile",
            "from_module": "serp",
            "profile_chaining_check": "true",
            "eligible_for_threads_cta": "false",
            "request_entry_point": "profile_auto_expand",
            **settings.parameters,
            "target_id": identifier,
        }
        if settings.username is not None:
            parameters["target_username"] = settings.username
        result = await self._transport.request("GET", f"discover/chaining/?{urlencode(parameters)}")
        try:
            users = result["users"]
            if not isinstance(users, list):
                raise ValueError
            target = result.get("user")
            if target is not None:
                if not isinstance(target, dict):
                    raise ValueError
                for key in ("pk", "id"):
                    if key in target and _identifier(target[key]) != identifier:
                        raise ValueError
            return Page(tuple(PersonSummary.from_dict(user) for user in users), raw=result)
        except (KeyError, TypeError, ValueError):
            raise ProtocolError("Related-accounts response contained invalid people.") from None

    async def about(self, user_id: str, options: AboutOptions | None = None) -> dict[str, Any]:
        """Read the About this account Bloks envelope without executing UI actions.

        Returns the complete GraphQL envelope. Its screen lives at
        data.bloks_app.screen_content; the bundle tree remains an opaque string.
        Dates, country, and username history are not inferred from localized UI text.
        """
        if options is not None and not isinstance(options, AboutOptions):
            raise ValueError("options must be AboutOptions or None.")
        identifier = _identifier(user_id)
        if not self._transport.session.authenticated:
            raise LoginRequired("Load an authenticated session before reading account metadata.")
        settings = options or AboutOptions()
        result = await self._transport.graphql(
            GraphQLRequest(
                "IGBloksAppRootQuery-com.bloks.www.ig.about_this_account",
                self._about_variables(identifier, settings),
                client_doc_id="25336029838367092199423836565",
                route="graphql_www",
                purpose="fetch",
            )
        )
        try:
            screen = result["data"]["bloks_app"]["screen_content"]
            tree = screen["component"]["bundle"]["bloks_bundle_tree"]
            if not isinstance(tree, str) or not tree:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ProtocolError("About response did not contain its Bloks screen bundle.") from None
        return result

    async def about_details(
        self,
        user_id: str,
        options: AboutOptions | None = None,
        decode_options: AboutDecodeOptions | None = None,
    ) -> AboutAccount:
        """Decode named About state without deriving fields from localized UI labels."""
        if decode_options is not None and not isinstance(decode_options, AboutDecodeOptions):
            raise ValueError("decode_options must be AboutDecodeOptions or None.")
        identifier = _identifier(user_id)
        response = await self.about(identifier, options)
        try:
            return AboutAccount.from_dict(response, identifier, decode_options)
        except ValueError:
            raise ProtocolError("About response contained an invalid structured bundle.") from None

    @staticmethod
    def _profile(
        records: tuple[dict[str, Any], ...], user_id: str | None = None, username: str | None = None
    ) -> PersonProfile:
        merged: dict[str, Any] = {}
        identifier = user_id
        for record in records:
            value = record.get("user")
            if not isinstance(value, dict):
                raise ProtocolError("Profile stream document did not contain a user.")
            try:
                for key in ("pk", "id"):
                    if key in value:
                        current = _identifier(value[key])
                        if identifier is not None and current != identifier:
                            raise ValueError
                        identifier = current
                if username is not None and "username" in value:
                    if (
                        not isinstance(value["username"], str)
                        or value["username"].lower() != username
                    ):
                        raise ValueError
            except ValueError:
                raise ProtocolError(
                    "Profile stream returned a different account identity."
                ) from None
            merged.update(value)
        try:
            result = PersonProfile.from_dict(merged)
        except ValueError:
            raise ProtocolError("Profile stream contained invalid person fields.") from None
        if result.id != identifier or (
            username is not None and result.username.lower() != username
        ):
            raise ProtocolError("Profile stream returned a different account identity.")
        return result

    def _about_variables(self, identifier: str, settings: AboutOptions) -> dict[str, Any]:
        session = self._transport.session
        return {
            "params": {
                "params": json.dumps(
                    {"referer_type": settings.referer_type, "target_user_id": identifier},
                    separators=(",", ":"),
                ),
                "bloks_versioning_id": session.profile.bloks_version,
                "infra_params": {"device_id": session.device.id},
                "app_id": "com.bloks.www.ig.about_this_account",
            },
            "bk_context": settings.bloks_context(),
        }
