"""Resolve attachment IDs embedded by the rich-text editor."""
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit
from uuid import UUID


def attachment_ids_from_content(content, explicit_ids=()):
    identifiers = dict.fromkeys(str(UUID(str(value))) for value in explicit_ids)

    class References(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in {"img", "a"}:
                return
            for name, value in attrs:
                if name not in {"src", "href"} or not value:
                    continue
                try:
                    for identifier in parse_qs(urlsplit(value).query).get("attachment_id", []):
                        identifiers[str(UUID(identifier))] = None
                except ValueError:
                    continue

    References().feed(content or "")
    return list(identifiers)
