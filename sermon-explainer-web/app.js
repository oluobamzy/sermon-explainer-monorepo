// ============================================================
// SERMON EXPLAINER - Frontend Logic
// ============================================================
// Beginner explanation of the overall approach:
// This page talks to your backend using "fetch" (the browser's built-in
// way of making web requests). Since generating a video takes several
// minutes, we can't just wait for one request to finish - instead we:
//   1. Submit the job (POST /jobs) and get back a job_id
//   2. Repeatedly ask "is it done yet?" (poll GET /jobs/{job_id})
//   3. Once it's done, fetch the actual result and show it
//
// We also save the job_id in the browser's local storage, so if you
// accidentally refresh the page while a job is running, we can pick
// back up where we left off instead of losing track of it.
// ============================================================

const STORAGE_KEY = "sermon_explainer_job_id";
const POLL_INTERVAL_MS = 3000;

// How long we're willing to keep retrying if the backend doesn't respond -
// this covers Render's free-tier "waking up" delay (can take up to a minute).
const WAKE_UP_MAX_RETRIES = 20;

let pollTimer = null;
let wakeUpRetryCount = 0;

// ---------- Element references ----------
const formScreen = document.getElementById("form-screen");
const progressScreen = document.getElementById("progress-screen");
const resultsScreen = document.getElementById("results-screen");
const jobForm = document.getElementById("job-form");
const sourceInput = document.getElementById("source");
const sourceFileInput = document.getElementById("source-file");
const rightsGroup = document.getElementById("rights-group");
const confirmRightsCheckbox = document.getElementById("confirm-rights");
const introSecondsInput = document.getElementById("intro-seconds");
const outroSecondsInput = document.getElementById("outro-seconds");
const errorBanner = document.getElementById("error-banner");
const errorMessage = document.getElementById("error-message");
const retryButton = document.getElementById("retry-button");
const dismissErrorButton = document.getElementById("dismiss-error-button");
const queuePositionNote = document.getElementById("queue-position-note");

let lastFailedAction = null; // lets the Retry button re-run whatever just failed

// ============================================================
// Small helpers
// ============================================================

function showScreen(screen) {
  [formScreen, progressScreen, resultsScreen].forEach((s) => {
    s.hidden = s !== screen;
  });
}

function showError(message, { retryable = false, onRetry = null } = {}) {
  errorMessage.textContent = message;
  retryButton.hidden = !retryable;
  lastFailedAction = onRetry;
  errorBanner.hidden = false;
}

function hideError() {
  errorBanner.hidden = true;
  lastFailedAction = null;
}

retryButton.addEventListener("click", () => {
  hideError();
  if (lastFailedAction) lastFailedAction();
});

dismissErrorButton.addEventListener("click", hideError);

// Only show the "I have permission" checkbox when the source looks like a
// web link (YouTube etc.) - a local file reference wouldn't need it.
function isWebSource(value) {
  return /^https?:\/\//i.test((value || "").trim());
}

function syncRightsRequirement() {
  const hasUploadedFile = sourceFileInput.files && sourceFileInput.files.length > 0;
  const hasTypedSource = (sourceInput.value || "").trim().length > 0;
  if (hasUploadedFile && !hasTypedSource) {
    rightsGroup.hidden = true;
    confirmRightsCheckbox.required = false;
    return;
  }

  const looksLikeUrl = isWebSource(sourceInput.value);
  rightsGroup.hidden = !looksLikeUrl;
  confirmRightsCheckbox.required = looksLikeUrl;
}

sourceInput.addEventListener("input", syncRightsRequirement);
sourceFileInput.addEventListener("change", syncRightsRequirement);
sourceInput.addEventListener("input", () => {
  // If someone types/pastes a source explicitly, treat that as authoritative.
  if ((sourceInput.value || "").trim()) {
    sourceFileInput.value = "";
  }
});

introSecondsInput.addEventListener("input", () => {
  document.getElementById("intro-seconds-value").textContent = introSecondsInput.value;
});
outroSecondsInput.addEventListener("input", () => {
  document.getElementById("outro-seconds-value").textContent = outroSecondsInput.value;
});

