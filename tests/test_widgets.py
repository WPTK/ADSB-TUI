"""Tests for adsbtui.ui.widgets: the curses-optional widget layer.

Every test here exercises handle_key()/render_lines()/value/items only -- never draw() --
so none of it requires a real curses window.
"""

from __future__ import annotations

import curses

from adsbtui.ui.widgets import (
    ListPicker,
    MessageBox,
    NumberField,
    ProgressBar,
    Select,
    Tabs,
    TextField,
    Toggle,
    center_rect,
)


class TestTextField:
    def test_insertion_at_cursor(self):
        field = TextField()
        for ch in "abc":
            assert field.handle_key(ord(ch)) is True
        assert field.value == "abc"
        assert field.cursor == 3

    def test_render_lines_contains_typed_text(self):
        field = TextField()
        for ch in "hello":
            field.handle_key(ord(ch))
        lines = field.render_lines(20)
        assert len(lines) == 1
        assert "hello" in lines[0]

    def test_backspace_removes_char_before_cursor(self):
        field = TextField("abc")
        assert field.cursor == 3
        assert field.handle_key(curses.KEY_BACKSPACE) is True
        assert field.value == "ab"
        assert field.cursor == 2

    def test_backspace_alternate_byte_values(self):
        # Some terminals send raw 127 (DEL) or 8 (^H) instead of KEY_BACKSPACE.
        for raw in (127, 8):
            field = TextField("xy")
            assert field.handle_key(raw) is True
            assert field.value == "x"

    def test_backspace_at_start_is_noop(self):
        field = TextField("abc")
        field.handle_key(curses.KEY_HOME)
        assert field.handle_key(curses.KEY_BACKSPACE) is False
        assert field.value == "abc"

    def test_delete_removes_char_after_cursor(self):
        field = TextField("abc")
        field.handle_key(curses.KEY_HOME)
        assert field.handle_key(curses.KEY_DC) is True
        assert field.value == "bc"
        assert field.cursor == 0

    def test_delete_at_end_is_noop(self):
        field = TextField("abc")
        assert field.cursor == 3
        assert field.handle_key(curses.KEY_DC) is False
        assert field.value == "abc"

    def test_left_right_cursor_movement(self):
        field = TextField("abc")
        assert field.cursor == 3
        assert field.handle_key(curses.KEY_LEFT) is True
        assert field.cursor == 2
        assert field.handle_key(curses.KEY_LEFT) is True
        assert field.cursor == 1
        assert field.handle_key(curses.KEY_RIGHT) is True
        assert field.cursor == 2

    def test_left_at_start_is_noop(self):
        field = TextField("ab")
        field.handle_key(curses.KEY_HOME)
        assert field.handle_key(curses.KEY_LEFT) is False

    def test_right_at_end_is_noop(self):
        field = TextField("ab")
        assert field.handle_key(curses.KEY_RIGHT) is False

    def test_home_and_end(self):
        field = TextField("abcdef")
        assert field.handle_key(curses.KEY_HOME) is True
        assert field.cursor == 0
        assert field.handle_key(curses.KEY_HOME) is False
        assert field.handle_key(curses.KEY_END) is True
        assert field.cursor == 6
        assert field.handle_key(curses.KEY_END) is False

    def test_insert_in_middle_of_text(self):
        field = TextField("ac")
        field.handle_key(curses.KEY_LEFT)
        field.handle_key(ord("b"))
        assert field.value == "abc"
        assert field.cursor == 2

    def test_non_printable_unhandled_key_not_consumed(self):
        field = TextField("abc")
        assert field.handle_key(curses.KEY_F1) is False
        assert field.value == "abc"

    def test_validator_rejects_edit(self):
        field = TextField("", validator=lambda s: "x" not in s)
        assert field.handle_key(ord("x")) is False
        assert field.value == ""
        assert field.handle_key(ord("y")) is True
        assert field.value == "y"

    def test_max_length_rejects_edit(self):
        field = TextField("ab", max_length=2)
        assert field.handle_key(ord("c")) is False
        assert field.value == "ab"

    def test_value_setter_clamps_cursor(self):
        field = TextField("abcdef")
        field.value = "ab"
        assert field.value == "ab"
        assert field.cursor == 2

    def test_render_lines_clips_to_width(self):
        field = TextField("abcdefghij")
        lines = field.render_lines(4)
        assert lines == ["abcd"]


