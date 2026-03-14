# host/clawd_tank_menubar/slider.py
"""Custom NSSlider embedded in an NSMenuItem for the status bar menu."""

import time
import AppKit
import objc

# Debounce interval in seconds
DEBOUNCE_INTERVAL = 0.2


class SliderMenuItem(AppKit.NSObject):
    """Wraps an NSSlider inside an NSMenuItem for use in a rumps menu.

    Subclasses NSObject so it can be the target of the NSSlider action.
    """

    @objc.python_method
    def set_value(self, value: int):
        """Set slider value programmatically (e.g., on config read)."""
        self._slider.setIntegerValue_(value)
        self._value_label.setStringValue_(f"{int(value / 255 * 100)}%")

    @objc.python_method
    def set_enabled(self, enabled: bool):
        """Enable or disable the slider."""
        self._slider.setEnabled_(enabled)
        if not enabled:
            self._value_label.setStringValue_("--")

    def sliderChanged_(self, sender):
        value = int(sender.integerValue())
        self._value_label.setStringValue_(f"{int(value / 255 * 100)}%")

        now = time.monotonic()
        if now - self._last_send_time >= DEBOUNCE_INTERVAL:
            self._last_send_time = now
            if self._on_change:
                self._on_change(value)


def create_slider_menu_item(label: str, min_val: int = 0, max_val: int = 255,
                            initial: int = 102, on_change=None):
    """Factory function to create a SliderMenuItem.

    Module-level function because PyObjC transforms @classmethod on NSObject
    subclasses into ObjC selectors, which breaks Python-style arguments.
    """
    instance = SliderMenuItem.alloc().init()
    instance._on_change = on_change
    instance._last_send_time = 0.0

    # Create container view — initial width is arbitrary; autoresizing
    # stretches it to match the menu's actual width at render time.
    width = 200  # placeholder, will stretch
    height = 68
    view = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, width, height)
    )
    view.setAutoresizingMask_(AppKit.NSViewWidthSizable)

    # Label (NSView y=0 is bottom) — fixed left edge
    label_field = AppKit.NSTextField.labelWithString_(label)
    label_field.setFrame_(AppKit.NSMakeRect(16, 40, 150, 16))
    label_field.setFont_(AppKit.NSFont.systemFontOfSize_(13))
    label_field.setAutoresizingMask_(AppKit.NSViewMaxXMargin)
    view.addSubview_(label_field)

    # Value label — pinned to right edge
    instance._value_label = AppKit.NSTextField.labelWithString_(
        f"{int(initial / 255 * 100)}%"
    )
    instance._value_label.setFrame_(AppKit.NSMakeRect(width - 56, 40, 40, 16))
    instance._value_label.setFont_(AppKit.NSFont.systemFontOfSize_(11))
    instance._value_label.setAlignment_(AppKit.NSTextAlignmentRight)
    instance._value_label.setAutoresizingMask_(AppKit.NSViewMinXMargin)
    view.addSubview_(instance._value_label)

    # Slider — stretches with the view, 26px height for thumb
    instance._slider = AppKit.NSSlider.alloc().initWithFrame_(
        AppKit.NSMakeRect(16, 8, width - 32, 26)
    )
    instance._slider.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    instance._slider.setMinValue_(min_val)
    instance._slider.setMaxValue_(max_val)
    instance._slider.setIntegerValue_(initial)
    instance._slider.setContinuous_(True)
    instance._slider.setTarget_(instance)
    instance._slider.setAction_(objc.selector(
        instance.sliderChanged_, signature=b'v@:@'
    ))
    view.addSubview_(instance._slider)

    # Store view for external use (caller sets it on a rumps MenuItem)
    instance.view = view

    return instance
