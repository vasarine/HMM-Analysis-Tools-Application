(function () {
  const ERROR_PREFIX_RE = /^(?:HMM(?:BUILD|EMIT|SEARCH|ER)\s*error|Exception|RuntimeError):\s*/i;

  function $(id) { return document.getElementById(id); }

  function applyStatus(elements, data) {
    if (elements.bar) elements.bar.style.width = (data.progress || 0) + '%';
    if (elements.msg) elements.msg.textContent = data.message || 'Waiting...';
    if (elements.badge) {
      elements.badge.textContent = data.status;
      elements.badge.className = 'status-pill ' + data.status.toLowerCase();
    }
  }

  function showFailure(elements, error) {
    if (elements.progressWrap) elements.progressWrap.style.display = 'none';
    if (elements.msg) elements.msg.style.display = 'none';
    if (!elements.errorContainer) return;
    elements.errorContainer.style.display = 'block';
    if (!elements.errorMessage) return;
    const cleaned = (error || 'Task failed').replace(ERROR_PREFIX_RE, '').trim();
    elements.errorMessage.textContent = cleaned || 'Something went wrong.';
  }

  function scrollToResults(id) {
    const el = id && $(id);
    if (el) requestAnimationFrame(() => el.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }

  window.initTaskStatusPoll = function (config) {
    const {
      taskId,
      statusUrl,
      initialStatus,
      resultsAnchorId,
      messageId = 'progress-msg',
      badgeId = 'status-badge',
      progressBarId = 'progress-bar',
      progressWrapId = 'progress-wrap',
      errorContainerId = 'error-container',
      errorMessageId = 'error-message',
      pollIntervalMs = 2000,
      reloadDelayMs = 800,
    } = config;

    scrollToResults(resultsAnchorId);

    if (!taskId || initialStatus === 'SUCCESS' || initialStatus === 'FAILURE') return;

    const elements = {
      bar: $(progressBarId),
      msg: $(messageId),
      badge: $(badgeId),
      progressWrap: $(progressWrapId),
      errorContainer: $(errorContainerId),
      errorMessage: $(errorMessageId),
    };

    let isReloading = false;
    let pollHandle = null;

    function poll() {
      if (isReloading) return;
      fetch(statusUrl)
        .then(r => r.json())
        .then(data => {
          applyStatus(elements, data);
          if (data.status === 'SUCCESS') {
            isReloading = true;
            clearInterval(pollHandle);
            if (elements.msg) elements.msg.textContent = 'Done! Reloading...';
            setTimeout(() => window.location.reload(), reloadDelayMs);
          } else if (data.status === 'FAILURE') {
            clearInterval(pollHandle);
            showFailure(elements, data.error);
          }
        })
        .catch(err => console.error('Poll error:', err));
    }

    poll();
    pollHandle = setInterval(poll, pollIntervalMs);
  };
})();
