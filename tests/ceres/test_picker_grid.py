"""Focused source and VM guards for 's grid-only picker refinement."""

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from ceres.candidates import rank_candidates
from .vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
CANDIDATES = (ROOT / "ceres/static/candidates.js").read_text(encoding="utf-8")
ACTIONS = (ROOT / "ceres/static/picker-actions.js").read_text(encoding="utf-8")
CSS = (ROOT / "ceres/static/ceres.css").read_text(encoding="utf-8")
ICONS = (ROOT / "ceres/static/icons.js").read_text(encoding="utf-8")
PREFERENCE_SOURCE = (
    "let BOARD = null;\nlet CURRENT_PLAYER = null;\n"
    + ACTIONS[
        ACTIONS.index("const PICKER_VIEW_STORAGE_PREFIX"):
        ACTIONS.index("import { scrollPlotAfterSlotAdd")
    ].replace("export function ", "function ")
)
CHOOSE_START = ACTIONS.index("export async function handleSlotEditorClick")
CHOOSE_END = ACTIONS.index("export function handlePickerFilter", CHOOSE_START)
CHOOSE_VM_SOURCE = ACTIONS[CHOOSE_START:CHOOSE_END].replace(
    "export async function handleSlotEditorClick", "async function handleSlotEditorClick"
)


def test_picker_view_toggle_and_list_branch_are_explicit() -> None:
    assert 'data-picker-action="toggle-view"' in CANDIDATES
    # (2026-09-16, commit dda4d0b7): the redundant aria-label/title pair
    # was replaced by the shared `tip()` tooltip helper; the accessible name now comes
    # from the still-present `sr-only` span alone.
    assert '<span class="sr-only">Card grid view</span>' in CANDIDATES
    assert 'data-picker-action="select"' in CANDIDATES
    assert 'data-picker-action="choose"' in CANDIDATES
    assert "picker.viewMode === 'grid'" in CANDIDATES
    assert " : renderCandidateRow(candidate" in CANDIDATES
    assert "savePickerViewPreference(next)" in ACTIONS
    assert 'data-picker-view="list"' not in CANDIDATES
    assert " : ' class=\"candidate-list\"';" in CANDIDATES


def test_picker_view_preference_defaults_to_list_and_survives_storage() -> None:
    run_in_module_context(
        """
        BOARD = { player: 's7640' };
        if (readPickerViewPreference() !== 'list') process.exit(10);
        localStorage.setItem('ceres:picker-view:v1:s7640', 'grid');
        if (readPickerViewPreference() !== 'grid') process.exit(11);
        localStorage.setItem('ceres:picker-view:v1:s7640', 'bad');
        if (readPickerViewPreference() !== 'list') process.exit(12);
        localStorage.getItem = () => { throw new Error('storage unavailable'); };
        if (readPickerViewPreference() !== 'list') process.exit(13);
        localStorage.setItem = () => { throw new Error('storage unavailable'); };
        savePickerViewPreference('grid');
        """,
        entry=ROOT / "ceres/static/picker-actions.js",
        source=PREFERENCE_SOURCE,
    )


def test_card_grid_default_fills_available_width_without_column_cap() -> None:
    # as many columns as fit the
    # card's content minimum -- derived from the grid's own width, never from a
    # tier breakpoint or a raw width media query.
    assert ".candidate-list.is-card-grid" in CSS
    assert "grid-template-columns: repeat(auto-fit, minmax(min(100%, var(--candidate-grid-card-min)), 1fr))" in CSS
    # (maintainer clarification 2026-09-16, commit 4c633f5d: "remove arbitrary layout
    # ceilings while preserving container boundaries") dropped the cqi-scaled clamp for a
    # flat minimum, so auto-fit alone decides the column count.
    assert "--candidate-grid-card-min: 13.5rem;" in CSS
    assert "html.ceres-lw-le-1919.ceres-lw-le-1659 .candidate-list.is-card-grid" not in CSS
    assert "html.ceres-lw-le-1919.ceres-lw-le-1439 .candidate-list.is-card-grid" not in CSS
    assert "1fr)); align-items: start; gap: var(--candidate-grid-gap)" in CSS
    assert "@media (max-width: 1200px) { .candidate-list.is-card-grid" not in CSS
    # The grid's OWN column rule, and no other rule (tier class or @media) that
    # overrides its column count.
    grid_rule = CSS.split(".candidate-list.is-card-grid {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: repeat(auto-fit, minmax(min(100%, var(--candidate-grid-card-min)" in grid_rule
    assert "max-inline-size: 100%" in grid_rule
    assert "1920px" not in grid_rule
    column_rules = [
        selector for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S))
        if "grid-template-columns" in declarations
        and any(re.fullmatch(r"(?:html(?:\.[\w-]+)*\s+)?\.candidate-list\.is-card-grid", part.strip())
                for part in selector.split(","))
    ]
    assert len(column_rules) == 1, column_rules