class TestNumberField:
    def test_accepts_digits(self):
        field = NumberField()
        for ch in "123":
            assert field.handle_key(ord(ch)) is True
        assert field.value == 123.0

    def test_rejects_alpha_characters(self):
        field = NumberField()
        assert field.handle_key(ord("a")) is False
        assert field.text == ""

    def test_accepts_single_decimal_point(self):
        field = NumberField()
        for ch in "1.5":
            assert field.handle_key(ord(ch)) is True
        assert field.value == 1.5

    def test_rejects_second_decimal_point(self):
        field = NumberField("1.5")
        assert field.handle_key(ord(".")) is False
        assert field.text == "1.5"

    def test_accepts_leading_minus(self):
        field = NumberField()
        for ch in "-42":
            assert field.handle_key(ord(ch)) is True
        assert field.value == -42.0

    def test_rejects_minus_not_at_start(self):
        field = NumberField("4")
        assert field.handle_key(ord("-")) is False
        assert field.text == "4"

    def test_rejects_second_minus(self):
        field = NumberField("-4")
        field.handle_key(curses.KEY_HOME)
        assert field.handle_key(ord("-")) is False
        assert field.text == "-4"

    def test_value_is_none_when_unparseable(self):
        field = NumberField()
        assert field.value is None
        field.handle_key(ord("-"))
        assert field.text == "-"
        assert field.value is None

    def test_backspace_still_works(self):
        field = NumberField("12")
        assert field.handle_key(curses.KEY_BACKSPACE) is True
        assert field.value == 1.0


class TestToggle:
    def test_space_flips(self):
        toggle = Toggle(False)
        assert toggle.handle_key(ord(" ")) is True
        assert toggle.value is True
        assert toggle.handle_key(ord(" ")) is True
        assert toggle.value is False

    def test_enter_flips(self):
        toggle = Toggle(True)
        assert toggle.handle_key(curses.KEY_ENTER) is True
        assert toggle.value is False

    def test_other_key_not_consumed(self):
        toggle = Toggle(False)
        assert toggle.handle_key(ord("x")) is False
        assert toggle.value is False

    def test_render_lines_reflects_state(self):
        toggle = Toggle(False, label="Enabled")
        assert toggle.render_lines(30) == ["[ ] Enabled"]
        toggle.handle_key(ord(" "))
        assert toggle.render_lines(30) == ["[x] Enabled"]


class TestSelect:
    def test_cycles_right_with_wraparound(self):
        select = Select([("a", "A"), ("b", "B"), ("c", "C")])
        assert select.value == "a"
        assert select.handle_key(curses.KEY_RIGHT) is True
        assert select.value == "b"
        assert select.handle_key(curses.KEY_RIGHT) is True
        assert select.value == "c"
        assert select.handle_key(curses.KEY_RIGHT) is True
        assert select.value == "a"  # wrapped around

    def test_cycles_left_with_wraparound(self):
        select = Select([("a", "A"), ("b", "B"), ("c", "C")])
        assert select.handle_key(curses.KEY_LEFT) is True
        assert select.value == "c"  # wrapped backwards
        assert select.handle_key(curses.KEY_LEFT) is True
        assert select.value == "b"

    def test_space_also_cycles_forward(self):
        select = Select([("a", "A"), ("b", "B")])
        assert select.handle_key(ord(" ")) is True
        assert select.value == "b"

    def test_render_lines_shows_label(self):
        select = Select([("a", "Alpha")])
        assert select.render_lines(30) == ["< Alpha >"]

    def test_unhandled_key(self):
        select = Select([("a", "A"), ("b", "B")])
        assert select.handle_key(ord("z")) is False


