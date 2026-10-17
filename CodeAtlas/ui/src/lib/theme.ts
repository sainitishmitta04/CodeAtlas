// Colours used by canvas/SVG renderers (which cannot read CSS variables cheaply).
// Keep in sync with styles/tokens.css.

export const COLORS = {
  bg: "#151816",
  raised: "#202521",
  ivory: "#F0EDE4",
  sage: "#A7B7A0",
  copper: "#C58B65",
  muted: "#A6ADA5",
  faint: "#6E756E",
  danger: "#D27A6A",
  warn: "#D6B26E",
};

export const LABEL_COLORS: Record<string, string> = {
  Project: "#F0EDE4",
  File: "#8FA9B8",
  Class: "#C58B65",
  Function: "#A7B7A0",
  Module: "#77807A",
};

export const REL_COLORS: Record<string, string> = {
  CONTAINS: "#4A524C",
  DEFINES: "#5E6B60",
  HAS_METHOD: "#7D6A5A",
  IMPORTS: "#6F8C9C",
  INHERITS: "#C9A267",
  CALLS: "#8DA586",
};

export const LAYER_COLORS: Record<string, string> = {
  presentation: "#C79A9A",
  api: "#8FA9B8",
  business: "#A7B7A0",
  domain: "#C9B07A",
  data: "#C58B65",
  infrastructure: "#B48678",
  utilities: "#9AA39B",
  tests: "#7E8A86",
  unclassified: "#5D645E",
};

export function labelColor(label: string): string {
  return LABEL_COLORS[label] ?? COLORS.muted;
}

export function layerColor(layer: string | null | undefined): string {
  return (layer && LAYER_COLORS[layer]) || LAYER_COLORS.unclassified;
}