def test_grid_loading_reserves_one_responsive_row_with_official_spinner() -> None:
    assert "const PICKER_GRID_PLACEHOLDER_COUNT = 7;" in CANDIDATES
    assert "ghost.repeat(PICKER_GRID_PLACEHOLDER_COUNT)" in CANDIDATES
    assert 'class="candidate-card candidate-grid-card is-ghost"' in CANDIDATES
    assert 'class="candidate-grid-select candidate-grid-stage"' in CANDIDATES
    assert 'class="candidate-grid-face"' in CANDIDATES
    assert 'class="candidate-grid-metrics"' in CANDIDATES
    assert 'const ghostIdentity = \'<span class="candidate-grid-identity cand-ghost-body"><strong class="candidate-name">&nbsp;</strong>' in CANDIDATES
    assert 'const ghostMetrics = \'<span class="candidate-grid-metrics"><span class="candidate-grid-metric"><strong>&nbsp;</strong><small>&nbsp;</small></span>' in CANDIDATES
    assert 'class="candidate-art"><span class="spinner"></span>' in CANDIDATES
    assert "background: url(/static/assets/ui/loading-spinner.gif) center / contain no-repeat" in CSS
    assert 'candidate-grid-card[data-picker-group="worker"]' not in CSS
    assert ".candidate-grid-card.is-ghost {" not in CSS
    assert ".candidate-list.is-card-grid > .candidate-grid-card.is-ghost .cand-ghost-body" not in CSS
    assert ".candidate-list.is-card-grid > .candidate-grid-card.is-ghost .candidate-grid-metric" not in CSS
    assert "\n.candidate-card.is-ghost .cand-ghost-body { min-block-size" not in CSS
    assert ".candidate-list:not(.is-card-grid) .candidate-card.is-ghost .cand-ghost-body { min-block-size: 8.4rem; }" in CSS
    # the phase C block became the fluid open-plot block plus the panel rules.
    for start, end in (("/* FLUID OPEN PLOT START", "/* FLUID OPEN PLOT END */"),
                       ("/* PHASE C LOW-RES PANELS START", "/* PHASE C LOW-RES PANELS END */")):
        low_res = CSS.split(start, 1)[1].split(end, 1)[0]
        assert ".candidate-grid-card.is-ghost" not in low_res
    assert "function trimPickerGridLoadingRow" in ACTIONS
    assert "getComputedStyle(grid).gridTemplateColumns" in ACTIONS
    assert "ghosts.slice(trackCount).forEach((ghost) => ghost.remove())" in ACTIONS
    assert "trimPickerGridLoadingRow(scopeEl);" in ACTIONS
    assert "if (!appendFrom && !footerOnly) trimPickerGridLoadingRow(list);" in ACTIONS


def test_grid_loading_trims_ghosts_to_the_live_computed_track_count() -> None:
    run_in_module_context(
        """
        const removed = [];
        const ghosts = Array.from({ length: 7 }, (_, index) => ({ remove() { removed.push(index); } }));
        const grid = {
          matches: (selector) => selector === '.candidate-list.is-card-grid',
          querySelectorAll: (selector) => selector === '.candidate-grid-card.is-ghost' ? ghosts : [],
        };
        getComputedStyle = () => ({
          gridTemplateColumns: 'minmax(0px, 1fr) minmax(0px, 1fr) minmax(0px, 1fr) minmax(0px, 1fr) minmax(0px, 1fr)',
        });
        trimPickerGridLoadingRow(grid);
        if (removed.join(',') !== '5,6') process.exit(44);
        """,
        entry=ROOT / "ceres/static/picker-actions.js",
    )


def test_grid_arrival_and_placement_use_gentle_existing_motion_paths() -> None:
    assert "is-grid-arriving" in CANDIDATES
    assert "candidate-grid-details-in 320ms ease-in both" in CSS
    assert "picker.cardsShownAt = null" in ACTIONS
    assert "function closeGridPickerAfterChoice" in ACTIONS
    assert "holdFillRunHeight();" in ACTIONS[ACTIONS.index("function closeGridPickerAfterChoice"):ACTIONS.index("function closePickerAfterFill")]
    assert "panel.classList.add('is-closing')" in ACTIONS
    assert "if (picker.viewMode === 'grid') { closeGridPickerAfterChoice(row, picker); return; }" in ACTIONS


