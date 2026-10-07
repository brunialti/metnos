# SPDX-License-Identifier: MIT
"""Read-only, local evidence for an obstructed page; never a browser endpoint."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path


# Run in the already-owned Page. The inert clone cannot execute page scripts.
# Geometry is measured on the original; the static replay is only diagnostic.
_SNAPSHOT_JS = r"""({maxNodes, budgetMs}) => {
  const started = performance.now();
  const root = document.documentElement;
  if (!root) return {status: 'unavailable'};
  const originals = [root, ...root.querySelectorAll('*')];
  if (originals.length > maxNodes)
    return {status: 'node_limit', available: originals.length, limit: maxNodes};
  const inert = document.implementation.createHTMLDocument('');
  const copy = inert.importNode(root, true);
  const copies = [copy, ...copy.querySelectorAll('*')];
  const indices = new Map(originals.map((node, i) => [node, i]));
  const email = /[a-z0-9.!#$%&'*+\/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+/ig;
  const clean = text => String(text || '').replace(email, '[redacted]');
  const privateSelector = 'input,textarea,select,[contenteditable]:not([contenteditable="false"]),[data-metnos-redact="1"]';
  const attributes = new Set(['role', 'aria-label', 'aria-hidden', 'aria-modal',
    'aria-expanded', 'type', 'colspan', 'rowspan', 'dir', 'lang']);
  const removedTags = new Set(['script', 'style', 'link', 'meta', 'base',
    'template', 'noscript', 'object', 'embed', 'animate', 'animateMotion', 'animateTransform', 'set']);
  const controls = [];
  const describe = node => {
    if (!node) return null;
    const r = node.getBoundingClientRect(), style = getComputedStyle(node);
    return {ref: `n${indices.get(node)}`, tag: node.localName,
      box: {x: r.x, y: r.y, width: r.width, height: r.height},
      position: style.position, zIndex: style.zIndex};
  };
  for (let i = 0; i < originals.length; i++) {
    if (performance.now() - started > budgetMs) return {status: 'time_limit'};
    const original = originals[i], node = copies[i];
    const computed = getComputedStyle(original);
    const privateNode = original.closest(privateSelector);
    // A display:contents container has no box, but its children can be visible.
    const hidden = computed.visibility !== 'visible' || computed.opacity === '0' ||
      (computed.display !== 'contents' && original.getClientRects().length === 0);
    if (original.matches('a,button,input:not([type="hidden"]),select,textarea,[role="button"],[role="link"]')) {
      const description = describe(original), r = description.box;
      const x = Math.max(0, r.x), y = Math.max(0, r.y);
      const right = Math.min(innerWidth, r.x + r.width);
      const bottom = Math.min(innerHeight, r.y + r.height);
      const inViewport = !hidden && right > x && bottom > y;
      const hit = inViewport ? document.elementFromPoint((x + right) / 2, (y + bottom) / 2) : null;
      const covered = inViewport && !!hit && hit !== original && !original.contains(hit);
      // Labels use a redacted clone below, never innerText of a private subtree.
      controls.push({...description, inViewport, covered,
        coveringElement: covered ? describe(hit) : null});
    }
    if (removedTags.has(original.localName) || original.matches('input[type="hidden"]')) {
      node.remove();
      continue;
    }
    for (const attr of [...node.attributes]) {
      if (!attributes.has(attr.name) || privateNode || hidden) node.removeAttribute(attr.name);
      else node.setAttribute(attr.name, clean(attr.value));
    }
    for (const property of computed) {
      const value = computed.getPropertyValue(property);
      if (!property.startsWith('--') && property !== 'content' && !/url\s*\(/i.test(value))
        node.style.setProperty(property, clean(value));
    }
    node.style.setProperty('animation', 'none', 'important');
    node.style.setProperty('transition', 'none', 'important');
    node.id = `snapshot-n${i}`;
    if (original.localName === 'a') node.setAttribute('href', `#snapshot-n${i}`);
    if (original.matches('input,textarea,select')) {
      if (original.localName === 'input') node.type = original.type;
      node.value = '';
      node.disabled = true;
      if (original.localName === 'input') node.checked = false;
    }
    // Keep private/hidden boxes, but discard their contents and descendants.
    if (privateNode || hidden || original.localName === 'iframe') node.replaceChildren();
    for (const text of [...node.childNodes]) {
      if (text.nodeType === Node.TEXT_NODE) text.textContent = clean(text.textContent);
      if (text.nodeType === Node.COMMENT_NODE) text.remove();
    }
  }
  for (const control of controls) {
    const node = copy.querySelector(`#snapshot-${control.ref}`);
    control.label = node && control.inViewport ?
      clean(node.getAttribute('aria-label') || node.textContent).trim().slice(0, 200) : '';
  }
  const head = copy.querySelector('head');
  if (!head) return {status: 'unavailable'};
  head.innerHTML = '<meta charset="utf-8">' +
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; ' +
    'style-src \'unsafe-inline\'; form-action \'none\'; base-uri \'none\'">';
  return {status: 'captured', format: 'metnos-page-snapshot-v1',
    capturedAt: new Date().toISOString(), origin: location.origin,
    viewport: {width: innerWidth, height: innerHeight},
    scroll: {x: scrollX, y: scrollY}, controls,
    omitted: {embeddedDocuments: document.querySelectorAll('iframe,object,embed').length,
      openShadowRoots: originals.filter(node => node.shadowRoot).length},
    limitations: 'Static main document only; no scripts, external assets, embedded/shadow contents or private fields. Visible text can still be personal. Measured geometry is authoritative; replay may differ.',
    html: '<!doctype html>\n' + copy.outerHTML};
}"""


async def save(page, directory: Path, *, max_nodes: int = 4000,
               max_bytes: int = 16 * 1024 * 1024) -> dict:
    """Save bounded evidence, reporting omission without changing action failure."""
    path = None
    try:
        snapshot = await asyncio.wait_for(page.evaluate(
            _SNAPSHOT_JS, {"maxNodes": max_nodes, "budgetMs": 1500}), timeout=3)
        if snapshot.get("status") != "captured":
            return {k: snapshot[k] for k in ("status", "available", "limit") if k in snapshot}
        data = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
        if len(data) > max_bytes:
            return {"status": "size_limit", "available": len(data), "limit": max_bytes}
        candidate = directory / f"{secrets.token_hex(12)}.page.json"
        fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        path = candidate
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        return {"status": "saved", "path": str(path)}
    except Exception as exc:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        # Exception messages can include page content/URLs. Keep only the type.
        return {"status": "unavailable", "reason": type(exc).__name__}
