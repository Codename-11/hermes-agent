const HOVER_REVEAL_TRIGGER_WIDTH = 4
const HOVER_REVEAL_EDGE_GUTTER = 8

export function narrowHoverStripStyle(side: 'left' | 'right'): { left?: number; right?: number; width: number } {
  return { [side]: HOVER_REVEAL_EDGE_GUTTER, width: HOVER_REVEAL_TRIGGER_WIDTH }
}