def test_grid_cards_are_intrinsic_height_and_top_aligned() -> None:
    grid_rule = CSS.split(".candidate-list.is-card-grid {", 1)[1].split("}", 1)[0]
    card_rule = CSS.split("\n.candidate-grid-card {", 1)[1].split("}", 1)[0]
    stage_rule = CSS.split(".candidate-grid-stage {", 1)[1].split("}", 1)[0]
    # Release v0.9.524 (commit 4eaecd58, "restore source plot badges") later appended its
    # own `.candidate-list.is-card-grid > .candidate-grid-card { grid-template-areas: none; }`
    # rule for the cross-plot-worker source badge, reusing this same selector text -- so
    # `rsplit` on the bare selector now finds THAT rule, not the sizing override this guard
    # means. Match the specific block that carries `grid-template-rows` instead of "whichever
    # one is last in the file".
    grid_override_blocks = [
        block for _sel, block in re.findall(
            r"(\.candidate-list\.is-card-grid > \.candidate-grid-card) \{([^}]*)\}", CSS,
        )
        if "grid-template-rows" in block
    ]
    assert len(grid_override_blocks) == 1
    grid_override = grid_override_blocks[0]
    assert "align-items: start" in grid_rule
    assert "grid-template-rows: auto auto" in card_rule
    assert "align-content: start" in card_rule
    assert "align-items: start" in card_rule
    assert "align-items: stretch" not in card_rule
    assert "block-size" not in stage_rule
    assert "grid-template-rows: auto auto" in grid_override
    assert "min-height" not in grid_override
    assert "min-block-size" not in grid_override
    assert ".candidate-card:not(.candidate-grid-card) { min-height: 9.4rem; }" in CSS


def test_grid_uses_native_stage_buttons_and_separate_combine_controls() -> None:
    grid_start = CANDIDATES.index("function renderCandidateGridCard")
    grid = CANDIDATES[grid_start:CANDIDATES.index("function pickerEmptyHtml", grid_start)]
    worker_grid = grid[grid.index("const construction = candidateEvaluation"):grid.index("function renderItemRarityGridCards")]
    assert '<button type="button" class="candidate-grid-select candidate-grid-stage" data-picker-action="choose"' in worker_grid
    assert 'candidateGridArtHtml(candidate)' in worker_grid
    assert 'candidate-grid-base' in worker_grid
    assert 'candidate-grid-boostable' in worker_grid
    assert 'candidateGridDetailHtml' not in worker_grid
    assert 'candidate-primary' not in worker_grid
    assert 'Total PP change' not in worker_grid
    assert 'Harvest /hr' not in worker_grid
    assert 'candidate-bloodline' not in worker_grid
    assert 'candidate-grid-stage' in CSS
    # (button migration, commit a38cb80d, 2026-09-16) moved Combine onto the
    # shared `ceres-btn ceres-btn--secondary` classes, retiring `.picker-combine`'s own
    # bespoke border/background/padding rules (including this grid-only padding
    # override) -- the shared token system now owns its spacing.
    assert 'class="picker-combine ceres-btn ceres-btn--secondary"' in CANDIDATES
    assert ".picker-combine { width: 100%; box-sizing: border-box; }" in CSS
    # (2026-09-16, commit 97b8f76d "restore shared default PP-box
    # padding") replaced this grid-only 0.2rem override with the shared control tokens
    # same as .picker-combine above.
    assert "padding-inline: var(--ceres-control-inline-pad);" in CSS
    assert ".candidate-grid-metric {" in CSS
    assert 'card.click()' not in ACTIONS
    assert ".candidate-grid-card[data-picker-action=\"choose\"]:focus-visible" in CSS
    assert ".candidate-readiness-overlay" in CSS
    assert "worker: ['🃏', 'Worker cards']" in CANDIDATES
    assert "runi: ['🃏', 'Runi cards']" in CANDIDATES
    assert "totem: ['🔮', 'Totems']" in CANDIDATES
    assert "title: ['🏷️', 'Titles']" in CANDIDATES


