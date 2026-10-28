"""Hypothesis strategies shared by the property tests: hostile aircraft.json records."""

from __future__ import annotations

from hypothesis import strategies as st

#: Every JSON-representable value, plus the non-finite floats Python's json module accepts.
json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=True, allow_infinity=True)
    | st.text(),
    lambda children: (
        st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=8), children, max_size=3)
    ),
    max_leaves=6,
)

#: Text biased toward the characters that hurt: ESC, NUL, C1 controls, bidi, CJK.
nasty_text = st.text(
    alphabet=st.sampled_from(
        list("\x00\x07\x1b\x7f\x9b\x9d[]0;PWN\r\n\t‮⁦​́") + list("日本語ＡＢＣ") + list("abcXYZ019 -~")
    ),
    max_size=40,
)

FIELDS = [
    "hex",
    "flight",
    "squawk",
    "emergency",
    "category",
    "alt_baro",
    "alt_geom",
    "baro_rate",
    "geom_rate",
    "gs",
    "track",
    "true_heading",
    "mag_heading",
    "lat",
    "lon",
    "seen_pos",
    "seen",
    "type",
    "rssi",
    "messages",
    "r",
    "t",
    "desc",
    "ownOp",
    "year",
    "dbFlags",
    "nav_altitude_mcp",
]

#: A record where every known field holds an arbitrary value (or is absent).
hostile_records = st.fixed_dictionaries(
    {}, optional={name: json_values | nasty_text for name in FIELDS}
)

#: A record with a valid hex and hostile everything else, so it reaches the sinks.
records_with_hex = st.builds(
    lambda hex_, rest: {**rest, "hex": hex_},
    st.from_regex(r"~?[0-9a-fA-F]{6}", fullmatch=True),
    hostile_records,
)
