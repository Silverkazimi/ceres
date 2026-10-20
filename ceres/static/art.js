import { ASSET_BASE } from './state.js';

// S10 round 2 / L4-06: the public release ships no game artwork; an image that cannot be loaded (not listed, offline, host down) is simply
// hidden instead of showing the browser's broken-image icon. Capture phase: image errors do not bubble.
try {
  document.addEventListener('load', (event) => {  // S10 / L4-03: a retry that succeeds shows the picture again
    const image = event.target;
    if (image && image.tagName === 'IMG' && image.hasAttribute('data-art-missing')) image.removeAttribute('data-art-missing');
  }, true);
  document.addEventListener('error', (event) => {
    const image = event.target;
    if (image && image.tagName === 'IMG' && String(image.currentSrc || image.src || '').includes('/static/assets/')) image.setAttribute('data-art-missing', '');
  }, true);
} catch (_) { /* no document in tests */ }

// Worker sub-table terrain/worksite scene (board-polish batch 19, item 17, 2026-08-21).
// the owner: beautify the sub-table with the plot's real terrain photo behind it and its
// worksite building overlaid, table shifted right so it doesn't sit on the building, own
// background for reading contrast.
// Both asset sets are gitignored (present on disk, never `git add`ed -- see
// tools/make_thumbnails.py's own header) and are NOT in ceres/static/assets/manifest.json's
// inventory (that manifest only covers the small, committed icon sets) -- so unlike every
// other Stage-B helper in this file, these two path builders can't just look up a
// pre-verified filename; they reconstruct it from the plot's own data fields, per the
// selection rules below (worked out and probed in work/worksite-images-manifest.md and
// work/ceres-worker-art.md).
// TERRAIN BACKGROUND -- the build variant follows the same resolved overlay as the
// building layer, so an existing worksite gets the authored building terrain. Each
// terrain's magical variant only has 3 of the 7 possible elements on disk (confirmed
// 2026-08-21) but every live magical plot's own element is always one of its terrain's 3
// this never needs a fallback in practice.
const LAKE_SCENES = new Map();
let mobileLakeScene = null;

export function beginLakeScene(row, { mobile = false } = {}) {
  const underwater = String(row.terrain || '').toLowerCase() === 'lake' && Math.random() < 0.5;
  if (mobile) mobileLakeScene = { uid: row.deed_uid, underwater };
  else LAKE_SCENES.set(row.deed_uid, underwater);
}

export function endLakeScene(uid, { mobile = false } = {}) {
  if (mobile) {
    if (mobileLakeScene?.uid === uid) mobileLakeScene = null;
  } else LAKE_SCENES.delete(uid);
}

function sceneResource(row) {
  const incoming = row.pending_change?.kind === 'worksite_change' ? row.pending_change.to : null;
  const staged = !row.resource && !incoming ? globalThis.ceresStagedWorksiteBuild?.(row.deed_uid)?.target : null;
  return String(row.resource || incoming || staged || '').toLowerCase().replace(/[^a-z]/g, '');
}

function underwaterLakeScene(row) {
  const selected = mobileLakeScene?.uid === row.deed_uid ? mobileLakeScene.underwater : LAKE_SCENES.get(row.deed_uid);
  if (String(row.terrain || '').toLowerCase() !== 'lake' || selected !== true) return false;
  const resource = sceneResource(row);
  // Keep both layers standard when the actual worksite has no verified underwater plate.
  return !resource || ['grain', 'aura', 'research', 'sps'].includes(resource)
    || (resource === 'tax' && ['castle', 'keep'].includes(String(row.worksite_type || '').toLowerCase()));
}

export function terrainBackgroundPath(row) {
  const terrain = (row.terrain || '').toLowerCase().replace(/[^a-z]/g, '');
  if (!terrain) return null;
  if (underwaterLakeScene(row)) return `${ASSET_BASE}/web/land/terrain-backgrounds/lake_natural_underwater${worksiteOverlayPath(row) ? '_build' : ''}.webp`;
  const status = (row.plot_status || '').toLowerCase();
  const magic = (row.magic_type || '').toLowerCase().replace(/[^a-z]/g, '');
  const variant = status === 'magical' && magic ? `magical_${magic}` : (status === 'occupied' ? 'occupied' : 'natural');
  const baseName = `${terrain}_${variant}`;
  const buildName = `${baseName}_build.jpg`;
  const buildSuffix = worksiteOverlayPath(row) && TERRAIN_BUILD_VARIANTS.has(buildName) ? '_build' : '';
  return `${ASSET_BASE}/web/land/terrain-backgrounds/${terrain}_${variant}${buildSuffix}.webp`;
}