def test_list_branch_remains_frozen_while_item_grids_use_rarity_buttons() -> None:
    # "Frozen" means immune to the grid-view rework, not literally immortal: three
    # unrelated, later-landed features touch this same list branch legitimately
    # itemMoveSourceHtml (, commit 9e66c7ce, linked item unstake/restake), multi
    # splinter cardSplinters() (//, commit 8e962b96), the shared
    # `ceres-btn ceres-btn--secondary` classes on "Stake here"/"Replace" (), and
    # bloodlinePairBadgeHtml (commit d748157b), and 's Ability filter markup. The hash is re-pinned to that legitimate
    # content.
    list_start = CANDIDATES.index("function renderCandidateRow")
    grid_start = CANDIDATES.index("function candidateReadinessOverlayHtml")
    list_branch = CANDIDATES[list_start:grid_start]
    assert hashlib.sha256(list_branch.encode()).hexdigest() == "18df695bec143d537d782ca321dae2fb2964ecdbb4924beb54a825581ce3702b"
    assert 'class="candidate-row candidate-card ${candidate.planned ? \'is-planned \' : \'\'}${classification}' in list_branch
    assert 'candidate-status">${status.html}' in list_branch
    assert 'candidate-plot-change' in list_branch
    assert 'harvestChangeHtml(candidate, picker)' in list_branch
    assert 'data-picker-action="choose" data-candidate-index="${index}"' in list_branch
    item_grid_start = CANDIDATES.index("function renderItemRarityGridCards", grid_start)
    item_grid_end = CANDIDATES.index("function pickerEmptyHtml", item_grid_start)
    item_grid_branch = CANDIDATES[item_grid_start:item_grid_end]
    assert 'class="candidate-grid-card candidate-grid-rarity' in item_grid_branch
    assert 'data-picker-action="choose"' in item_grid_branch
    assert 'data-picker-rarity="${rarity}"' in item_grid_branch
    assert 'candidate-grid-detail' not in item_grid_branch
    assert 'candidate-primary' not in item_grid_branch


def test_item_rarity_grids_have_authoritative_group_counts() -> None:
    item_grid_start = CANDIDATES.index("function renderItemRarityGridCards")
    item_grid_end = CANDIDATES.index("function pickerEmptyHtml", item_grid_start)
    item_grid_branch = CANDIDATES[item_grid_start:item_grid_end]
    assert "const rarities = isTitle ? ['rare', 'epic', 'legendary'] : ['common', 'rare', 'epic', 'legendary'];" in CANDIDATES
    assert 'data-picker-rarity="${rarity}"' in CANDIDATES
    assert "const group = picker.rarityGroups?.[rarity] || { count: 0, candidate: null };" in item_grid_branch
    assert "const count = Number(group.count || 0);" in item_grid_branch
    assert "const disabled = !candidate || count < 1;" in item_grid_branch
    assert "${disabled ? ' is-unavailable' : ''}" in item_grid_branch
    assert '${count} available' in item_grid_branch
    assert "disabled aria-disabled=\"true\"" in item_grid_branch
    disabled_rule = CSS.split('.candidate-grid-rarity.is-unavailable:is(:disabled, [aria-disabled="true"]) {', 1)[1].split("}", 1)[0]
    assert re.sub(r"\s+", " ", disabled_rule).strip() == "opacity: 0.55; cursor: not-allowed;"
    assert "grayscale" not in disabled_rule
    assert '.candidate-grid-card:not(:disabled):not([aria-disabled="true"]):hover' in CSS
    assert ".candidate-grid-card:hover, .candidate-grid-card:focus-within" not in CSS
    assert 'candidate-grid-totem-boost' not in CANDIDATES
    assert "['totem', 'title'].includes(picker.groupId)" in CANDIDATES
    assert 'rarity_groups' in ACTIONS
    assert '"rarity_groups": rarity_groups' in (ROOT / "ceres/candidates.py").read_text(encoding="utf-8")