// ============================================================
// Building the request the backend expects
// ============================================================

function buildJobPayload(resolvedSource) {
  const sourceValue = (resolvedSource || sourceInput.value || "").trim();
  const socials = {
    facebook: document.getElementById("social-facebook").value.trim(),
    youtube: document.getElementById("social-youtube").value.trim(),
    x: document.getElementById("social-x").value.trim(),
    instagram: document.getElementById("social-instagram").value.trim(),
    tiktok: document.getElementById("social-tiktok").value.trim(),
  };

  // Only send social fields that actually have a value - blank ones are
  // simply omitted, matching the backend's documented behavior.
  Object.keys(socials).forEach((key) => {
    if (!socials[key]) delete socials[key];
  });

  const looksLikeUrl = isWebSource(sourceValue);

  return {
    source: sourceValue,
    title: document.getElementById("title").value.trim() || undefined,
    outro_text: document.getElementById("outro-text").value.trim() || undefined,
    // Clamped client-side to 1-10 (the <input type="range"> already enforces
    // this, but we clamp again here defensively in case of odd input).
    intro_seconds: Math.min(10, Math.max(1, Number(introSecondsInput.value))),
    outro_seconds: Math.min(10, Math.max(1, Number(outroSecondsInput.value))),
    socials,
    confirm_rights: looksLikeUrl ? confirmRightsCheckbox.checked : true,
    options: {
      enable_music: document.getElementById("enable-music").checked,
      burn_captions: document.getElementById("burn-captions").checked,
    },
  };
}

// ============================================================
// Talking to the backend
// ============================================================

async function submitJob() {
  hideError();

  const sourceTextValue = sourceInput.value.trim();
  const uploadedFile = sourceFileInput.files && sourceFileInput.files.length > 0
    ? sourceFileInput.files[0]
    : null;

  if (!sourceTextValue && !uploadedFile) {
    showError("Add a YouTube URL/server path, or upload a local audio/video file before continuing.");
    return;
  }

  const looksLikeUrl = isWebSource(sourceTextValue);
  if (looksLikeUrl && !confirmRightsCheckbox.checked) {
    showError("Please confirm you have the rights to use this recording before continuing.");
    return;
  }

  let resolvedSource = sourceTextValue;
  if (!resolvedSource && uploadedFile) {
    setProgressMessage("Uploading source file...");
    showScreen(progressScreen);

    const uploadedSourcePath = await uploadSourceFile(uploadedFile);
    if (!uploadedSourcePath) {
      showScreen(formScreen);
      return;
    }
    resolvedSource = uploadedSourcePath;
  }

  const payload = buildJobPayload(resolvedSource);

  showScreen(progressScreen);
  setProgressMessage("Submitting your job...");

  try {
    const response = await fetch(`${API_BASE_URL}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const body = await safeJson(response);
      handleBackendError(response.status, body, submitJob);
      showScreen(formScreen);
      return;
    }

    const data = await response.json();
    localStorage.setItem(STORAGE_KEY, data.job_id);
    wakeUpRetryCount = 0;
    startPolling(data.job_id);
  } catch (err) {
    // A network-level failure here often just means the backend is asleep
    // (Render free tier) or unreachable - not necessarily a real error.
    showError(
      "Couldn't reach the server. If this is the first request in a while, " +
      "the backend may just be waking up - trying again...",
      { retryable: true, onRetry: submitJob }
    );
    showScreen(formScreen);
  }
}

async function uploadSourceFile(file) {
  try {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch(`${API_BASE_URL}/uploads`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const body = await safeJson(response);
      handleBackendError(response.status, body, () => submitJob());
      return null;
    }

    const data = await response.json();
    return data.source;
  } catch (err) {
    showError(
      "Couldn't upload the selected file. Check your connection and try again.",
      { retryable: true, onRetry: () => submitJob() }
    );
    return null;
  }
}

async function safeJson(response) {
  try {
    const parsed = await response.json();
    if (parsed && parsed.detail && typeof parsed.detail === "object") {
      return parsed.detail;
    }
    return parsed;
  } catch {
    return null;
  }
}

function handleBackendError(status, body, retryAction) {
  const errorCode = body && body.error_code ? body.error_code : null;
  const message = body && body.message ? body.message : `Request failed (status ${status}).`;
  const retryable = body && typeof body.retryable === "boolean" ? body.retryable : status >= 500;

  showError(message + (errorCode ? ` (${errorCode})` : ""), {
    retryable,
    onRetry: retryable ? retryAction : null,
  });
}

function startPolling(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => checkJobStatus(jobId), POLL_INTERVAL_MS);
  checkJobStatus(jobId); // check immediately, don't wait for the first interval
}

async function checkJobStatus(jobId) {
  try {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}`);

    if (response.status === 404) {
      // Job not found - likely the backend restarted and lost its in-memory
      // record. Nothing to resume; let the person start fresh.
      clearInterval(pollTimer);
      localStorage.removeItem(STORAGE_KEY);
      showError(
        "This job could not be found anymore - the server may have restarted. Please start a new video."
      );
      showScreen(formScreen);
      return;
    }

    if (!response.ok) {
      const body = await safeJson(response);
      handleBackendError(response.status, body, () => checkJobStatus(jobId));
      return;
    }

    wakeUpRetryCount = 0; // got a real response, reset the "waking up" counter
    const data = await response.json();
    renderProgress(data);

    if (data.status === "completed") {
      clearInterval(pollTimer);
      await loadResult(jobId);
    } else if (data.status === "failed") {
      clearInterval(pollTimer);
      localStorage.removeItem(STORAGE_KEY);
      showError(data.message || "The job failed.", { retryable: false });
      showScreen(formScreen);
    } else if (data.status === "canceled") {
      clearInterval(pollTimer);
      localStorage.removeItem(STORAGE_KEY);
      showScreen(formScreen);
    }
    // "queued" / "running" -> just keep polling, nothing else to do
  } catch (err) {
    // Likely the backend is still asleep/waking up - keep trying for a while
    // before giving up, since Render's free tier can take up to ~a minute.
    wakeUpRetryCount += 1;
    if (wakeUpRetryCount === 1) {
      setProgressMessage("Waking up the server - this can take up to a minute...");
    }
    if (wakeUpRetryCount > WAKE_UP_MAX_RETRIES) {
      clearInterval(pollTimer);
      showError(
        "The server didn't respond after a while. Please check your connection and try again.",
        { retryable: true, onRetry: () => startPolling(jobId) }
      );
    }
  }
}

