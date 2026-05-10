function updateShareStatus(message, colorClass = "text-cw-muted") {
  const status = document.getElementById("shareEmailStatus");
  if (!status) return;
  status.textContent = message;
  status.className = `mt-2 min-h-4 px-1 text-[11px] font-medium ${colorClass}`;
}

function toggleShareDropdown(forceOpen) {
  const dropdown = document.getElementById("shareDropdown");
  const button = document.getElementById("shareBtn");
  if (!dropdown || !button) return;
  const shouldOpen = forceOpen ?? dropdown.classList.contains("hidden");
  dropdown.classList.toggle("hidden", !shouldOpen);
  button.setAttribute("aria-expanded", String(shouldOpen));
  if (!shouldOpen) return;
  updateShareStatus("");
}

function showShareEmailForm() {
  const form = document.getElementById("shareEmailForm");
  if (!form) return;
  const shouldOpen = form.classList.contains("hidden");
  form.classList.toggle("hidden", !shouldOpen);
  updateShareStatus("");
  if (shouldOpen) document.getElementById("shareEmailTo")?.focus();
}

async function copyShareLink() {
  const link = window.location.href;
  try {
    await navigator.clipboard.writeText(link);
    updateShareStatus("Link copied.", "text-cw-green");
  } catch {
    updateShareStatus(link, "text-cw-muted");
  }
}

async function sendCurrentEmail(source = "share") {
  if (!currentResult) {
    updateShareStatus("Analyze a question before sharing.", "text-cw-muted");
    return;
  }
  if (currentResult.type === "clarification") {
    updateShareStatus("No TM1 analysis to email yet.", "text-red-600");
    return;
  }

  const input  = source === "share" ? document.getElementById("shareEmailTo") : document.getElementById("emailTo");
  const button = source === "share" ? document.getElementById("shareEmailBtn") : document.getElementById("emailBtn");
  if (!input || !button) return;
  const to = input.value.trim();
  if (!to) { input.focus(); updateShareStatus("Enter recipient.", "text-red-600"); return; }

  button.disabled = true;
  updateShareStatus("Sending...", "text-cw-muted");

  try {
    const res = await fetch(`${API}/api/send-email`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        to,
        question: currentResult.question,
        chosen_cube: currentResult.chosen_cube,
        reasoning: currentResult.reasoning,
        data_row_count: currentResult.data_row_count,
        data_preview: currentResult.data_preview || [],
        analysis: currentResult.analysis,
        history: currentMessages.map(m => ({ question: m.question, analysis: m.analysis })),
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Failed");
    }
    saveEmailRecord(to);
    updateShareStatus("Sent.", "text-cw-green");
  } catch (err) {
    updateShareStatus(err.message, "text-red-600");
  } finally {
    button.disabled = false;
  }
}
