"""  readiness projection and timer stay local to observed models."""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / 'ceres' / 'static'


def test_collects_all_r2_sources_and_clears_after_the_items_disappear():
    run_in_module_context(r"""
const at=Date.parse('2026-10-05T12:00:00Z');
const stage=[{deed_uid:'d1',group_id:'worker',slot_index:0,asset_ref:'x',_asset_name:'Worker',action:'stake',cooldown:{ready:true,ready_at:'2026-10-05T11:00:00Z'}}];
const constructions=[{deedUid:'d2',kind:'feed',toLabel:'Grain Farm',readyAt:null}];
const plans=[{id:'p1',state:'ready',card:{name:'Lord Arianthus'}}, {id:'p2',state:'cooling',ready_at:'2026-10-06T12:00:00Z',card:{name:'Worker'}}];
const result=collectReadyItems({stage,constructions,plans,plotLabels:new Map([['d1','Plot A'],['d2','Plot B']]),now:at});
if(result.items.length!==3) throw Error('must collect stake, feed and plan');
if(!result.items[0].label.includes('Worker on Plot A, worker slot 1')) throw Error('stake label');
if(!result.items[1].label.includes('Feed the workers · Grain Farm on Plot B is finished')) throw Error('feed label');
if(!result.items[2].label.includes('Ready to combine · Lord Arianthus')) throw Error('plan label');
if(result.future.length!==1||result.future[0].at!==Date.parse('2026-10-06T12:00:00Z')) throw Error('earliest future candidate');
stage[0].txId='signed';
const cleared=collectReadyItems({stage,constructions:[],plans:[],now:at});
if(cleared.items.length!==0) throw Error('ready mode must clear when ready items are gone');
""", entry=STATIC / 'ready-feed.js')


def test_exactly_one_timeout_is_replaced_and_no_banner_or_seen_store():
    source = (STATIC / 'ready-feed.js').read_text()
    assert source.count('setTimeout(') == 1
    assert source.count('clearTimeout(') == 2  # one replacement and explicit test reset
    checkout = (STATIC / 'checkout.js').read_text()
    assert "armReadyTimer(ready.future" in checkout
    assert 'checkout-ready-status' in checkout
    assert "setAttribute('data-tip-foot', 'Open Changes to sign them.')" in checkout
    assert 'seen' not in source.lower()
    assert 'localStorage' not in source and 'sessionStorage' not in source
    assert 'banner' not in source.lower()


def test_timer_replaces_previous_deadline_and_marks_only_local_cooldown():
    run_in_module_context(r"""
const entry={cooldown:{ready:false,ready_at:'2026-10-05T12:01:00Z'}};
let redrawn=0;
const first=[{at:Date.parse('2026-10-05T12:01:00Z'),kind:'stake',entry}];
armReadyTimer(first,()=>redrawn++,Date.parse('2026-10-05T12:00:00Z'));
if(__timers.size!==1||[...__timers.values()][0].delay!==60000) throw Error('one earliest timer');
const second=[{at:Date.now()+60000,kind:'stake',entry}];
armReadyTimer(second,()=>redrawn++,Date.now());
if(__timers.size!==1||![...__timers.keys()].includes(2)||![...__cleared].includes(1)) throw Error('render did not replace timeout');
const timer=[...__timers.values()][0]; second[0].at=Date.now()-1; __timers.clear(); timer.fn();
if(!entry.cooldown.ready||entry.cooldown.state!=='ready'||entry.cooldown.reason!=='cooldown ended; the game checks it when you sign'||redrawn!==1) throw Error('expiry must mark local readiness and redraw');
if(__timers.size!==0) throw Error('expired timer remains active');
""", entry=STATIC / 'ready-feed.js', extra_context_js=r"""
context.__timers = new Map(); context.__cleared = new Set(); context.__timerId = 0;
context.setTimeout = (fn, delay) => { const id=++context.__timerId; context.__timers.set(id,{fn,delay}); return id; };
context.clearTimeout = (id) => { context.__cleared.add(id); context.__timers.delete(id); };
""")
