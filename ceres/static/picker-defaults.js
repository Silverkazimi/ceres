// The saved picker preference stays a Live preference. Planning opens with the
// existing On land switch enabled so cards on other plots are visible at once.
export function pickerOnLandAtOpen(planning, remembered) {
  return planning ? true : remembered === true;
}