def test_totem_rarity_groups_use_full_pool_when_candidates_are_paged() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    items = []
    uids = []
    counts = {"common": 12, "rare": 9, "epic": 7, "legendary": 0}
    common_boosts = [0.1, 0.3, 0.3] + [0.01] * 9
    for rarity, count in counts.items():
        for ordinal in range(count):
            detail_id = 20_000 + len(items)
            name = f"{rarity.title()} Totem {ordinal:02d}"
            if rarity == "common" and ordinal == 1:
                name = "Common Totem B"
            elif rarity == "common" and ordinal == 2:
                name = "Common Totem A"
            boost = common_boosts[ordinal] if rarity == "common" else 0.02
            items.append({
                "item_detail_id": detail_id,
                "name": name,
                "rarity": rarity,
                "boost": boost,
                "count": 1,
            })
            uids.append(f"I-{detail_id}-{ordinal}")
    report = {
        "plots": [{
            "deed_uid": "totem-page-test",
            "facts": {
                "plot": "1-1-1", "rarity": "common", "status": "occupied",
                "resource": "GRAIN", "territory": "T",
            },
            "staking_facts": {
                "max_workers_allowed": 5, "total_base_pp_cap": 100_000,
                "is_power_core_staked": True, "is_energized": False,
                "is_runi_staked": False, "deed_rarity_boost": 0,
                "totem_boost": 0, "title_boost": 0, "runi_boost": 0,
                "deed_status_token_boost": 0,
                "red_biome_modifier": 0, "blue_biome_modifier": 0,
                "white_biome_modifier": 0, "black_biome_modifier": 0,
                "green_biome_modifier": 0, "gold_biome_modifier": 0,
            },
        }],
        "workers": [],
        "cards": [],
        "unassigned_items": {
            "totem": {"capturable": True, "items": items, "uids": uids},
        },
    }
    body = rank_candidates(
        report, "totem-page-test", "totem", player="player0005", now=now,
        sort="boost", limit=25, details_by_id={},
    )

    assert len(body["candidates"]) == 25
    assert body["returned"] == 25
    assert body["total"] == 28
    assert set(body["rarity_groups"]) == {"common", "rare", "epic", "legendary"}
    assert {rarity: body["rarity_groups"][rarity]["count"] for rarity in counts} == counts
    assert body["rarity_groups"]["legendary"]["candidate"] is None
    assert body["rarity_groups"]["common"]["candidate"]["asset_ref"] == "I-20002-2"
    assert body["rarity_groups"]["common"]["candidate"]["name"] == "Common Totem A"


def test_title_rarity_groups_use_catalogue_boost_not_names() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    report = {
        "plots": [{
            "deed_uid": "title-rarity-test",
            "facts": {
                "plot": "1-1-1", "rarity": "common", "status": "occupied",
                "resource": "GRAIN", "territory": "T",
            },
            "staking_facts": {
                "max_workers_allowed": 5, "total_base_pp_cap": 100_000,
                "is_power_core_staked": True, "is_energized": False,
                "is_runi_staked": False, "deed_rarity_boost": 0,
                "totem_boost": 0, "title_boost": 0, "runi_boost": 0,
                "deed_status_token_boost": 0,
                "red_biome_modifier": 0, "blue_biome_modifier": 0,
                "white_biome_modifier": 0, "black_biome_modifier": 0,
                "green_biome_modifier": 0, "gold_biome_modifier": 0,
            },
        }],
        "workers": [],
        "cards": [],
        "item_catalogue": [
            {"id": 41_001, "name": "Alpha", "rarity": None, "boost": 0.10},
            {"id": 41_002, "name": "Beta", "rarity": None, "boost": 0.25},
        ],
        "unassigned_items": {
            "title": {
                "capturable": True,
                "items": [
                    {"item_detail_id": 41_001, "name": "Alpha", "count": 2},
                    {"item_detail_id": 41_002, "name": "Beta", "count": 1},
                ],
                "uids": ["I-41001-a", "I-41001-b", "I-41002-a"],
            },
        },
    }
    body = rank_candidates(
        report, "title-rarity-test", "title", player="player0005", now=now,
        sort="boost", limit=1, details_by_id={},
    )

    assert body["returned"] == 1
    assert body["total"] == 3
    assert set(body["rarity_groups"]) == {"rare", "epic", "legendary"}
    assert {rarity: body["rarity_groups"][rarity]["count"] for rarity in body["rarity_groups"]} == {
        "rare": 2, "epic": 1, "legendary": 0,
    }
    assert body["rarity_groups"]["rare"]["candidate"]["rarity"] == "rare"
    assert body["rarity_groups"]["legendary"]["candidate"] is None
    rare_body = rank_candidates(
        report, "title-rarity-test", "title", player="player0005", now=now,
        rarity="rare", sort="boost", limit=25, details_by_id={},
    )
    assert rare_body["total"] == 2
    assert rare_body["rarity_groups"]["rare"]["count"] == 2
    assert rare_body["rarity_groups"]["epic"]["count"] == 0