class TestListPicker:
    def test_cursor_moves_down_and_up(self):
        picker = ListPicker(["a", "b", "c"])
        assert picker.cursor == 0
        assert picker.handle_key(curses.KEY_DOWN) is True
        assert picker.cursor == 1
        assert picker.handle_key(ord("j")) is True
        assert picker.cursor == 2
        assert picker.handle_key(curses.KEY_DOWN) is False  # already at the end
        assert picker.handle_key(curses.KEY_UP) is True
        assert picker.cursor == 1
        assert picker.handle_key(ord("k")) is True
        assert picker.cursor == 0

    def test_home_and_end(self):
        picker = ListPicker(["a", "b", "c", "d"])
        assert picker.handle_key(curses.KEY_END) is True
        assert picker.cursor == 3
        assert picker.handle_key(curses.KEY_HOME) is True
        assert picker.cursor == 0

    def test_page_down_and_up(self):
        picker = ListPicker(list(range(20)), visible_rows=5)
        assert picker.handle_key(curses.KEY_NPAGE) is True
        assert picker.cursor == 5
        assert picker.handle_key(curses.KEY_PPAGE) is True
        assert picker.cursor == 0

    def test_multi_select_toggle(self):
        picker = ListPicker(["a", "b", "c"], multi_select=True)
        assert picker.handle_key(ord(" ")) is True
        assert picker.is_selected(0) is True
        assert picker.selected_items == ["a"]
        picker.handle_key(curses.KEY_DOWN)
        picker.handle_key(ord(" "))
        assert picker.selected_items == ["a", "b"]
        # toggling again deselects
        picker.handle_key(ord(" "))
        assert picker.selected_items == ["a"]

    def test_single_select_enter_confirms(self):
        picker = ListPicker(["a", "b", "c"])
        picker.handle_key(curses.KEY_DOWN)
        assert picker.confirmed_index is None
        assert picker.handle_key(curses.KEY_ENTER) is True
        assert picker.confirmed_index == 1

    def test_single_select_space_is_noop(self):
        picker = ListPicker(["a", "b"])
        assert picker.handle_key(ord(" ")) is False

    def test_multi_select_enter_is_noop(self):
        picker = ListPicker(["a", "b"], multi_select=True)
        assert picker.handle_key(curses.KEY_ENTER) is False

    def test_reorder_move_up_and_down(self):
        picker = ListPicker(["a", "b", "c"], multi_select=True)
        picker.handle_key(curses.KEY_DOWN)  # cursor -> index 1 ("b")
        assert picker.move_up() is True
        assert picker.items == ["b", "a", "c"]
        assert picker.cursor == 0
        assert picker.move_down() is True
        assert picker.items == ["a", "b", "c"]
        assert picker.cursor == 1

    def test_move_up_at_top_is_noop(self):
        picker = ListPicker(["a", "b"], multi_select=True)
        assert picker.move_up() is False
        assert picker.items == ["a", "b"]

    def test_move_down_at_bottom_is_noop(self):
        picker = ListPicker(["a", "b"], multi_select=True)
        picker.handle_key(curses.KEY_END)
        assert picker.move_down() is False
        assert picker.items == ["a", "b"]

    def test_reorder_unavailable_in_single_select(self):
        picker = ListPicker(["a", "b"], multi_select=False)
        picker.handle_key(curses.KEY_DOWN)
        assert picker.move_up() is False
        assert picker.items == ["a", "b"]

    def test_reorder_carries_selection_with_item(self):
        picker = ListPicker(["a", "b", "c"], multi_select=True)
        picker.handle_key(ord(" "))  # select "a" at index 0
        picker.handle_key(curses.KEY_DOWN)  # cursor -> index 1 ("b")
        picker.move_up()  # "b" moves above "a": order becomes [b, a, c]
        assert picker.items == ["b", "a", "c"]
        # "a" (now at index 1) should still be the selected one, not "b"
        assert picker.selected_items == ["a"]

    def test_render_lines_shows_cursor_and_selection_marks(self):
        picker = ListPicker(["a", "b"], multi_select=True)
        picker.handle_key(ord(" "))
        lines = picker.render_lines(20)
        assert lines[0].startswith(">[x]")
        assert lines[1].startswith(" [ ]")

    def test_empty_list_render(self):
        picker = ListPicker([])
        assert picker.render_lines(20) == ["(empty)"]
        assert picker.handle_key(curses.KEY_DOWN) is False


class TestProgressBar:
    def test_zero_percent(self):
        bar = ProgressBar(0.0)
        lines = bar.render_lines(14)
        assert len(lines) == 1
        assert "0%" in lines[0]
        assert "#" not in lines[0]

    def test_fifty_percent(self):
        bar = ProgressBar(0.5)
        lines = bar.render_lines(14)
        assert "50%" in lines[0]
        assert "#" in lines[0]
        assert "-" in lines[0]

    def test_hundred_percent(self):
        bar = ProgressBar(1.0)
        lines = bar.render_lines(14)
        assert "100%" in lines[0]
        assert "-" not in lines[0].split("]")[0]  # bar interior fully filled

    def test_value_clamped(self):
        bar = ProgressBar(1.5)
        assert bar.value == 1.0
        bar.value = -0.5
        assert bar.value == 0.0

    def test_render_never_exceeds_width(self):
        for width in range(0, 40):
            bar = ProgressBar(0.33)
            assert len(bar.render_lines(width)[0]) <= width

    def test_fill_increases_monotonically(self):
        prev_fill = -1
        for value in (0.0, 0.25, 0.5, 0.75, 1.0):
            bar = ProgressBar(value)
            line = bar.render_lines(30)[0]
            fill = line.count("#")
            assert fill >= prev_fill
            prev_fill = fill


