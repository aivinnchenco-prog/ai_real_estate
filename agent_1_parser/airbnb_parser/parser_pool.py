"""Переиспользование браузера между запросами — не запускать Chrome заново."""

from airbnb_parser import AirbnbParser

_parser: AirbnbParser | None = None


def get_parser(headless: bool = True) -> AirbnbParser:
    global _parser
    if _parser is None:
        _parser = AirbnbParser(headless=headless)
    return _parser


def close_parser():
    global _parser
    if _parser is not None:
        _parser.close()
        _parser = None