function renderProgress(data) {
  const queuePosition = Number.isInteger(data.queue_position) ? data.queue_position : null;
  if (data.status === "queued") {
    const queuedMessage = queuePosition
      ? `Queued (position ${queuePosition}) - waiting for an open processing slot...`
      : (data.message || "Queued - waiting for an open processing slot...");
    setProgressMessage(queuedMessage);
    if (queuePosition) {
      queuePositionNote.hidden = false;
      queuePositionNote.textContent = `Estimated wait: ${Math.max(0, queuePosition - 1)} job(s) ahead of you.`;
    } else {
      queuePositionNote.hidden = true;
      queuePositionNote.textContent = "";
    }
  } else {
    setProgressMessage(data.message || "Working...");
    queuePositionNote.hidden = true;
    queuePositionNote.textContent = "";
  }

  const percent = typeof data.progress_percent === "number" ? data.progress_percent : 0;
  document.getElementById("progress-bar-fill").style.width = `${percent}%`;
  document.getElementById("progress-percent-label").textContent = `${percent}%`;

  const stageOrder = [
    "fetch_source",
    "transcribe",
    "summarize",
    "narrate",
    "illustrate",
    "assemble_base",
    "music",
    "finalize",
  ];
  const normalizedStage = data.stage === "starting" ? "fetch_source" : data.stage;
  const currentIndex = stageOrder.indexOf(normalizedStage);

  if (data.status === "queued") {
    document.querySelectorAll(".stage-list li").forEach((li) => {
      li.classList.remove("active", "done");
    });
    return;
  }

  if (data.stage === "completed") {
    document.querySelectorAll(".stage-list li").forEach((li) => {
      li.classList.remove("active");
      li.classList.add("done");
    });
    return;
  }

  document.querySelectorAll(".stage-list li").forEach((li) => {
    const stageIndex = stageOrder.indexOf(li.dataset.stage);
    li.classList.remove("active", "done");
    if (stageIndex < currentIndex) li.classList.add("done");
    if (stageIndex === currentIndex || (currentIndex < 0 && stageIndex === 0)) li.classList.add("active");
  });
}

