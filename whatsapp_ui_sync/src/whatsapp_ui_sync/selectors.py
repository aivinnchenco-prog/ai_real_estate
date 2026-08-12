"""Selector strategy for WhatsApp Web — prefer ARIA / visible text.

Native Lists dialog (UA/RU/EN) after chat menu → Add to list.
Never auto-create lists (+ Новий список).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UiSelectors:
    """Logical selector intents (resolved via get_by_role / get_by_text)."""

    qr_accessible_names: tuple[str, ...] = (
        "Scan this QR code",
        "Scan QR code",
        "QR code",
    )
    main_search_placeholder_substrings: tuple[str, ...] = (
        "Search",
        "Поиск",
        "Пошук",
        "Buscar",
    )
    chat_list_hints: tuple[str, ...] = ("Chats", "Чаты", "Бесіди", "Chats")

    # Migration banner Labels → Lists
    continue_labels: tuple[str, ...] = (
        "Продовжити",
        "Продолжить",
        "Continue",
    )

    # Chat overflow menu → open list picker
    # Newer WA UI uses "Edit list" / "Изменить список" once membership exists;
    # older UI uses "Add to list".
    add_to_list_menu: tuple[str, ...] = (
        "Изменить список",
        "Змінити список",
        "Edit list",
        "Додати в список",
        "Добавить в список",
        "Add to list",
    )

    # List picker dialog title
    select_list_titles: tuple[str, ...] = (
        "Вибрати список",
        "Выбрать список",
        "Select list",
        "Choose list",
    )

    # Confirm / cancel in list picker — NEVER "new list"
    done_labels: tuple[str, ...] = (
        "Готово",
        "Done",
        "OK",
        "Сохранить",
        "Зберегти",
        "Save",
    )
    cancel_labels: tuple[str, ...] = (
        "Скасувати",
        "Отмена",
        "Cancel",
    )
    # Forbidden create-list controls (exact / prefix)
    create_list_forbidden: tuple[str, ...] = (
        "Новий список",
        "Новый список",
        "New list",
        "+ Новий список",
        "+ Новый список",
        "+ New list",
    )

    contact_info_names: tuple[str, ...] = (
        "Contact info",
        "Інформація про контакт",
        "Информация о контакте",
        "Данные контакта",
        "Group info",
        "Business info",
        "Chat info",
    )
    lists_section_names: tuple[str, ...] = (
        "Lists",
        "Списки",
        "Labels",
        "Метки",
        "Ярлыки",
    )

    linked_device_conflict_texts: tuple[str, ...] = (
        "device limit",
        "too many devices",
        "linked devices",
        "слишком много устройств",
        "привязанных устройств",
    )


SELECTORS = UiSelectors()


def exact_list_name_match(visible: str, expected: str) -> bool:
    """Lists must match exactly (Owner / Client), not fuzzy."""
    return (visible or "").strip() == (expected or "").strip()