def test_grid_renderers_cover_readiness_terrain_native_stage_and_item_rarity_buttons() -> None:
    run_in_module_context(
        """
        const picker = { groupId: 'worker', selectedKey: null };
        const ready = {
          asset_ref: 'ready', uid: 'ready', card_detail_id: 1001, name: 'Ready Worker',
          base_pp: 100, terrain_boost: 0.25, bcx: 1, bcx_max: 400, foil: false,
          construction_pp: { status: 'point', value: 125 }, cooldown: { state: 'idle' },
          level: 3, edition: 3, splinter: 'fire', rarity: 'common', card_set: 'chaos',
        };
        S.IDLE_CARDS_CACHE = [
          ready,
          { ...ready, asset_ref: 'ready-copy', uid: 'ready-copy' },
        ];
        const cooling = {
          asset_ref: 'cooling', name: 'Cooling Worker', base_pp: 100, terrain_boost: -0.25,
          construction_pp: { status: 'point', value: 75 },
          cooldown: { state: 'cooling', ready_at: '2026-09-18T00:00:00Z' },
          level: 3, edition: 3, splinter: 'fire', rarity: 'common', card_set: 'chaos',
        };
        const survival = {
          asset_ref: 'survival', name: 'Survival Worker', base_pp: 100, terrain_boost: 0,
          construction_pp: { status: 'point', value: 100 },
          cooldown: { state: 'survival_locked', ready_at: '2026-09-18T00:00:00Z' },
          level: 3, edition: 3, splinter: 'fire', rarity: 'common', card_set: 'chaos',
        };
        const gold = { ...ready, asset_ref: 'gold', foil: 1 };
        const black = { ...ready, asset_ref: 'black', foil: 3 };
        const readyHtml = renderCandidateGridCard(ready, 0, picker, false);
        const coolingHtml = renderCandidateGridCard(cooling, 1, picker, false);
        const survivalHtml = renderCandidateGridCard(survival, 2, picker, false);
        const goldHtml = renderCandidateGridCard(gold, 3, picker, false);
        const blackHtml = renderCandidateGridCard(black, 4, picker, false);
        if (!readyHtml.includes('<button type="button" class="candidate-grid-select candidate-grid-stage" data-picker-action="choose"') || readyHtml.includes('candidate-readiness-overlay')) process.exit(20);
        if (!readyHtml.includes('candidate-grid-boostable is-positive')) process.exit(21);
        if (!coolingHtml.includes('candidate-readiness-overlay is-danger') || !coolingHtml.includes('<span>Ready in</span><span class="candidate-readiness-badge">')) process.exit(22);
        if (!coolingHtml.includes('candidate-grid-boostable is-negative')) process.exit(23);
        if (!survivalHtml.includes('<button type="button" class="candidate-grid-select candidate-grid-stage" data-picker-action="choose"') || !survivalHtml.includes(' disabled')) process.exit(24);
        if (!survivalHtml.includes('candidate-readiness-overlay is-danger') || !survivalHtml.includes('<span>Ready in</span>') || survivalHtml.includes('Revives in') || !survivalHtml.includes('candidate-readiness-badge')) process.exit(25);
        const stageEnd = readyHtml.indexOf('</button>');
        const toolsStart = readyHtml.indexOf('<div class="candidate-grid-tools">');
        const combineAt = readyHtml.indexOf('data-picker-action="combine"');
        if (stageEnd < 0 || toolsStart <= stageEnd || combineAt <= toolsStart || readyHtml.slice(0, stageEnd).includes('data-picker-action="combine"')) process.exit(26);
        if ([readyHtml, coolingHtml, survivalHtml].some((html) => /Details|Stake here|Replace|Terrain|Total PP change|Harvest \/hr|Bloodline/.test(html))) process.exit(27);
        if (!readyHtml.includes('aria-label="Set: Chaos. Edition: Reward"') || !readyHtml.includes('data-tip-body="Edition: Reward"') || readyHtml.includes('<span>Reward</span>')) process.exit(41);
        if (!goldHtml.includes('candidate-grid-art-foil">Gold</span>') || !blackHtml.includes('candidate-grid-art-foil">Black Foil</span>')) process.exit(42);
        if (goldHtml.indexOf('candidate-grid-art-foil') > goldHtml.indexOf('</span>')) process.exit(43);
        const totems = renderItemRarityGridCards({
          groupId: 'totem',
          rarityGroups: {
            common: { count: 2, candidate: { asset_ref: 'common' } },
            rare: { count: 0, candidate: null },
            epic: { count: 1, candidate: { asset_ref: 'epic' } },
            legendary: { count: 0, candidate: null },
          },
        }, false);
        if ((totems.match(/<button\\b/g) || []).length !== 4) process.exit(28);
        if ((totems.match(/<button[^>]* disabled/g) || []).length !== 2) process.exit(29);
        if (totems.includes('candidate-grid-totem-boost')) process.exit(30);
        const unavailableTotems = totems.match(/<button[^>]*is-unavailable[^>]*>/g) || [];
        if (unavailableTotems.length !== 2) process.exit(45);
        if (unavailableTotems.some((tag) => !tag.includes(' disabled aria-disabled="true"') || !tag.includes(', 0 available'))) process.exit(46);
        const titles = renderItemRarityGridCards({
          groupId: 'title',
          rarityGroups: {
            rare: { count: 2, candidate: { asset_ref: 'rare-title' } },
            epic: { count: 1, candidate: { asset_ref: 'epic-title' } },
            legendary: { count: 0, candidate: null },
          },
        }, true);
        if ((titles.match(/<button\\b/g) || []).length !== 3) process.exit(36);
        if ((titles.match(/<button[^>]* disabled/g) || []).length !== 1) process.exit(37);
        if (!titles.includes('Rare Title') || !titles.includes('Epic Title') || !titles.includes('Legendary Title')) process.exit(38);
        if (!titles.includes('2 available') || !titles.includes('1 available') || !titles.includes('0 available')) process.exit(39);
        if (!titles.includes('rarity-gem') || titles.includes('candidate-grid-detail')) process.exit(40);
        const unavailableTitles = titles.match(/<button[^>]*is-unavailable[^>]*>/g) || [];
        if (unavailableTitles.length !== 1) process.exit(47);
        if (unavailableTitles.some((tag) => !tag.includes(' disabled aria-disabled="true"') || !tag.includes(', 0 available'))) process.exit(48);
        """,
        entry=ROOT / "ceres/static/candidates.js",
        extra_context_js="""
          context.BOARD = { plots: [] };
          context.ASSET_BASE = '';
          context.esc = (value) => String(value ?? '').replace(/[&<>\"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[char]));
          context.num = (value) => String(value);
          context.terrainValue = (value) => value === null || value === undefined ? null : Number(value);
          context.editorCardImageHtml = () => '<img alt="card art">';
          context.editionDisplayLabel = (value) => `Edition ${value}`;
          context.foilBadgeHtml = (candidate, extraClass = '') => {
            const label = candidate.foil === 1 ? 'Gold' : candidate.foil === 3 ? 'Black Foil' : '';
            return label ? `<span class="foil-badge ${extraClass}">${label}</span>` : '';
          };
          context.splinterIconHtml = () => '<span>splinter</span>';
          context.rarityGemHtml = () => '<span class="rarity-gem">rarity</span>';
          context.setIconOnlyHtml = (cardSet, edition, detail) => `<span data-edition-tooltip="${detail}">set</span>`;
          context.totemIconHtml = (rarity) => `<span>${rarity}</span>`;
          context.titleIconHtml = () => '';
          context.titleRarityGem = () => '';
          context.humanizeInstant = () => ({ relative: '3d', absolute: 'later' });
          context.countdownText = () => '3d';
          context.abilityTipModel = () => ({ icon: '', title: '' });
          context.abilityRowText = () => '';
          context.tipAttrs = () => '';
          context.tooltipIconPath = () => '';
          context.workerSubType = () => '';
          context.abilityIconHtml = () => '';
          context.bloodlineOutranked = () => false;
          context.combineTipModel = () => ({});
          context.EDITION_LABEL = {};
          context.S = {};
          context.FOIL_BADGE = '';
        """,
    )