const TERRAIN_BUILD_VARIANTS = new Set([
  'badlands_magical_death_build.jpg', 'badlands_magical_fire_build.jpg', 'badlands_magical_water_build.jpg', 'badlands_natural_build.jpg', 'badlands_occupied_build.jpg',
  'bog_magical_death_build.jpg', 'bog_magical_dragon_build.jpg', 'bog_magical_water_build.jpg', 'bog_natural_build.jpg', 'bog_occupied_build.jpg',
  'caldera_magical_dragon_build.jpg', 'caldera_magical_fire_build.jpg', 'caldera_magical_life_build.jpg', 'caldera_natural_build.jpg', 'caldera_occupied_build.jpg',
  'canyon_magical_death_build.jpg', 'canyon_magical_dragon_build.jpg', 'canyon_magical_fire_build.jpg', 'canyon_natural_build.jpg', 'canyon_occupied_build.jpg',
  'desert_magical_earth_build.jpg', 'desert_magical_fire_build.jpg', 'desert_magical_water_build.jpg', 'desert_natural_build.jpg', 'desert_occupied_build.jpg',
  'forest_magical_earth_build.jpg', 'forest_magical_fire_build.jpg', 'forest_magical_life_build.jpg', 'forest_natural_build.jpg', 'forest_occupied_build.jpg',
  'hills_magical_dragon_build.jpg', 'hills_magical_earth_build.jpg', 'hills_magical_life_build.jpg', 'hills_natural_build.jpg', 'hills_occupied_build.jpg',
  'jungle_magical_earth_build.jpg', 'jungle_magical_fire_build.jpg', 'jungle_magical_life_build.jpg', 'jungle_natural_build.jpg', 'jungle_occupied_build.jpg',
  'lake_magical_earth_build.jpg', 'lake_magical_life_build.jpg', 'lake_magical_water_build.jpg', 'lake_natural_build.jpg', 'lake_occupied_build.jpg',
  'mountain_magical_death_build.jpg', 'mountain_magical_fire_build.jpg', 'mountain_magical_life_build.jpg', 'mountain_natural_build.jpg', 'mountain_occupied_build.jpg',
  'plains_magical_dragon_build.jpg', 'plains_magical_fire_build.jpg', 'plains_magical_life_build.jpg', 'plains_natural_build.jpg', 'plains_occupied_build.jpg',
  'river_magical_earth_build.jpg', 'river_magical_life_build.jpg', 'river_magical_water_build.jpg', 'river_natural_build.jpg', 'river_occupied_build.jpg',
  'swamp_magical_death_build.jpg', 'swamp_magical_earth_build.jpg', 'swamp_magical_water_build.jpg', 'swamp_natural_build.jpg', 'swamp_occupied_build.jpg',
  'tundra_magical_earth_build.jpg', 'tundra_magical_life_build.jpg', 'tundra_magical_water_build.jpg', 'tundra_natural_build.jpg', 'tundra_occupied_build.jpg',
]);

// WORKSITE OVERLAY -- keyed off row.resource + row.terrain. Two asset families on disk
// (work/worksite-images-manifest.md, full CDN probe 2026-08-21): family A
// (`{resource}_{terrain}.webp`) only has grain/research/sps, all 14 terrains each; family B
// (`{resource}/plot/{terrain}_lvl_1.webp`) only has wood/stone/iron on their 3-4 NATIVE
// terrains plus aura on all 14 -- research/sps have no family-B art at all. Family A was
// originally shipped as PNG at ~327KB/file (vs. family B's 32-42KB WebP for the same
// 2829x1080 RGBA canvas) -- converted to WebP (quality 82, lossy) in
// work/ceres-worksite-reposition/, ~5-6x smaller with no visible quality loss on this kind
// of flat-shaded render. A real plot's resource is always native to its terrain (the game
// only ever assigns a resource a terrain can actually produce), so between the two families
// every real combination has art -- verified against all 130 live plots in
// work/ceres-worker-art.md, zero landing on the `null` branch below. Selection order
// 1. research/sps -> family A (their only family).
// 2. aura -> family B (its only family, all 14 terrains).
// 3. wood/stone/iron -> family B on their native terrain (their only family).
// 4. grain -> family B on grain's 4 native terrains (bog/lake/plains/river); family A
// elsewhere (the live account has grain on Hills/Mountain/Tundra too, none of which
// are grain's family-B native set, but all 14 exist in family A).
const WORKSITE_FAMILY_B_NATIVE_TERRAIN = {
  wood: ['forest', 'jungle', 'swamp', 'tundra'],
  stone: ['canyon', 'desert', 'hills'],
  iron: ['badlands', 'caldera', 'mountain'],
  grain: ['bog', 'lake', 'plains', 'river'],
};
// worksite images are served `immutable` for 30 days -- bump ART_REV whenever an image changes
// or browsers keep drawing the old crop.
// (2026-09-14): Keep/Castle _icon.webp files recropped (centred); every reference must bust the 30-day immutable cache.
export const ART_REV = '20260914a';

const CONSTRUCTION_TERRAINS = new Set([
  'badlands', 'bog', 'caldera', 'canyon', 'desert', 'forest', 'hills',
  'jungle', 'lake', 'mountain', 'plains', 'river', 'swamp', 'tundra',
]);