function setProgressMessage(message) {
  document.getElementById("progress-message").textContent = message;
}

async function loadResult(jobId) {
  try {
    const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/result`);

    if (!response.ok) {
      const body = await safeJson(response);
      handleBackendError(response.status, body, () => loadResult(jobId));
      showScreen(formScreen);
      return;
    }

    const data = await response.json();
    localStorage.removeItem(STORAGE_KEY);
    showResults(jobId, data);
  } catch (err) {
    showError("The video finished, but the result couldn't be loaded. Please try again.", {
      retryable: true,
      onRetry: () => loadResult(jobId),
    });
  }
}

function showResults(jobId, data) {
  const outputs = data.outputs || {};
  const downloads = outputs.downloads || {};

  const videoUrl = downloads.final_video
    ? `${API_BASE_URL}${downloads.final_video}`
    : `${API_BASE_URL}/jobs/${jobId}/artifacts/final_video`;
  const subtitlesUrl = downloads.subtitles
    ? `${API_BASE_URL}${downloads.subtitles}`
    : `${API_BASE_URL}/jobs/${jobId}/artifacts/subtitles`;

  const preview = document.getElementById("result-preview");
  preview.src = videoUrl;

  document.getElementById("download-video-button").href = videoUrl;
  document.getElementById("download-subtitles-button").href = subtitlesUrl;

  const sourceKind = outputs.source_kind;
  const requestedSource = outputs.source_requested;
  const resolvedSourcePath = outputs.source_resolved_path;
  const sourceLabel =
    sourceKind === "url"
      ? `Source used: YouTube/web URL (${requestedSource || "unknown"})`
      : `Source used: local/uploaded file (${resolvedSourcePath || requestedSource || "unknown"})`;
  document.getElementById("result-source-used").textContent = sourceLabel;

  const durationSeconds = outputs.duration_seconds ?? outputs.total_duration_seconds;
  const durationText = durationSeconds
    ? `Total length: ${Math.round(durationSeconds / 60)} min`
    : "";
  document.getElementById("result-duration").textContent = durationText;

  const musicEnabled = outputs.music_enabled ?? outputs.background_music_used;
  const musicReason = outputs.music_reason;
  document.getElementById("result-music-note").textContent =
    musicEnabled === false
      ? (musicReason === "user_disabled"
          ? "Background music was disabled for this run."
          : `Background music could not be generated, so it was skipped. Reason: ${musicReason || "unknown"}.`)
      : "Background music is included in this render.";

  showScreen(resultsScreen);
}

// ============================================================
// Resuming a job after a page refresh
// ============================================================

function resumeExistingJobIfAny() {
  const existingJobId = localStorage.getItem(STORAGE_KEY);
  if (existingJobId) {
    showScreen(progressScreen);
    setProgressMessage("Reconnecting to your job...");
    startPolling(existingJobId);
  }
}

// ============================================================
// Buttons that reset the flow
// ============================================================

function resetToForm() {
  if (pollTimer) clearInterval(pollTimer);
  localStorage.removeItem(STORAGE_KEY);
  jobForm.reset();
  document.getElementById("intro-seconds-value").textContent = "5";
  document.getElementById("outro-seconds-value").textContent = "5";
  document.getElementById("result-source-used").textContent = "";
  syncRightsRequirement();
  hideError();
  showScreen(formScreen);
}

document.getElementById("cancel-button").addEventListener("click", resetToForm);
document.getElementById("start-new-button").addEventListener("click", resetToForm);

// ============================================================
// Wire up the form and start
// ============================================================

jobForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitJob();
});

resumeExistingJobIfAny();
syncRightsRequirement();
