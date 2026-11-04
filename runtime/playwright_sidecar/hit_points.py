# SPDX-License-Identifier: MIT
"""Where a person would hit an element: the centre of each of its line boxes."""
from __future__ import annotations

# A link that wraps is several line boxes. The centre of its bounding box can
# fall between them, on the text around it, and the link then looks covered
# (live turn adfff4ca, 2/11/2026: a cookie banner at 1280 px). The centre of
# each box, clamped to the viewport, is a point a person really hits. A block
# has one box, so its point is the centre used before.
HIT_POINTS_JS = r"""
  const hitPoints = el => {
    const boxes = Array.from(el.getClientRects())
      .filter(b => b.width >= 1 && b.height >= 1).slice(0, 4);
    return (boxes.length ? boxes : [el.getBoundingClientRect()]).map(b => [
      Math.max(0, Math.min(innerWidth - 1, b.left + b.width / 2)),
      Math.max(0, Math.min(innerHeight - 1, b.top + b.height / 2))]);
  };
"""