class TestMessageBox:
    def test_default_focus_is_first_button(self):
        box = MessageBox("Are you sure?", ["OK", "Cancel"])
        assert box.focus == 0

    def test_right_and_tab_move_focus_forward_with_wrap(self):
        box = MessageBox("msg", ["OK", "Cancel"])
        assert box.handle_key(curses.KEY_RIGHT) is True
        assert box.focus == 1
        assert box.handle_key(ord("\t")) is True
        assert box.focus == 0  # wrapped

    def test_left_moves_focus_backward_with_wrap(self):
        box = MessageBox("msg", ["OK", "Cancel", "Help"])
        assert box.handle_key(curses.KEY_LEFT) is True
        assert box.focus == 2  # wrapped backwards

    def test_enter_presses_focused_button(self):
        box = MessageBox("msg", ["OK", "Cancel"])
        box.handle_key(curses.KEY_RIGHT)
        assert box.pressed_index is None
        assert box.handle_key(curses.KEY_ENTER) is True
        assert box.pressed_index == 1
        assert box.pressed_label == "Cancel"

    def test_render_lines_includes_message_and_buttons(self):
        box = MessageBox("Delete file?", ["OK", "Cancel"])
        lines = box.render_lines(40)
        assert "Delete file?" in lines[0]
        assert "OK" in lines[-1]
        assert "Cancel" in lines[-1]


class TestTabs:
    def test_right_cycles_forward_with_wrap(self):
        tabs = Tabs(["Overview", "Filters", "Columns"])
        assert tabs.active_index == 0
        assert tabs.handle_key(curses.KEY_RIGHT) is True
        assert tabs.active_index == 1
        assert tabs.handle_key(ord("\t")) is True
        assert tabs.active_index == 2
        assert tabs.handle_key(curses.KEY_RIGHT) is True
        assert tabs.active_index == 0  # wrapped

    def test_left_cycles_backward_with_wrap(self):
        tabs = Tabs(["A", "B", "C"])
        assert tabs.handle_key(curses.KEY_LEFT) is True
        assert tabs.active_index == 2  # wrapped backwards
        assert tabs.active_label == "C"

    def test_render_lines_marks_active_tab(self):
        tabs = Tabs(["A", "B"])
        lines = tabs.render_lines(40)
        assert "[A]" in lines[0]
        assert "[B]" not in lines[0]
        tabs.handle_key(curses.KEY_RIGHT)
        lines = tabs.render_lines(40)
        assert "[B]" in lines[0]
        assert "[A]" not in lines[0]


class TestForm:
    def _make_form(self):
        from adsbtui.ui.widgets import Form

        name_field = TextField("")
        radius_field = NumberField("")
        return Form([("Name", name_field), ("Radius", radius_field)]), name_field, radius_field

    def test_typing_goes_to_focused_field(self):
        form, name_field, radius_field = self._make_form()
        assert form.focus_index == 0
        for ch in "bob":
            assert form.handle_key(ord(ch)) is True
        assert name_field.value == "bob"
        assert radius_field.value is None

    def test_tab_moves_focus_and_typing_follows(self):
        form, name_field, radius_field = self._make_form()
        for ch in "bob":
            form.handle_key(ord(ch))
        assert form.handle_key(ord("\t")) is True
        assert form.focus_index == 1
        for ch in "42":
            assert form.handle_key(ord(ch)) is True
        assert name_field.value == "bob"  # unchanged
        assert radius_field.value == 42.0

    def test_tab_wraps_around(self):
        form, _, _ = self._make_form()
        form.handle_key(ord("\t"))
        assert form.focus_index == 1
        form.handle_key(ord("\t"))
        assert form.focus_index == 0  # wrapped

    def test_shift_tab_moves_focus_backward(self):
        form, _, _ = self._make_form()
        assert form.handle_key(curses.KEY_BTAB) is True
        assert form.focus_index == 1  # wrapped backwards

    def test_up_down_also_move_focus(self):
        form, _, _ = self._make_form()
        assert form.handle_key(curses.KEY_DOWN) is True
        assert form.focus_index == 1
        assert form.handle_key(curses.KEY_UP) is True
        assert form.focus_index == 0

    def test_render_lines_includes_labels_and_values(self):
        form, name_field, _ = self._make_form()
        for ch in "bob":
            form.handle_key(ord(ch))
        lines = form.render_lines(40)
        assert any("Name" in line and "bob" in line for line in lines)
        assert any("Radius" in line for line in lines)

    def test_focused_widget_property(self):
        form, name_field, radius_field = self._make_form()
        assert form.focused_widget is name_field
        form.focus_next()
        assert form.focused_widget is radius_field


class TestCenterRect:
    def test_centers_box_on_screen(self):
        y, x = center_rect(screen_w=80, screen_h=24, box_w=40, box_h=10)
        assert y == 7
        assert x == 20

    def test_clamped_when_box_larger_than_screen(self):
        y, x = center_rect(screen_w=10, screen_h=10, box_w=40, box_h=40)
        assert y == 0
        assert x == 0
