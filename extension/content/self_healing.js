/**
 * Componente 3: Selectores Auto-Reparables (Self-Healing Cascades en 4 Niveles)
 * 
 * Estrategia determinista de resolución de elementos para content scripts de Firefox:
 *  Nivel 1: Accesibilidad ARIA (role + aria-label/name)
 *  Nivel 2: Texto Semántico Visible Normalizado
 *  Nivel 3: Posición Relativa / Jerarquía AXTree
 *  Nivel 4: Bounding Box (BBox) Coordenadas Visuales
 */

function isElementVisible(el) {
  if (!el) return false;
  const style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
    return false;
  }
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function normalizeText(text) {
  return (text || '').toLowerCase().trim().replace(/\s+/g, ' ');
}

function resolveSelfHealingElement(candidate, rootDoc = document) {
  if (!candidate) return null;

  const targetRole = normalizeText(candidate.role);
  const targetName = normalizeText(candidate.name);

  // ───────────────────────────────────────────────────────────────────────────
  // NIVEL 1: ACCESIBILIDAD ARIA (Role + Aria-Label / Name / Placeholder)
  // ───────────────────────────────────────────────────────────────────────────
  if (targetName) {
    if (targetRole) {
      const elRoleLabel = rootDoc.querySelector(`[role="${targetRole}"][aria-label="${candidate.name}"]`);
      if (isElementVisible(elRoleLabel)) {
        return { element: elRoleLabel, level: 1, strategy: 'aria_role_label' };
      }
    }

    const elLabelOnly = rootDoc.querySelector(`[aria-label="${candidate.name}"], [placeholder="${candidate.name}"]`);
    if (isElementVisible(elLabelOnly)) {
      return { element: elLabelOnly, level: 1, strategy: 'aria_label_placeholder' };
    }
  }

  // ───────────────────────────────────────────────────────────────────────────
  // NIVEL 2: TEXTO SEMÁNTICO VISIBLE NORMALIZADO
  // ───────────────────────────────────────────────────────────────────────────
  if (targetName) {
    const selector = candidate.tag || 'button, a, input, select, textarea, [role="button"], [role="link"], label, span, div';
    const elements = Array.from(rootDoc.querySelectorAll(selector));

    for (const el of elements) {
      if (!isElementVisible(el)) continue;
      const text = normalizeText(el.innerText || el.textContent || el.value || '');
      if (text === targetName || (text.length > 3 && targetName.length > 3 && text.includes(targetName))) {
        return { element: el, level: 2, strategy: 'semantic_text_match' };
      }
    }
  }

  // ───────────────────────────────────────────────────────────────────────────
  // NIVEL 3: POSICIÓN RELATIVA / JERARQUÍA AXTREE
  // ───────────────────────────────────────────────────────────────────────────
  if (candidate.parent_role || candidate.container_selector) {
    const container = rootDoc.querySelector(candidate.container_selector || `[role="${candidate.parent_role}"]`);
    if (container) {
      const children = Array.from(container.querySelectorAll('button, input, a, select'));
      const child = children.find(isElementVisible);
      if (child) {
        return { element: child, level: 3, strategy: 'axtree_hierarchy_relative' };
      }
    }
  }

  // ───────────────────────────────────────────────────────────────────────────
  // NIVEL 4: COORDENADAS VISUALES BOUNDING BOX (BBox)
  // ───────────────────────────────────────────────────────────────────────────
  if (candidate.bbox && typeof candidate.bbox.x === 'number' && typeof candidate.bbox.y === 'number') {
    const centerX = candidate.bbox.x + (candidate.bbox.w || 0) / 2;
    const centerY = candidate.bbox.y + (candidate.bbox.h || 0) / 2;

    const elAtPoint = rootDoc.elementFromPoint(centerX, centerY);
    if (isElementVisible(elAtPoint)) {
      return { element: elAtPoint, level: 4, strategy: 'bbox_visual_coordinates' };
    }
  }

  return null; // Elemento no localizado de forma segura
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { resolveSelfHealingElement, normalizeText, isElementVisible };
}
