from .vm_context import run_in_module_context


def test_worker_tooltip_prefers_sides_and_falls_back_when_neither_fits() -> None:
    run_in_module_context(
        """
        const viewport = { left: 0, top: 0, width: 1536, height: 864 };
 const tooltip = { width: 320, height: 280 };
 const borders = { left: 2, right: 2, top: 2, bottom: 2 };
 const right = calculateTooltipPosition({ left: 400, top: 300, width: 100, height: 80 }, tooltip, viewport, borders, 12, 10, { side: true });
 if (right.side !== 'right' || right.left !== 508 || Math.abs(right.top - 200) > 0.001) throw new Error(JSON.stringify(right));
 if (right.arrowTop !== 138) throw new Error(JSON.stringify(right));
 const left = calculateTooltipPosition({ left: 1250, top: 300, width: 100, height: 80 }, tooltip, viewport, borders, 12, 10, { side: true });
 if (left.side !== 'left' || left.left !== 922 || Math.abs(left.top - 200) > 0.001) throw new Error(JSON.stringify(left));
 const fallback = calculateTooltipPosition({ left: 650, top: 300, width: 100, height: 80 }, { width: 800, height: 280 }, viewport, borders, 12, 10, { side: true });
 if (fallback.side !== null || !fallback.below || fallback.left !== 300 || fallback.top !== 390) throw new Error(JSON.stringify(fallback));
 const phone = calculateTooltipPosition({ left: 40, top: 400, width: 100, height: 80 }, tooltip, { left: 0, top: 0, width: 412, height: 915 }, borders, 12, 10, { side: true });
 if (phone.side !== null || phone.top !== 110) throw new Error(JSON.stringify(phone));
        """,
    )


def test_worker_side_placement_is_scoped_to_worker_layout() -> None:
    run_in_module_context(
        """
        const workerAttrs = tipAttrs({ title: 'Worker', layout: 'worker' });
 if (!workerAttrs.includes('data-tip-layout="worker"')) throw new Error(workerAttrs);

 const classes = new Set();
 TOOLTIP_EL = {
 offsetWidth: 320,
 offsetHeight: 280,
 style: { left: '', top: '', setProperty(name, value) { this[name] = value; } },
 classList: { toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); } },
 };
 document.documentElement.clientWidth = 1536;
 window.innerHeight = 864;
 const target = {
 getAttribute(name) { return name === 'data-tip-layout' ? 'worker' : null; },
 getBoundingClientRect() { return { left: 400, top: 300, width: 100, height: 80, right: 500, bottom: 380 }; },
 };
 positionTooltip(target);
 if (!classes.has('is-side-right') || classes.has('is-side-left')) throw new Error([...classes]);

 const ordinaryTarget = { ...target, getAttribute() { return null; } };
 positionTooltip(ordinaryTarget);
 if (classes.has('is-side-right') || classes.has('is-side-left')) throw new Error([...classes]);
        """,
    )


def test_position_tooltip_writes_viewport_coordinates_unscaled() -> None:
    # there is no page zoom, so the tooltip is placed at the viewport
    # coordinates it computed -- a stale `window.ceresScale.factor` (the retired
    # zoom scaler) must be ignored, never divided out.
    run_in_module_context(
        """
        window.ceresScale = { factor: 2 };
 TOOLTIP_EL = {
 offsetWidth: 100,
 offsetHeight: 50,
 style: { left: '', top: '', setProperty(name, value) { this[name] = value; } },
 classList: { toggle() {} },
 };
 document.documentElement.clientWidth = 1920;
 const target = { getAttribute() { return null; }, getBoundingClientRect() {
 return { left: 400, top: 400, width: 40, height: 20, bottom: 420 };
 } };
 positionTooltip(target);
 if (TOOLTIP_EL.style.left !== '370px') throw new Error(TOOLTIP_EL.style.left);
 if (TOOLTIP_EL.style.top !== '340px') throw new Error(TOOLTIP_EL.style.top);
 if (TOOLTIP_EL.style['--ceres-tooltip-arrow-left'] !== '50px') throw new Error(TOOLTIP_EL.style['--ceres-tooltip-arrow-left']);
        """,
    )
