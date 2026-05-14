const setQ = text => {
  const input = document.getElementById("q");
  if (!input) return;
  input.value = text;
  autoResizeQuestion();
  updateAnalyzeDisabled();
  input.focus();
};

function autoResizeQuestion() {
  const input = document.getElementById("q");
  if (!input) return;
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 176)}px`;
}

function updateAnalyzeDisabled() {
  const input = document.getElementById("q");
  const button = document.getElementById("runBtn");
  if (!input || !button) return;
  button.disabled = input.value.trim().length === 0;
}

function setLoading(on) {
  const button = document.getElementById("runBtn");
  if (button) button.disabled = on || !document.getElementById("q")?.value.trim();
  document.getElementById("sp")?.classList.toggle("hidden", !on);
  const label = document.getElementById("bl");
  if (label) label.textContent = on ? "" : "->";
  const stopBtn = document.getElementById("stopBtn");
  if (stopBtn) {
    stopBtn.classList.toggle("hidden", !on);
    stopBtn.classList.toggle("inline-flex", on);
  }
}

function updatePromptDock() {
  const prompt = document.getElementById("promptShell");
  if (!prompt || !chatMode) return;
  const sidebarWidth = isSidebarCollapsed() ? "3rem" : "260px";
  prompt.style.left  = `calc(${sidebarWidth} + 1.5rem)`;
  prompt.style.width = `calc(100vw - ${sidebarWidth} - 3rem)`;
}

function setPromptSolid(solid) {
  const prompt = document.getElementById("promptShell");
  if (!prompt || !chatMode) return;
  prompt.classList.toggle("opacity-60",  !solid);
  prompt.classList.toggle("opacity-100",  solid);
  prompt.style.opacity = solid ? "1" : "0.62";
}

function clearQuestionInput() {
  const input = document.getElementById("q");
  if (!input) return;
  input.value = "";
  autoResizeQuestion();
  updateAnalyzeDisabled();
}

function scrollToLatest() {
  window.requestAnimationFrame(() => {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
  });
}

function setChatMode(on) {
  chatMode = on;

  const main          = document.getElementById("mainContent");
  const wrap          = document.getElementById("chatWrap");
  const intro         = document.getElementById("heroIntro");
  const suggested     = document.getElementById("suggestedContent");
  const prompt        = document.getElementById("promptShell");
  const out           = document.getElementById("out");
  const planningBadge = document.getElementById("planningBadge");
  const shareMenu     = document.getElementById("shareMenu");

  intro?.classList.toggle("hidden", on);
  suggested?.classList.toggle("hidden", on);
  planningBadge?.classList.toggle("hidden", on);
  shareMenu?.classList.toggle("hidden", !on);
  if (intro)         intro.style.display         = on ? "none" : "";
  if (suggested)     suggested.style.display     = on ? "none" : "";
  if (planningBadge) planningBadge.style.display = on ? "none" : "";
  if (shareMenu)     shareMenu.style.display     = on ? "" : "none";
  if (!on) {
    toggleShareDropdown(false);
    document.getElementById("shareEmailForm")?.classList.add("hidden");
  }

  main?.classList.toggle("items-center",  !on);
  main?.classList.toggle("items-stretch",  on);
  main?.classList.toggle("pb-20",         !on);
  main?.classList.toggle("pb-36",          on);
  main?.classList.toggle("pt-8",          !on);
  main?.classList.toggle("pt-20",          on);
  wrap?.classList.toggle("-translate-y-8", !on);
  wrap?.classList.toggle("max-w-[760px]",  !on);
  wrap?.classList.toggle("max-w-[900px]",   on);
  out?.classList.toggle("pb-12",            on);

  prompt?.classList.toggle("fixed",           on);
  prompt?.classList.toggle("bottom-6",        on);
  prompt?.classList.toggle("right-6",         on);
  prompt?.classList.toggle("z-30",            on);
  prompt?.classList.toggle("mx-auto",         on);
  prompt?.classList.toggle("max-w-[780px]",   on);
  prompt?.classList.toggle("mb-6",           !on);
  prompt?.classList.toggle("mb-0",            on);
  prompt?.classList.toggle("backdrop-blur-md", on);

  if (prompt) {
    if (on) {
      updatePromptDock();
      prompt.style.position = "fixed";
      prompt.style.right    = "1.5rem";
      prompt.style.bottom   = "1.5rem";
      prompt.style.zIndex   = "30";
      prompt.style.maxWidth = "780px";
      setPromptSolid(false);
    } else {
      ["left","width","opacity","position","right","bottom","z-index","max-width"].forEach(p =>
        prompt.style.removeProperty(p)
      );
      prompt.classList.remove("opacity-60", "opacity-100", "backdrop-blur-md");
    }
  }
}
