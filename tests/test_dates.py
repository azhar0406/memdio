"""Tests for _extract_event_date — absolute + relative date parsing."""

import pytest

from memdio.core.storage import _extract_event_date

# (content, document_date anchor, expected ISO date)
ABSOLUTE_CASES = [
    ("Meeting on Mar 15, 2025", None, "2025-03-15"),
    ("March 15, 2025 meeting", None, "2025-03-15"),
    ("Event 2023-05-30 happened", None, "2023-05-30"),
    ("logged 2023/05/30 today", None, "2023-05-30"),
    ("on 3/15/2025 we met", None, "2025-03-15"),
]

RELATIVE_CASES = [
    ("It was yesterday", "2023/05/30 (Tue) 19:30", "2023-05-29"),
    ("done 2 weeks ago", "2023-05-30", "2023-05-16"),
    ("shipped last week", "2023-05-30", "2023-05-23"),
    ("happened last month", "2023-01-15", "2022-12-15"),  # month underflow -> prev year
    ("three months ago", "2023-05-30", "2023-02-28"),      # word-number + day clamp
    ("last year event", "2023-06-15", "2022-06-15"),
    ("5 days ago", "2023-05-30", "2023-05-25"),
]

NONE_CASES = [
    ("no date here", "2023-05-30"),        # no date tokens
    ("yesterday but no anchor", None),     # relative needs an anchor
    ("2 weeks ago", None),                 # relative needs an anchor
    ("just some text", None),
]


@pytest.mark.parametrize("content,anchor,expected", ABSOLUTE_CASES)
def test_absolute_dates(content, anchor, expected):
    assert _extract_event_date(content, anchor) == expected


@pytest.mark.parametrize("content,anchor,expected", RELATIVE_CASES)
def test_relative_dates(content, anchor, expected):
    assert _extract_event_date(content, anchor) == expected


@pytest.mark.parametrize("content,anchor", NONE_CASES)
def test_unparseable_returns_none(content, anchor):
    assert _extract_event_date(content, anchor) is None


def test_invalid_absolute_date_falls_through_to_none():
    # A syntactically-matching but invalid calendar date should not raise.
    assert _extract_event_date("meeting 2023-13-45 scheduled", None) is None
