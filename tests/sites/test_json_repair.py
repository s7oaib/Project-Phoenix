"""The model often drops the last closing brace; the parser must close it."""

import pytest

from phoenix.sites.generator import parse_json


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": [{"b": 1}]', {"a": [{"b": 1}]}),  # outer } missing
        ('{"a": [1, 2', {"a": [1, 2]}),  # ] and } missing
        ('```json\n{"a": {"b": "x"}\n```', {"a": {"b": "x"}}),  # fenced and truncated
        ('{"a": "it\\"s {fine}"}', {"a": 'it"s {fine}'}),  # braces in strings
        ('noise {"a": 1} tail', {"a": 1}),  # prose around valid JSON
        ('{"s": [{"i": [{"t": "x"}}, {"k": 2}]}', {"s": [{"i": [{"t": "x"}]}, {"k": 2}]}),  # }} for ]}
    ],
)
def test_parse_json_repairs_missing_closers(text: str, expected: object) -> None:
    assert parse_json(text) == expected


def test_parse_json_still_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_json("no json here")
