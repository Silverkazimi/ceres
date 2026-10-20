import json
import subprocess
from pathlib import Path

import pytest
from ceres.candidates import _projected_grain_food


def detail(code, rate):
    return {"stats": {"land_abilities": [[[code, rate]]]}}


def test_food_uses_strongest_rationing_and_includes_runi():
    cards = [{"card_detail_id": 1, "level": 1}, {"card_detail_id": 2, "level": 1}]
    assert _projected_grain_food(cards, [{"land_base_pp": 1500}], {"a": 67000},
                                 {1: detail("RATIONING", -0.3), 2: detail("RATIONING", -0.1)}) == pytest.approx(479.5)


@pytest.mark.parametrize("base, expected", [(20000, 160), (20001, 200.01)])
def test_light_rationing_threshold(base, expected):
    assert _projected_grain_food([{"card_detail_id": 1, "level": 1}], [], {"a": base},
                                 {1: detail("RATIONING_LITE", -0.2)}) == pytest.approx(expected)


def test_unknown_ability_level_does_not_claim_savings():
    assert _projected_grain_food([{"card_detail_id": 1, "level": None}], [], {"a": 1000},
                                 {1: detail("RATIONING", -0.3)}) is None


def test_compact_verdict_only_for_verified_net_win_despite_gross_loss():
    source = (Path(__file__).resolve().parents[2] / "ceres/static/editor.js").read_text()
    function = source[source.index("function renderChangeBadges("):source.index("function editorBasePpColumn(")]
    script = "const stagedProduction=()=>null; const num=String, signedPp=String, esc=String, fmtRate=x=>Number(x.toFixed(2)); const tooltipIconPath=()=>''; const tipAttrs=x=>' data-tip='+JSON.stringify(x.body);\n" + function + """
const render = economics => renderChangeBadges(null, 0, 1, null, 0, {}, null, economics);
const html=render({gross_delta:-42,food_saved:335.5,net_delta_before_tax:293.5});
if (!html.includes('>Net gain</span>') || !html.includes('293.5') || !html.includes('before tax')) throw Error(html);
for (const value of [null,{gross_delta:-42,food_saved:20,net_delta_before_tax:-22},{gross_delta:42,food_saved:20,net_delta_before_tax:62}]) {
 if (render(value).includes('Net gain')) throw Error('misleading verdict');
}
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
