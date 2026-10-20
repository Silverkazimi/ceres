from ceres.metrics import land_ability_row
from praetoria.sets import is_land_or_verico
from .vm_context import STATIC, run_in_module_context


def test_lumina_land_membership_and_unlocked_abilities():
    assert is_land_or_verico(1051)
    assert all(is_land_or_verico(n) for n in range(866,870))
    assert not is_land_or_verico(1050)
    assert not is_land_or_verico(1052)
    detail={1051:{'stats':{'land_abilities':[[] for _ in range(9)]+[[['ENERGIZED'],['RATIONING_LITE',-0.1]]]}}}
    for level in range(1,10):
        assert land_ability_row(1051,level,detail)==[]
    assert land_ability_row(1051,10,detail)==[['ENERGIZED'],['RATIONING_LITE',-0.1]]


def test_lumina_uses_existing_land_badge_at_low_and_max_level():
    run_in_module_context(r"""
function assert(ok,message) { if (!ok) throw new Error(message); }
for (const id of [866,867,868,869,1051,'1051']) assert(abilitySetLabel(id)==='Land Card','Land identity');
assert(abilitySetLabel(985)==='VoV','Preserve VoV');
assert(abilitySetLabel(1050)==='Ability','Do not include intervening cards');
for (const abilities of [[],[['ENERGIZED'],['RATIONING_LITE',-0.1]]]) {
 const row={workers:[{asset_ref:'lumina',card_name:'Lumina Lightweaver',land_abilities:abilities}],ability_holders:[{asset_ref:'lumina',name:'Lumina Lightweaver',card_detail_id:1051,level:abilities.length?10:1,codes:abilities.map(a=>a[0])}]};
 const chips=landAbilityCardChips(row);
 assert(chips.length===1 && chips[0].includes('>Land Card</span>'),'Canonical Land Card badge at every level');
 assert(chips[0].includes('Lumina Lightweaver'),'Correct tooltip identity');
}
""",entry=STATIC/'badges.js')