function constructionOverlayFile(row) {
  const pending = row?.pending_change;
  if (!pending || pending.kind !== 'worksite_change') return null;
  if (underwaterLakeScene(row)) return `${ASSET_BASE}/land/worksites/productionBuilding/construction_lake_underwater.png`;
  const terrain = String(row.terrain || '').toLowerCase().replace(/[^a-z]/g, '');
  return CONSTRUCTION_TERRAINS.has(terrain)
    ? `${ASSET_BASE}/land/constructions/construction_${terrain}.png`
    : null;
}

function worksiteOverlayPath(row) {
  const url = worksiteOverlayFile(row);
  return url ? `${url}?r=${ART_REV}` : null;
}

function worksiteOverlayFile(row) {
  return constructionOverlayFile(row) || finishedWorksiteOverlayFile(row);
}

export function finishedWorksiteOverlayPath(row) {
  const url = finishedWorksiteOverlayFile(row);
  return url ? `${url}?r=${ART_REV}` : null;
}

function finishedWorksiteOverlayFile(row) {
  // A plot MID WORKSITE-CHANGE reports `resource: null` and `worksite_type: ''` -- on
  // every field this function reads, byte-identical to land that was never built on. The
  // only thing separating them is `pending_change`, which the board already sends and
  // which was unused here, so a plot with a worksite rendered as bare ground ().
  // It is worth one line because the state has a population of ONE plot in 130, so no
  // sweep and no sampled fixture ever caught it.
  const resource = sceneResource(row);
  const terrain = (row.terrain || '').toLowerCase().replace(/[^a-z]/g, '');
  // A plot genuinely without a resource AND without an incoming one legitimately has no
  // worksite building -- not a bug.
  if (!resource || !terrain) return null;
  if (underwaterLakeScene(row)) {
    const base = `${ASSET_BASE}/land/worksites`;
    if (resource === 'grain' || resource === 'aura') return `${base}/productionBuilding/${resource}/plot/lake_underwater_lvl_1.webp`;
    if (resource === 'research' || resource === 'sps') return `${base}/productionBuilding/${resource}_lake_underwater.png`;
    if (resource === 'tax' && ['castle', 'keep'].includes(String(row.worksite_type || '').toLowerCase())) return `${base}/buildings/lake_${String(row.worksite_type).toLowerCase()}_big_underwater.png`;
  }

  // a Keep/Castle plot (resource TAX) gets the
  // game's own building art (land/buildings/{terrain}_{keep|castle}_big.png, cropped to this set's uniform format).
  if (resource === 'tax') {
    const build = String(row.worksite_type || '').toLowerCase();
    return build === 'castle' || build === 'keep' ? `${ASSET_BASE}/land/worksites/productionBuildingUniform/tax/${build}/${terrain}.webp` : null;
  }
  if (resource === 'research' || resource === 'sps') {
    return `${ASSET_BASE}/land/worksites/productionBuildingUniform/${resource}_${terrain}.webp`;
  }
  if (resource === 'aura') {
    return `${ASSET_BASE}/land/worksites/productionBuildingUniform/aura/plot/${terrain}_lvl_1.webp`;
  }
  const nativeTerrains = WORKSITE_FAMILY_B_NATIVE_TERRAIN[resource];
  if (nativeTerrains && nativeTerrains.includes(terrain)) {
    return `${ASSET_BASE}/land/worksites/productionBuildingUniform/${resource}/plot/${terrain}_lvl_1.webp`;
  }
  if (resource === 'grain') {
    return `${ASSET_BASE}/land/worksites/productionBuildingUniform/grain_${terrain}.webp`;
  }
  // SAFETY NET ONLY, should never fire for a real plot (see the comment above) -- if it
  // does, that's a fetch/extraction bug (a missing combo that should exist per the owner's
  // nativity argument), not a legitimate "no art" case. Falls through to a plain terrain
  // background with no overlay, never a broken image.
  return null;
}

// Same selection logic as worksiteOverlayPath() above, but against the local
// `productionBuildingSquare/` tree (640x640, with the building in a smaller
// grounded region) instead of the remote CDN's full 2829x1080 plate -- the modal's tile
// art needs a square icon, not the artist's whole scene, and a loopback app
// with no fallback has no business depending on a public CDN for it (R-23
// Phase 1). Coverage verified complete: every legal target/terrain
// combination this function can be asked for resolves to a real file under
// `ceres/static/assets/land/worksites/productionBuildingSquare/`.
export function worksiteTileArtPath(target, terrain) {
  const resource = (target || '').toLowerCase().replace(/[^a-z]/g, '');
  const terrainKey = (terrain || '').toLowerCase().replace(/[^a-z]/g, '');
  if (!resource || !terrainKey) return null;
  const base = `${ASSET_BASE}/land/worksites/productionBuildingSquare`;
  if (resource === 'research' || resource === 'sps') {
    return `${base}/${resource}_${terrainKey}.webp`;
  }
  if (resource === 'aura') {
    return `${base}/aura/plot/${terrainKey}_lvl_1.webp`;
  }
  const nativeTerrains = WORKSITE_FAMILY_B_NATIVE_TERRAIN[resource];
  if (nativeTerrains && nativeTerrains.includes(terrainKey)) {
    return `${base}/${resource}/plot/${terrainKey}_lvl_1.webp`;
  }
  if (resource === 'grain') {
    return `${base}/grain_${terrainKey}.webp`;
  }
  return null;
}

