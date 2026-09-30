/**
 * Componente 4: Extractor Ligero AXTree + MutationObserver
 * 
 * Extrae únicamente nodos interactivos visibles construyendo un índice semántico
 * ultra ligero de 3-6 KB con 3-10 candidatos principales.
 * Escucha mutaciones del DOM de forma incremental con MutationObserver.
 */

function isNodeVisible(node) {
  if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
    return false;
  }
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function getNodeAccessibleName(node) {
  return (
    node.getAttribute('aria-label') ||
    node.getAttribute('placeholder') ||
    node.getAttribute('title') ||
    (node.innerText || node.textContent || node.value || '').trim().replace(/\s+/g, ' ')
  );
}

function buildAXTreeIndex(root = document, maxCandidates = 15) {
  const INTERACTIVE_SELECTOR = [
    'button', 'input', 'select', 'textarea', 'a[href]',
    '[role="button"]', '[role="textbox"]', '[role="searchbox"]',
    '[role="combobox"]', '[role="link"]', '[role="checkbox"]', '[role="option"]'
  ].join(', ');

  const rawNodes = Array.from(root.querySelectorAll(INTERACTIVE_SELECTOR));
  const candidates = [];

  rawNodes.forEach((node, index) => {
    if (!isNodeVisible(node)) return;

    const rect = node.getBoundingClientRect();
    const role = node.getAttribute('role') || node.tagName.toLowerCase();
    const name = getNodeAccessibleName(node);

    candidates.push({
      id: candidates.length + 1,
      role: role,
      name: name.substring(0, 80), // Recortar nombres excesivamente largos
      tag: node.tagName.toLowerCase(),
      type: node.type || '',
      disabled: Boolean(node.disabled),
      bbox: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        w: Math.round(rect.width),
        h: Math.round(rect.height)
      }
    });

    if (candidates.length >= maxCandidates) return;
  });

  return candidates.slice(0, maxCandidates);
}

let activeObserver = null;
let mutationDebounceTimer = null;

function setupAXTreeMutationObserver(onMutatedCallback, debounceMs = 150) {
  if (activeObserver) {
    activeObserver.disconnect();
  }

  activeObserver = new MutationObserver((mutations) => {
    let hasRelevantChanges = false;
    for (const mutation of mutations) {
      if (mutation.type === 'childList' || mutation.type === 'attributes') {
        hasRelevantChanges = true;
        break;
      }
    }

    if (hasRelevantChanges) {
      clearTimeout(mutationDebounceTimer);
      mutationDebounceTimer = setTimeout(() => {
        const updatedCandidates = buildAXTreeIndex();
        if (typeof onMutatedCallback === 'function') {
          onMutatedCallback(updatedCandidates);
        }
      }, debounceMs);
    }
  });

  activeObserver.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['disabled', 'hidden', 'aria-hidden', 'class', 'style', 'value']
  });

  return activeObserver;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { buildAXTreeIndex, setupAXTreeMutationObserver, isNodeVisible };
}
