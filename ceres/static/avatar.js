// (maintainer: "Avatar looks cool. But it is wrong. Check out the MCP server. This is a runi and should be
// documented that way."): the player's real avatar is the avatar-builder character -- the saved settings from
// GET /players/player_avatar/{name}, drawn from the game's own artwork layers. /players/avatar/{name} is only the
// legacy profile image (a RUNI) and is not used here. The layer mapping (assets/avatar-layers.json) and the rules
// below are ported from the splinterlands-mcp server (src/avatar/artwork.ts). Browser-only: the settings answer
// carries CORS for this origin; the layers are plain images, stacked in order -- nothing is proxied or cached by
// Ceres. The level numeral, badges and the square frame are left out (the medallion draws its own ring).

const SETTINGS_BASE = 'https://api.splinterlands.com/players/player_avatar/';
const ASSET_ORIGIN = 'https://d36mxiodymuqjm.cloudfront.net';
const MAX_LAYERS = 24;
let MAPPING = null;

async function avatarMapping() {
  if (!MAPPING) {
    MAPPING = fetch('/static/assets/avatar-layers.json').then((resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    }).catch((error) => { MAPPING = null; throw error; });
  }
  return MAPPING;
}

function whole(value) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('invalid avatar selection');
  return value;
}

// The ordered layer URLs for one saved avatar (background first); throws on anything the mapping does not know.
function avatarLayerUrls(avatar, mapping) {
  const config = mapping.configurations.find((c) => c.race === avatar.race && c.gender === avatar.gender);
  if (!config || avatar.body !== 1) throw new Error('unsupported avatar');
  const face = config.items.find((item) => item.key === 'face');
  const skin = whole(avatar.skin_color);
  if (!face || !face.colors.includes(skin)) throw new Error('unsupported skin');
  const layers = [];
  const add = (path, layer, key) => {
    if (!/^\/avatars\/[a-zA-Z0-9/_-]+\.webp$/.test(path)) throw new Error('invalid asset path');
    layers.push({ url: `${ASSET_ORIGIN}/website${path}`, layer, key });
  };
  const background = whole(avatar.background);
  if (!mapping.backgrounds.includes(background)) throw new Error('unsupported background');
  if (background) add(`/avatars/backgrounds/bg_${background}.webp`, 0, 'background');
  add(`/avatars/body/${config.race}/${config.gender}/body1_c${skin}.webp`, 8, 'body');
  for (const item of config.items) {
    let selected = whole(avatar[item.key]);
    if (!selected && item.key === 'cloth') selected = item.options[0].value;
    if (!selected) continue;
    const option = item.options.find((o) => o.value === selected);
    if (!option) throw new Error(`unsupported ${item.key}`);
    let color = 1;
    if (['nose', 'ears'].includes(item.key) || (config.race === 'orc' && ['mouth', 'earrings', 'eyebrows'].includes(item.key))) {
      color = skin;
    } else if (['eyebrows', 'mustache', 'beard'].includes(item.key) || (config.race === 'human' && config.gender === 1 && item.key === 'mouth')) {
      color = whole(avatar.hair_color);
    } else {
      const colors = item.colors.length ? item.colors : option.colors;
      if (colors.length) {
        const supplied = item.key === 'cloth' && avatar.cloth === 0 ? colors[0] : whole(avatar[item.colorKey || '']);
        if (!colors.includes(supplied)) throw new Error(`unsupported colour for ${item.key}`);
        color = supplied;
      }
    }
    for (const path of item.paths) {
      if (path.validOptions && !path.validOptions.includes(selected)) continue;
      add(path.path.replace('{option}', String(selected)).replace('{color}', String(color)), path.layer, item.key);
    }
  }
  if (layers.length > MAX_LAYERS) throw new Error('too many layers');
  const order = Object.keys(avatar);
  return layers.sort((a, b) => a.layer - b.layer || order.indexOf(a.key) - order.indexOf(b.key)).map((layer) => layer.url);
}

// The layer URLs for an account's saved avatar, or null when it has none / cannot be drawn.
export async function playerAvatarLayers(account) {
  const [mapping, avatar] = await Promise.all([
    avatarMapping(),
    fetch(`${SETTINGS_BASE}${encodeURIComponent(account)}`, { cache: 'no-store' }).then((resp) => (resp.ok ? resp.json() : null)),
  ]);
  if (!avatar || typeof avatar !== 'object' || !avatar.race) return null;
  return avatarLayerUrls(avatar, mapping);
}
