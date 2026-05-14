// Cube-scope picker: lets the user restrict which cubes get queried for a
// question. Selection persists across questions until cleared.

const selectedCubeScope = new Set();
let _cubeScopeAll = [];
let _cubeScopeLoaded = false;

function _cubeScopePopup() {
  return document.getElementById("cubeScopePopup");
}

function _renderCubeScopeList() {
  const listEl = document.getElementById("cubeScopeList");
  const countEl = document.getElementById("cubeScopeCount");
  const loadingEl = document.getElementById("cubeScopeLoading");
  if (!listEl) return;
  loadingEl?.remove();

  const search = (document.getElementById("cubeScopeSearch")?.value || "").trim().toLowerCase();
  const filtered = _cubeScopeAll.filter(c => {
    if (!search) return true;
    return (
      String(c.cube || "").toLowerCase().includes(search) ||
      String(c.description || "").toLowerCase().includes(search)
    );
  });

  listEl.innerHTML = "";
  if (!filtered.length) {
    listEl.innerHTML = '<div class="px-2 py-3 text-[11px] italic text-cw-muted">No cubes match this search.</div>';
  } else {
    filtered.forEach(c => {
      const cubeName = String(c.cube || "");
      const checked = selectedCubeScope.has(cubeName);
      const id = `cube-scope-${cubeName.replace(/[^A-Za-z0-9_-]+/g, "_")}`;
      const row = document.createElement("label");
      row.className = "flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 transition hover:bg-cw-bg";
      row.setAttribute("for", id);
      row.innerHTML = `
        <input type="checkbox" id="${id}" data-cube="${esc(cubeName)}"
          class="mt-0.5 h-4 w-4 rounded border-cw-border text-cw-blue focus:ring-cw-blue/20"
          ${checked ? "checked" : ""} />
        <div class="min-w-0 flex-1">
          <div class="truncate text-[12px] font-medium text-cw-text">${esc(cubeName)}</div>
          ${c.description && c.description !== cubeName ? `<div class="truncate text-[10px] text-cw-muted">${esc(c.description)}</div>` : ""}
        </div>
      `;
      row.querySelector("input")?.addEventListener("change", event => {
        const target = event.currentTarget;
        if (target.checked) selectedCubeScope.add(cubeName);
        else selectedCubeScope.delete(cubeName);
        _updateCubeScopeBadge();
        _updateCubeScopeCount();
      });
      listEl.appendChild(row);
    });
  }

  _updateCubeScopeCount();
  // List height may have changed - re-anchor the popup so it stays aligned to
  // the button (especially important when the list loads asynchronously).
  if (!_cubeScopePopup()?.classList.contains("hidden")) {
    requestAnimationFrame(_positionCubeScopePopup);
  }
}

function _updateCubeScopeCount() {
  const countEl = document.getElementById("cubeScopeCount");
  if (countEl) {
    const n = selectedCubeScope.size;
    countEl.textContent = `${n} selected`;
  }
}

function _updateCubeScopeBadge() {
  const badge = document.getElementById("cubeScopeBadge");
  const label = document.getElementById("cubeScopeLabel");
  const btn = document.getElementById("cubeScopeBtn");
  if (!badge || !btn) return;
  const n = selectedCubeScope.size;
  if (n > 0) {
    badge.classList.remove("hidden");
    badge.classList.add("inline-flex");
    badge.textContent = String(n);
    btn.classList.add("border-cw-blue", "bg-cw-blueLite", "text-cw-blue");
    if (label) label.textContent = "Cubes";
  } else {
    badge.classList.add("hidden");
    badge.classList.remove("inline-flex");
    btn.classList.remove("border-cw-blue", "bg-cw-blueLite", "text-cw-blue");
    if (label) label.textContent = "Cubes";
  }
}

async function _loadCubeScopeOptions() {
  if (_cubeScopeLoaded) {
    _renderCubeScopeList();
    return;
  }
  try {
    const res = await fetch(`${API}/api/views`);
    if (!res.ok) throw new Error();
    const data = await res.json();
    _cubeScopeAll = Array.isArray(data.cubes) ? data.cubes : [];
    _cubeScopeLoaded = true;
  } catch {
    const listEl = document.getElementById("cubeScopeList");
    if (listEl) listEl.innerHTML = '<div class="px-2 py-3 text-[11px] italic text-red-500">Failed to load cube list.</div>';
    return;
  }
  // Drop any selections that no longer exist on the server.
  const valid = new Set(_cubeScopeAll.map(c => String(c.cube || "")));
  for (const name of Array.from(selectedCubeScope)) {
    if (!valid.has(name)) selectedCubeScope.delete(name);
  }
  _renderCubeScopeList();
  _updateCubeScopeBadge();
}

function _positionCubeScopePopup() {
  const popup = _cubeScopePopup();
  const button = document.getElementById("cubeScopeBtn");
  const listEl = document.getElementById("cubeScopeList");
  if (!popup || !button) return;
  const rect = button.getBoundingClientRect();
  const margin = 12;
  const viewportH = window.innerHeight;
  const viewportW = window.innerWidth;

  // Pick the side with more vertical room; clamp the list height so the popup
  // always fits within the viewport regardless of cube count.
  const spaceAbove = rect.top - margin * 2;
  const spaceBelow = viewportH - rect.bottom - margin * 2;
  const placeAbove = spaceAbove >= spaceBelow;
  const available = placeAbove ? spaceAbove : spaceBelow;
  const chrome = 160; // header + search + footer height (approx)
  if (listEl) {
    const listMax = Math.max(140, Math.min(360, available - chrome));
    listEl.style.maxHeight = `${listMax}px`;
  }

  const popupRect = popup.getBoundingClientRect();
  let top = placeAbove
    ? Math.max(margin, rect.top - popupRect.height - 6)
    : Math.min(viewportH - popupRect.height - margin, rect.bottom + 6);
  let left = rect.left;
  if (left + popupRect.width > viewportW - margin) left = viewportW - popupRect.width - margin;
  if (left < margin) left = margin;

  popup.style.top = `${Math.round(top)}px`;
  popup.style.left = `${Math.round(left)}px`;
}

function toggleCubeScopePopup(forceOpen) {
  const popup = _cubeScopePopup();
  const button = document.getElementById("cubeScopeBtn");
  if (!popup || !button) return;
  const shouldOpen = forceOpen ?? popup.classList.contains("hidden");
  popup.classList.toggle("hidden", !shouldOpen);
  button.setAttribute("aria-expanded", String(shouldOpen));
  if (shouldOpen) {
    _loadCubeScopeOptions();
    // Position after the popup is visible so we can measure its real height.
    requestAnimationFrame(_positionCubeScopePopup);
    window.addEventListener("resize", _positionCubeScopePopup);
    window.addEventListener("scroll", _positionCubeScopePopup, true);
  } else {
    window.removeEventListener("resize", _positionCubeScopePopup);
    window.removeEventListener("scroll", _positionCubeScopePopup, true);
  }
}

function clearCubeScope() {
  selectedCubeScope.clear();
  _updateCubeScopeBadge();
  _renderCubeScopeList();
}

function getSelectedCubeScope() {
  return Array.from(selectedCubeScope);
}

function invalidateCubeScopeCache() {
  _cubeScopeLoaded = false;
  _cubeScopeAll = [];
}