def test_grid_subset_moves_to_edition_tooltip_and_foil_moves_to_art() -> None:
    grid_start = CANDIDATES.index("function candidateReadinessOverlayHtml")
    grid_end = CANDIDATES.index("function renderItemRarityGridCards", grid_start)
    worker_grid = CANDIDATES[grid_start:grid_end]
    assert "setIconOnlyHtml(candidate.card_set, candidate.edition, editionDisplayLabel(candidate.edition))" in worker_grid
    assert '<span>${esc(editionDisplayLabel(candidate.edition))}</span>' not in worker_grid
    assert "foilBadgeHtml(candidate, 'candidate-grid-art-foil')" in worker_grid
    assert "editionDetail = ''" in ICONS
    assert "Edition: ${editionDetail}" in ICONS
    assert ".candidate-grid-art-foil { position: absolute; inset: auto auto 0.4rem 50%" in CSS
    assert "background: color-mix(in srgb, var(--danger) 46%, transparent)" in CSS
    assert ".candidate-list.is-card-grid > .candidate-grid-card.is-not-ready .candidate-art img { filter: none; }" in CSS


def test_item_rarity_choose_stages_totem_and_replaces_title() -> None:
    run_in_module_context(
        """
        (async () => {
        const candidate = {
          asset_ref: 'totem-rare-1', name: 'Rare Totem', source_deed_uid: null,
          base_pp_delta: null, cooldown: {}, plot_impact: { status: 'not_applicable' },
        };
        const picker = {
          deedUid: 'deed-1', groupId: 'totem', slotIndex: 2, mode: 'stake',
          currentOccupant: null, candidates: [{ combine_info: { key: 'ready-worker', copies: [] } }],
          rarityGroups: { rare: { count: 3, candidate } }, filters: {}, chain: false,
        };
        S.CERES_PICKER = picker;
        const pickerAction = {
          dataset: { pickerAction: 'choose', pickerRarity: 'rare' },
          closest: () => null,
        };
        const event = {
          target: {
            closest: (selector) => selector === '[data-picker-action]' ? pickerAction : null,
          },
          preventDefault() {}, stopPropagation() {},
        };
        await handleSlotEditorClick(event);
        if (stageEvents.length !== 1) process.exit(31);
        if (stageEvents[0][0] !== 'deed-1' || stageEvents[0][1] !== 'totem' || stageEvents[0][2] !== 2) process.exit(32);
        if (stageEvents[0][4] !== 'totem-rare-1') process.exit(33);
        if (S.CERES_PICKER !== null) process.exit(34);
        const titleCandidate = {
          asset_ref: 'title-epic-1', name: 'Epic Title', source_deed_uid: null,
          base_pp_delta: null, cooldown: {}, plot_impact: { status: 'not_applicable' },
        };
        const titlePicker = {
          deedUid: 'deed-1', groupId: 'title', slotIndex: 0, mode: 'replace',
          currentOccupant: { asset_ref: 'old-title', name: 'Old Title' }, candidates: [],
          rarityGroups: { epic: { count: 2, candidate: titleCandidate } }, filters: {}, chain: false,
        };
        S.CERES_PICKER = titlePicker;
        const titleAction = {
          dataset: { pickerAction: 'choose', pickerRarity: 'epic' },
          closest: () => null,
        };
        const titleEvent = {
          target: {
            closest: (selector) => selector === '[data-picker-action]' ? titleAction : null,
          },
          preventDefault() {}, stopPropagation() {},
        };
        await handleSlotEditorClick(titleEvent);
        if (stageEvents.length !== 2) process.exit(36);
        if (stageEvents[1][1] !== 'title' || stageEvents[1][3] !== 'replace') process.exit(37);
        if (stageEvents[1][4] !== 'title-epic-1' || stageEvents[1][6] !== 'old-title') process.exit(38);
        if (S.CERES_PICKER !== null) process.exit(39);
        S.CERES_PICKER = picker;
        const combineAction = { dataset: { pickerAction: 'combine', candidateIndex: '0' }, closest: () => null };
        const combineEvent = {
          target: {
            closest: (selector) => selector === '[data-picker-action]' ? combineAction : null,
          },
          preventDefault() {}, stopPropagation() {},
        };
        await handleSlotEditorClick(combineEvent);
        if (combineEvents !== 1 || combineDialogKey !== 'ready-worker' || stageEvents.length !== 2) process.exit(35);
        })();
        """,
        entry=ROOT / "ceres/static/picker-actions.js",
        source=CHOOSE_VM_SOURCE,
        extra_context_js="""
          context.S = { CERES_PICKER: null };
          context.BOARD = { plots: [{ deed_uid: 'deed-1' }] };
          context.stageEvents = [];
          context.combineEvents = 0;
          context.combineDialogKey = null;
          context.IDLE_COMBINE_GROUPS = new Map();
          context.editorGroups = () => [{ id: context.S.CERES_PICKER.groupId }];
          context.candidateDelta = () => 12;
          context.editorOccupant = (candidate) => ({ asset_ref: candidate.asset_ref, name: candidate.name });
          context.stageForSlot = (...args) => context.stageEvents.push(args);
          context.candidateCombineInfo = (candidate) => candidate.combine_info || null;
          context.openCombineGroupDialog = (key) => { context.combineEvents += 1; context.combineDialogKey = key; };
          context.mutateEditorSlot = () => {};
          context.stageToast = () => {};
          context.editorSlotLabel = () => 'Totem';
          context.stageDeltaToastText = (delta) => String(delta);
          context.releaseFillRunHeight = () => {};
          context.unbindCountdowns = () => {};
          context.abortWorkerUpgradeOptions = () => {};
          context.updateMountedRow = () => {};
          context.scrollPlotAfterSlotAdd = () => {};
        """,
    )