// Builds the `style` attribute for the editor scene wrapper: 1-2 stacked CSS
// `background-image` layers (worksite overlay on top of the terrain photo, or just the
// terrain photo alone). Deliberately CSS backgrounds, not `<img>` tags, for both layers
// an `<img src>` starts fetching the instant it's in the DOM regardless of `display:none`
// on an ancestor (this sub-table's `<tr class="expand-row">` starts and stays
// `display:none` until first expanded, and workerSubtable() re-renders its cached HTML on
// every updateMountedRow() call even while collapsed -- see that function's own comment).
// `background-image` does NOT fetch while its element (or an ancestor) is `display:none` in
// any current engine, so this composition only ever downloads once a plot is actually
// expanded, in every case an expanded row is later re-rendered -- no 130-photo preload.
// (FR2): where the BUILDING sits inside each productionBuildingUniform/ image (1560x640), measured
// from the image alpha: [building centre x as a fraction of the width, building bottom as a fraction of
// the height, image height in rem]. The height is capped per image so the building's full width fits
// inside the band when centred on the harvest box. Owner rules: building centre = harvest-box centre
// base = the artwork's lowest visible pixel (alpha > 40), measured per image () -- the CSS keeps it
// .5rem above the harvest box, so no building structure ever touches or hides behind the box.
// [3..5] = the artwork's outer box (left, top, right as fractions, alpha > 40). ceres.css fits every
// image into its allowed box with min() -- right of the affinity icons, below the plot controls, left of the
// first tile, above the harvest box -- so ZERO pixels are ever cut off or hidden; the building stays centred.
const WORKSITE_ART_GEOMETRY = {"aura/plot/badlands_lvl_1.webp":[0.4718,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/bog_lvl_1.webp":[0.4718,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/caldera_lvl_1.webp":[0.4715,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/canyon_lvl_1.webp":[0.4718,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/desert_lvl_1.webp":[0.4715,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/forest_lvl_1.webp":[0.4718,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/hills_lvl_1.webp":[0.4715,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/jungle_lvl_1.webp":[0.4715,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/lake_lvl_1.webp":[0.4715,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/mountain_lvl_1.webp":[0.4715,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/plains_lvl_1.webp":[0.4715,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/river_lvl_1.webp":[0.4715,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/swamp_lvl_1.webp":[0.4715,0.9406,13.0,0.2359,0.0813,0.7647],"aura/plot/tundra_lvl_1.webp":[0.4718,0.9406,13.0,0.2359,0.0813,0.7647],"grain/plot/bog_lvl_1.webp":[0.5144,0.9406,11.18,0.0346,0.0813,0.9654],"grain/plot/lake_lvl_1.webp":[0.5144,0.9406,11.18,0.0346,0.0813,0.9654],"grain/plot/plains_lvl_1.webp":[0.5144,0.9406,11.18,0.0346,0.0813,0.9654],"grain/plot/river_lvl_1.webp":[0.5144,0.9406,11.18,0.0346,0.0813,0.9654],"grain_badlands.webp":[0.5612,0.9406,12.0,0.2718,0.0813,0.7282],"grain_bog.webp":[0.5564,0.9406,12.0,0.2718,0.0813,0.7288],"grain_caldera.webp":[0.559,0.9406,12.0,0.2731,0.0813,0.7276],"grain_canyon.webp":[0.5599,0.9406,12.0,0.2724,0.0813,0.7276],"grain_desert.webp":[0.5587,0.9406,12.0,0.2724,0.0813,0.7276],"grain_forest.webp":[0.5593,0.9406,12.0,0.2731,0.0813,0.7276],"grain_hills.webp":[0.558,0.9406,12.0,0.2731,0.0813,0.7263],"grain_jungle.webp":[0.5583,0.9406,12.0,0.2731,0.0813,0.7276],"grain_lake.webp":[0.5612,0.9406,12.0,0.2718,0.0813,0.7288],"grain_mountain.webp":[0.5551,0.9406,12.0,0.2718,0.0813,0.7288],"grain_plains.webp":[0.5551,0.9406,12.0,0.2724,0.0813,0.7276],"grain_river.webp":[0.5545,0.9406,12.0,0.2718,0.0813,0.7282],"grain_swamp.webp":[0.5561,0.9406,12.0,0.2724,0.0813,0.7276],"grain_tundra.webp":[0.5554,0.9406,12.0,0.2705,0.0813,0.7288],"iron/plot/badlands_lvl_1.webp":[0.4974,0.9406,13.0,0.1013,0.0813,0.8981],"iron/plot/caldera_lvl_1.webp":[0.508,0.9406,13.0,0.1423,0.0813,0.8583],"iron/plot/mountain_lvl_1.webp":[0.492,0.9406,13.0,0.1,0.0813,0.9006],"research_badlands.webp":[0.4929,0.9406,14.0,0.2744,0.0813,0.7256],"research_bog.webp":[0.4926,0.9406,14.0,0.2744,0.0813,0.7256],"research_caldera.webp":[0.4814,0.9406,14.0,0.2744,0.0813,0.7256],"research_canyon.webp":[0.4933,0.9406,14.0,0.2744,0.0813,0.7256],"research_desert.webp":[0.492,0.9406,14.0,0.2744,0.0813,0.7256],"research_forest.webp":[0.4936,0.9406,14.0,0.2744,0.0813,0.7256],"research_hills.webp":[0.4936,0.9406,14.0,0.2744,0.0813,0.7256],"research_jungle.webp":[0.4929,0.9406,14.0,0.2744,0.0813,0.7256],"research_lake.webp":[0.4933,0.9406,14.0,0.2744,0.0813,0.7256],"research_mountain.webp":[0.492,0.9406,14.0,0.2744,0.0813,0.7256],"research_plains.webp":[0.4913,0.9406,14.0,0.2744,0.0813,0.7256],"research_river.webp":[0.492,0.9406,14.0,0.2744,0.0813,0.7263],"research_swamp.webp":[0.4923,0.9406,14.0,0.2744,0.0813,0.7256],"research_tundra.webp":[0.4808,0.9406,14.0,0.2744,0.0813,0.7263],"sps_badlands.webp":[0.5427,0.9406,12.5,0.2019,0.0813,0.7974],"sps_bog.webp":[0.5427,0.9406,12.5,0.2032,0.0813,0.7974],"sps_caldera.webp":[0.5414,0.9406,12.5,0.2013,0.0813,0.7981],"sps_canyon.webp":[0.5411,0.9406,12.5,0.2019,0.0813,0.7974],"sps_desert.webp":[0.5382,0.9406,12.5,0.2019,0.0813,0.7974],"sps_forest.webp":[0.5375,0.9406,12.5,0.2026,0.0813,0.7968],"sps_hills.webp":[0.5391,0.9406,12.5,0.2019,0.0813,0.7974],"sps_jungle.webp":[0.5398,0.9406,12.5,0.2026,0.0813,0.7974],"sps_lake.webp":[0.5365,0.9406,12.5,0.2026,0.0813,0.7968],"sps_mountain.webp":[0.5372,0.9406,12.5,0.2019,0.0813,0.7981],"sps_plains.webp":[0.5379,0.9406,12.5,0.2026,0.0813,0.7974],"sps_river.webp":[0.5375,0.9406,12.5,0.2026,0.0813,0.7974],"sps_swamp.webp":[0.5398,0.9406,12.5,0.2019,0.0813,0.7974],"sps_tundra.webp":[0.5395,0.9406,12.5,0.2026,0.0813,0.7974],"stone/plot/canyon_lvl_1.webp":[0.4946,0.9406,13.5,0.1506,0.0813,0.8494],"stone/plot/desert_lvl_1.webp":[0.4946,0.9406,13.5,0.1506,0.0813,0.8494],"stone/plot/hills_lvl_1.webp":[0.4946,0.9406,13.5,0.1506,0.0813,0.8494],"wood/plot/forest_lvl_1.webp":[0.426,0.9406,12.287,0.0327,0.0813,0.9667],"wood/plot/jungle_lvl_1.webp":[0.426,0.9406,12.287,0.0327,0.0813,0.9667],"wood/plot/swamp_lvl_1.webp":[0.426,0.9406,12.287,0.0327,0.0813,0.9667],"wood/plot/tundra_lvl_1.webp":[0.4158,0.9406,12.4,0.0372,0.0813,0.9622],"tax/castle/badlands.webp":[0.501,0.9406,13.0,0.2615,0.0813,0.7404],"tax/keep/badlands.webp":[0.5003,0.9406,13.0,0.3237,0.0813,0.6769],"tax/castle/bog.webp":[0.5006,0.9406,13.0,0.2615,0.0828,0.7397],"tax/keep/bog.webp":[0.4968,0.9391,13.0,0.275,0.0813,0.7186],"tax/castle/caldera.webp":[0.4987,0.9219,13.0,0.2731,0.0828,0.7244],"tax/keep/caldera.webp":[0.5006,0.9406,13.0,0.3237,0.0813,0.6776],"tax/castle/canyon.webp":[0.5003,0.9391,13.0,0.2821,0.0813,0.7186],"tax/keep/canyon.webp":[0.4981,0.9406,13.0,0.2724,0.0813,0.7237],"tax/castle/desert.webp":[0.4997,0.9391,13.0,0.2917,0.0813,0.7077],"tax/keep/desert.webp":[0.4968,0.9406,13.0,0.275,0.0813,0.7186],"tax/castle/forest.webp":[0.5,0.9406,13.0,0.259,0.0828,0.741],"tax/keep/forest.webp":[0.4968,0.9391,13.0,0.275,0.0813,0.7186],"tax/castle/hills.webp":[0.501,0.9391,13.0,0.2718,0.0813,0.7301],"tax/keep/hills.webp":[0.4974,0.9406,13.0,0.275,0.0813,0.7199],"tax/castle/jungle.webp":[0.4987,0.9406,13.0,0.2647,0.0813,0.7327],"tax/keep/jungle.webp":[0.4968,0.9406,13.0,0.2744,0.0813,0.7192],"tax/castle/lake.webp":[0.5006,0.9391,13.0,0.2712,0.0813,0.7301],"tax/keep/lake.webp":[0.4971,0.9406,13.0,0.275,0.0813,0.7192],"tax/castle/mountain.webp":[0.4997,0.9391,13.0,0.2808,0.0813,0.7186],"tax/keep/mountain.webp":[0.4971,0.9406,13.0,0.275,0.0813,0.7192],"tax/castle/plains.webp":[0.4997,0.9391,13.0,0.2718,0.0828,0.7276],"tax/keep/plains.webp":[0.4968,0.9391,13.0,0.275,0.0813,0.7186],"tax/castle/river.webp":[0.501,0.9406,13.0,0.2737,0.0828,0.7282],"tax/keep/river.webp":[0.4971,0.9406,13.0,0.275,0.0813,0.7192],"tax/castle/swamp.webp":[0.501,0.9391,13.0,0.2718,0.0828,0.7301],"tax/keep/swamp.webp":[0.4971,0.9406,13.0,0.2744,0.0813,0.7199],"tax/castle/tundra.webp":[0.5,0.9406,13.0,0.284,0.0813,0.716],"tax/keep/tundra.webp":[0.4968,0.9391,13.0,0.275,0.0813,0.7186]};

const CONSTRUCTION_ART_GEOMETRY = {
  badlands: [0.22, 0.9167, 13, 0.0594, 0.513, 0.3807], bog: [0.22, 0.9176, 13, 0.0587, 0.512, 0.3814],
  caldera: [0.2193, 0.9157, 13, 0.0587, 0.512, 0.38], canyon: [0.2204, 0.9176, 13, 0.0583, 0.5111, 0.3825],
  desert: [0.2202, 0.9176, 13, 0.0583, 0.5111, 0.3821], forest: [0.22, 0.9213, 13, 0.059, 0.512, 0.3811],
  hills: [0.22, 0.9176, 13, 0.0587, 0.513, 0.3814], jungle: [0.2197, 0.9176, 13, 0.059, 0.5139, 0.3803],
  lake: [0.2195, 0.9185, 13, 0.058, 0.512, 0.3811], mountain: [0.2193, 0.9157, 13, 0.0587, 0.512, 0.38],
  plains: [0.2192, 0.9148, 13, 0.0594, 0.513, 0.3789], river: [0.2188, 0.913, 13, 0.0597, 0.5139, 0.3779],
  swamp: [0.2185, 0.913, 13, 0.0594, 0.5139, 0.3775], tundra: [0.2183, 0.913, 13, 0.0587, 0.513, 0.3779],
};

const UNDERWATER_ART_GEOMETRY = {"productionBuilding/grain/plot/lake_underwater_lvl_1.webp":[0.1976,0.8815,13,0.0795,0.4093,0.3157],"productionBuilding/aura/plot/lake_underwater_lvl_1.webp":[0.2061,0.9019,13,0.0064,0.2259,0.4058],"productionBuilding/research_lake_underwater.png":[0.2045,0.863,13,0.0696,0.275,0.3393],"productionBuilding/sps_lake_underwater.png":[0.2025,0.8981,13,0.0619,0.4657,0.3432],"productionBuilding/construction_lake_underwater.png":[0.2181,0.9139,13,0.058,0.512,0.3782],"buildings/lake_castle_big_underwater.png":[0.2055,0.863,13,0.0615,0.2917,0.3496],"buildings/lake_keep_big_underwater.png":[0.1893,0.8537,13,0.0431,0.3352,0.3355]};

export function worksiteArtGeometry(row, { fallback = false } = {}) {
  const url = fallback ? finishedWorksiteOverlayPath(row) : worksiteOverlayPath(row);
  if (!url) return null;
  const underwater = UNDERWATER_ART_GEOMETRY[url.split('/worksites/')[1]?.split('?')[0]];
  if (underwater) return { cx: underwater[0], base: underwater[1], hRem: underwater[2], l: underwater[3], t: underwater[4], r: underwater[5], ratio: 2829 / 1080 };
  const construction = url.match(/\/constructions\/construction_([a-z]+)\.png/);
  if (construction) {
    const cg = CONSTRUCTION_ART_GEOMETRY[construction[1]];
    return cg ? { cx: cg[0], base: cg[1], hRem: cg[2], l: cg[3], t: cg[4], r: cg[5], ratio: 2829 / 1080 } : null;
  }
  const key = url.split('/productionBuildingUniform/')[1];
  const g = key ? WORKSITE_ART_GEOMETRY[key.split('?')[0]] : null;
  return g ? { cx: g[0], base: g[1], hRem: g[2], l: g[3], t: g[4], r: g[5], ratio: 1560 / 640 } : null;
}

// (maintainer: "Align the center of the building with the center of the wide plot box ... the original artwork is
// optimized to host the building perfectly. It sits on the left of the terrain art"): each production building's full
// scene (same 560x214 frame as the terrain, so the two align exactly) with the building's measured centre, ground line
// and top as fractions of that frame (alpha bounding box, alpha > 40).
const PLATE_GEOMETRY = {"aura/plot/badlands_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/bog_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/caldera_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/canyon_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/desert_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/forest_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/hills_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/jungle_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/lake_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/mountain_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/plains_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/river_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/swamp_lvl_1.webp":[0.2205,0.9019,0.3271],"aura/plot/tundra_lvl_1.webp":[0.2205,0.9019,0.3271],"grain/plot/bog_lvl_1.webp":[0.1777,0.8645,0.6262],"grain/plot/lake_lvl_1.webp":[0.1777,0.8645,0.6262],"grain/plot/plains_lvl_1.webp":[0.1777,0.8645,0.6262],"grain/plot/river_lvl_1.webp":[0.1777,0.8645,0.6262],"grain_badlands.webp":[0.1848,0.8738,0.4252],"grain_bog.webp":[0.1866,0.8785,0.4252],"grain_caldera.webp":[0.1875,0.8692,0.4252],"grain_canyon.webp":[0.1848,0.8785,0.4252],"grain_desert.webp":[0.1884,0.8832,0.4252],"grain_forest.webp":[0.1812,0.8692,0.4252],"grain_hills.webp":[0.1804,0.8738,0.4252],"grain_jungle.webp":[0.1812,0.8692,0.4252],"grain_lake.webp":[0.1875,0.8879,0.4252],"grain_mountain.webp":[0.1866,0.8692,0.4252],"grain_plains.webp":[0.1857,0.8645,0.4252],"grain_river.webp":[0.1848,0.8645,0.4252],"grain_swamp.webp":[0.1875,0.8692,0.4252],"grain_tundra.webp":[0.1893,0.8785,0.4252],"iron/plot/badlands_lvl_1.webp":[0.1938,0.8598,0.5093],"iron/plot/caldera_lvl_1.webp":[0.1866,0.8551,0.5093],"iron/plot/mountain_lvl_1.webp":[0.1964,0.8598,0.5093],"research_badlands.webp":[0.2143,0.8692,0.2523],"research_bog.webp":[0.2134,0.8645,0.257],"research_caldera.webp":[0.2152,0.8645,0.257],"research_canyon.webp":[0.2152,0.8645,0.257],"research_desert.webp":[0.2143,0.8598,0.2617],"research_forest.webp":[0.2152,0.8645,0.257],"research_hills.webp":[0.2143,0.8645,0.257],"research_jungle.webp":[0.2143,0.8645,0.257],"research_lake.webp":[0.2143,0.8645,0.2523],"research_mountain.webp":[0.2134,0.8645,0.257],"research_plains.webp":[0.2134,0.8645,0.2617],"research_river.webp":[0.2143,0.8645,0.2617],"research_swamp.webp":[0.2143,0.8645,0.2617],"research_tundra.webp":[0.2143,0.8645,0.2617],"sps_badlands.webp":[0.2089,0.9019,0.4673],"sps_bog.webp":[0.208,0.9019,0.4673],"sps_caldera.webp":[0.208,0.9019,0.4673],"sps_canyon.webp":[0.2071,0.9019,0.4673],"sps_desert.webp":[0.2071,0.9019,0.4673],"sps_forest.webp":[0.2062,0.9019,0.4673],"sps_hills.webp":[0.2054,0.9019,0.4673],"sps_jungle.webp":[0.2054,0.9019,0.4673],"sps_lake.webp":[0.2062,0.8972,0.4673],"sps_mountain.webp":[0.2045,0.8972,0.4673],"sps_plains.webp":[0.2045,0.9019,0.4673],"sps_river.webp":[0.2045,0.8972,0.4673],"sps_swamp.webp":[0.2045,0.9019,0.4673],"sps_tundra.webp":[0.2045,0.9019,0.4673],"stone/plot/canyon_lvl_1.webp":[0.2554,0.8879,0.5981],"stone/plot/desert_lvl_1.webp":[0.2554,0.8879,0.5981],"stone/plot/hills_lvl_1.webp":[0.2554,0.8879,0.5981],"wood/plot/forest_lvl_1.webp":[0.2107,0.8785,0.6449],"wood/plot/jungle_lvl_1.webp":[0.2107,0.8785,0.6449],"wood/plot/swamp_lvl_1.webp":[0.2107,0.8785,0.6449],"wood/plot/tundra_lvl_1.webp":[0.2116,0.8785,0.6449]};

export function tilePlateLayers(row) {
  const uniform = worksiteOverlayFile(row);
  const key = uniform?.split('/productionBuildingUniform/')[1];
  const g = key && PLATE_GEOMETRY[key];
  const terrain = terrainBackgroundPath(row);
  if (!g || !terrain) return null;
  // Full-size art (terrain 1920x733, scene 2829x1080, same proportions): the card zooms in ~2.5x, so thumbnails blur.
  return { terrain, plate: `${ASSET_BASE}/land/worksites/productionBuilding/${key}?r=${ART_REV}`, cx: g[0], base: g[1], top: g[2] };
}

export function editorSceneLayers(row) {
  const worksite = worksiteOverlayPath(row);
  const worksiteFallback = constructionOverlayFile(row) ? finishedWorksiteOverlayPath(row) : null;
  return { terrain: terrainBackgroundPath(row), worksite, worksiteFallback };
}

export function editorSceneStyle(row) {
  const { terrain: bg, worksite: overlay } = editorSceneLayers(row);
  return ` style="--scene-terrain: ${bg ? `url('${bg}')` : 'none'}; --scene-worksite: ${overlay ? `url('${overlay}')` : 'none'}; --scene-y: 87%;"`;
}

// every image that fails to load is
// retried with backoff -- a fresh URL each retry so a poisoned cache entry or a half-finished response is
// never re-read -- and only after the last retry does the element fall back instead of hanging. The first
// try keeps the untouched URL so the immutable 30-day cache still serves it.
export const IMAGE_RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 15000];

export function retryImageUrl(url, attempt) {
  if (!attempt) return url;
  return `${url}${url.includes('?') ? '&' : '?'}r=${attempt}`;
}

const IMAGE_LOADS_IN_FLIGHT = new Map();

function loadOnce(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => (image.naturalWidth ? resolve(url) : reject(new Error(`Empty image: ${url}`)));
    image.onerror = () => reject(new Error(`Image failed: ${url}`));
    image.src = url;
  });
}

// Resolves to the URL that loaded (the original on the first try, a `r=<attempt>` URL on a retry)
// rejects after the last scheduled retry. One in-flight promise per URL, so concurrent callers share it.
export function retryImageLoad(url, { attempt = 0 } = {}) {
  const key = `${url}#${attempt}`;
  const pending = IMAGE_LOADS_IN_FLIGHT.get(key);
  if (pending) return pending;
  const promise = loadOnce(retryImageUrl(url, attempt)).catch((error) => {
    if (attempt >= IMAGE_RETRY_DELAYS_MS.length) throw error;
    return new Promise((resolve) => setTimeout(resolve, IMAGE_RETRY_DELAYS_MS[attempt]))
      .then(() => retryImageLoad(url, { attempt: attempt + 1 }));
  });
  IMAGE_LOADS_IN_FLIGHT.set(key, promise);
  promise.finally(() => IMAGE_LOADS_IN_FLIGHT.delete(key)).catch(() => {});
  return promise;
}

export function loadImageWithRetry(url) {
  return retryImageLoad(url, { attempt: 0 });
}

// CSS background layers set from JS (`[data-bg-retry="<url>"]` with an inline `background-image`): preload
// each with the same retries and, when only a retried URL loaded, redraw the layer from that URL.
export function retryBackgroundLayers(root) {
  if (!root?.querySelectorAll) return;
  root.querySelectorAll('[data-bg-retry]').forEach((el) => {
    const url = el.dataset.bgRetry;
    if (!url || el.dataset.bgRetryState) return;
    el.dataset.bgRetryState = 'pending';
    loadImageWithRetry(url).then((loaded) => {
      el.dataset.bgRetryState = 'loaded';
      if (loaded !== url && el.isConnected) el.style.backgroundImage = `url('${loaded}')`;
    }, () => {
      el.dataset.bgRetryState = 'exhausted';
      if (el.isConnected) el.classList.add('is-missing');
    });
  });
}

// `<img>` elements: one capture-phase error listener on the document (the error event does not bubble).
// Images with their own fallback chain (`data-card-image-candidates`, `data-eos-managed`) and
// `data-no-retry` are left alone; an image without a real `src` never enters the loop.
const IMG_RETRY_MAX = IMAGE_RETRY_DELAYS_MS.length;
let imageRetryInstalled = false;

function handleImageError(event) {
  const img = event.target;
  if (!(typeof HTMLImageElement !== 'undefined' && img instanceof HTMLImageElement)) return;
  const data = img.dataset;
  if (data.noRetry !== undefined || data.cardImageCandidates !== undefined || data.eosManaged !== undefined) return;
  if (data.retry === 'exhausted') return;
  const original = data.retryUrl || img.getAttribute('src');
  if (!original || original.startsWith('data:')) return;
  const attempt = Number(data.retry || 0) + 1;
  if (attempt > IMG_RETRY_MAX) { data.retry = 'exhausted'; return; }
  data.retry = String(attempt);
  data.retryUrl = original;
  setTimeout(() => {
    if (!img.isConnected || data.retry !== String(attempt)) return;
    img.src = retryImageUrl(original, attempt);
  }, IMAGE_RETRY_DELAYS_MS[attempt - 1]);
}

export function installImageRetry(doc = typeof document !== 'undefined' ? document : null) {
  if (!doc || imageRetryInstalled) return;
  imageRetryInstalled = true;
  doc.addEventListener('error', handleImageError, true);
}
