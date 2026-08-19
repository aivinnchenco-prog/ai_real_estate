"""Channel connector abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SendResult:
    ok: bool
    thread_id: str = ""
    thread_url: str = ""
    error: str = ""
    screen_state: str = "UNKNOWN"


@dataclass
class InboundMessage:
    text: str
    thread_id: str


class ChannelConnector(ABC):
  @abstractmethod
  def open_listing(self, url: str, listing_id: str) -> str:
      """Return screen_state."""

  @abstractmethod
  def send_message(self, text: str, *, thread_id: str = "", listing_id: str = "") -> SendResult:
      ...

  @abstractmethod
  def poll_inbound(self, thread_id: str) -> list[InboundMessage]:
      ...

  @abstractmethod
  def thread_contains_sent_text(self, thread_id: str, text: str) -> bool:
      ...
