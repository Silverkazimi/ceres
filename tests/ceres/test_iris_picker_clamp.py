"""keep Iris flush to the viewport's right edge and below its top."""

from tests.ceres.vm_context import run_in_module_context


def test_iris_picker_stays_flush_right_and_never_starts_above_viewport() -> None:
    run_in_module_context(r"""
      const cases = [
 { width: 1536, height: 945, triggerTop: 0 },
 { width: 1920, height: 1080, triggerTop: 0 },
 { width: 390, height: 844, triggerTop: -40 },
 { width: 390, height: 844, triggerTop: 820 },
 ];
 for (const { width, height, triggerTop } of cases) {
 const viewport = { left: 0, top: 0, right: width, bottom: height };
 const trigger = { top: triggerTop, height: 48 };
 const picker = { width: 308, height: 48 };
 const rect = anchoredThemePickerRect(trigger, picker, viewport);
 if (rect.left + picker.width !== viewport.right || rect.top < viewport.top) {
 throw new Error(`picker lost its viewport corner at ${width}x${height}: ${JSON.stringify(rect)}`);
 }
 }
    """)
