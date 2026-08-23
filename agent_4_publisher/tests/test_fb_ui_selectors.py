#!/usr/bin/env python3
from __future__ import annotations

from fb_groups_pipeline import is_composer_submit_label
from fb_marketplace_pipeline import (
    LABEL_BEDROOMS,
    LABEL_DATES,
    LABEL_PROPERTY_TYPE,
    LABEL_RENT_OR_SALE,
    NEXT_BUTTON_RE,
    OPTION_RENT,
    PROPERTY_TYPE_OPTIONS,
    PUBLISH_BUTTON_RE,
    debug_shot_name,
)


def test_group_submit_accepts_send_and_post():
    for label in ("Отправить", "отправить", "Send", "Post", "Опубликовать", "Publish", "Надіслати"):
        assert is_composer_submit_label(label), label


def test_group_submit_ignores_anonymous_toggle_and_edit():
    assert is_composer_submit_label("Опубликовать анонимно") is False
    assert is_composer_submit_label("Редактировать всё") is False
    assert is_composer_submit_label("Закрыть") is False
    assert is_composer_submit_label("") is False


def test_marketplace_labels_match_russian_ui():
    assert LABEL_RENT_OR_SALE.search("Продажа или аренда недвижимости")
    assert LABEL_RENT_OR_SALE.search("Home for sale or rent")
    assert LABEL_PROPERTY_TYPE.search("Тип объекта")
    assert LABEL_PROPERTY_TYPE.search("Property type")
    assert LABEL_BEDROOMS.search("Число спален")
    assert LABEL_BEDROOMS.search("Количество спален")
    assert LABEL_BEDROOMS.search("Number of bedrooms")
    assert LABEL_DATES.search("Доступные даты")
    assert LABEL_DATES.search("Available dates")


def test_marketplace_rent_option_is_exact():
    assert OPTION_RENT.match("Аренда")
    assert OPTION_RENT.match("Rent")
    assert OPTION_RENT.match("в аренду")
    assert OPTION_RENT.search("Продажа или аренда недвижимости") is None


def test_marketplace_house_option_includes_villa_and_house():
    house = PROPERTY_TYPE_OPTIONS["house"]
    assert house.search("Дом")
    assert house.search("House")
    assert house.search("Вилла")
    assert house.search("Частный дом")
    assert house.search("Таунхаус") is None
    assert house.search("Townhouse") is None


def test_marketplace_next_and_publish_buttons():
    assert NEXT_BUTTON_RE.match("Далее")
    assert NEXT_BUTTON_RE.match("Next")
    assert PUBLISH_BUTTON_RE.match("Опубликовать")
    assert PUBLISH_BUTTON_RE.match("Отправить")
    assert PUBLISH_BUTTON_RE.match("Publish")
    assert PUBLISH_BUTTON_RE.match("Опубликовать анонимно") is None


def test_debug_shot_name_strips_slash():
    assert "/" not in debug_shot_name("аренда/продажа")
    assert debug_shot_name("тип (house)") == "тип_house"
