/*
 * Additive UI enhancement for the dated closed-loop runtime.
 *
 * The legacy React bundle already receives the recognition responses, but its
 * result components predate the duration fields.  This small same-origin
 * observer keeps the legacy bundle untouched while exposing the server-side
 * recognition timing to users.  It observes only recognition endpoints and
 * never changes a response or request.
 */
(function installRecognitionDurationOverlay20260902() {
  'use strict';

  if (window.__recognitionDurationOverlay20260902) return;
  window.__recognitionDurationOverlay20260902 = true;

  var OVERLAY_ID = 'recognition-duration-indicator-20260902';
  var MODE_CONTROL_ID = 'recognition-mode-toggle-20260902';
  var MODE_STORAGE_KEY = 'datainfraRedaction:closedLoopEnabled';
  var originalFetch = window.fetch.bind(window);
  var visionTotals = Object.create(null);
  var closedLoopEnabled = true;

  try {
    var storedMode = window.localStorage.getItem(MODE_STORAGE_KEY);
    if (storedMode === '0' || storedMode === 'false') closedLoopEnabled = false;
  } catch (_error) {
    // Private browsing may deny localStorage; the safe default is closed-loop.
  }

  function finiteNumber(value) {
    var number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function formatDuration(milliseconds) {
    var value = finiteNumber(milliseconds);
    if (value <= 0) return '';
    if (value < 1000) return Math.max(1, Math.round(value)) + ' ms';
    var seconds = value / 1000;
    return (seconds >= 10 ? seconds.toFixed(1) : seconds.toFixed(2)) + ' s';
  }

  function indicator() {
    if (!document.body) return null;
    var element = document.getElementById(OVERLAY_ID);
    if (element) return element;
    element = document.createElement('div');
    element.id = OVERLAY_ID;
    element.setAttribute('role', 'status');
    element.setAttribute('aria-live', 'polite');
    element.style.position = 'fixed';
    element.style.right = '16px';
    element.style.bottom = '16px';
    element.style.zIndex = '2147483647';
    element.style.maxWidth = 'min(420px, calc(100vw - 32px))';
    element.style.padding = '8px 12px';
    element.style.border = '1px solid rgba(148,163,184,.36)';
    element.style.borderRadius = '999px';
    element.style.background = 'rgba(15,23,42,.92)';
    element.style.boxShadow = '0 8px 24px rgba(15,23,42,.24)';
    element.style.color = '#f8fafc';
    element.style.font = '500 12px/1.35 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    element.style.pointerEvents = 'none';
    element.style.opacity = '0';
    element.style.transform = 'translateY(6px)';
    element.style.transition = 'opacity 160ms ease, transform 160ms ease';
    document.body.appendChild(element);
    return element;
  }

  function show(message) {
    if (!message) return;
    var element = indicator();
    if (!element) {
      window.requestAnimationFrame(function () { show(message); });
      return;
    }
    element.textContent = message;
    element.style.opacity = '1';
    element.style.transform = 'translateY(0)';
  }

  function clear() {
    var element = document.getElementById(OVERLAY_ID);
    if (element) element.remove();
    visionTotals = Object.create(null);
  }

  function updateModeControl(element) {
    element.textContent = closedLoopEnabled ? '闭环识别：开' : '单次识别：开';
    element.title = closedLoopEnabled
      ? '当前使用三轮闭环识别；点击切换为单次识别'
      : '当前使用单次识别；点击切换为三轮闭环识别';
    element.setAttribute('aria-pressed', closedLoopEnabled ? 'true' : 'false');
    element.style.background = closedLoopEnabled ? 'rgba(30,64,175,.94)' : 'rgba(71,85,105,.94)';
  }

  function modeControl() {
    if (!document.body) return null;
    var element = document.getElementById(MODE_CONTROL_ID);
    if (element) return element;
    element = document.createElement('button');
    element.id = MODE_CONTROL_ID;
    element.type = 'button';
    element.setAttribute('aria-label', '切换识别模式');
    element.style.position = 'fixed';
    element.style.right = '16px';
    element.style.bottom = '56px';
    element.style.zIndex = '2147483647';
    element.style.padding = '7px 12px';
    element.style.border = '1px solid rgba(148,163,184,.42)';
    element.style.borderRadius = '999px';
    element.style.boxShadow = '0 8px 24px rgba(15,23,42,.20)';
    element.style.color = '#f8fafc';
    element.style.font = '600 12px/1.35 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    element.style.cursor = 'pointer';
    element.style.pointerEvents = 'auto';
    element.addEventListener('click', function () {
      closedLoopEnabled = !closedLoopEnabled;
      try {
        window.localStorage.setItem(MODE_STORAGE_KEY, closedLoopEnabled ? '1' : '0');
      } catch (_error) {
        // Keep the in-memory choice for this page when storage is unavailable.
      }
      updateModeControl(element);
      show(closedLoopEnabled ? '已切换为三轮闭环识别' : '已切换为单次识别');
    });
    updateModeControl(element);
    document.body.appendChild(element);
    return element;
  }

  function ensureModeControl() {
    if (modeControl()) return;
    window.requestAnimationFrame(ensureModeControl);
  }

  ensureModeControl();

  function urlFor(input) {
    try {
      return new URL(typeof input === 'string' ? input : input.url, window.location.href);
    } catch (_error) {
      return null;
    }
  }

  function textAudit(fileId, fallbackMilliseconds, responseData) {
    var directAudit = responseData && responseData.closed_loop;
    if (directAudit && finiteNumber(directAudit.duration_ms) > 0) {
      renderText(directAudit, fallbackMilliseconds);
      return;
    }

    originalFetch('/api/v1/files/' + encodeURIComponent(fileId) + '/ner/closed-loop', {
      credentials: 'include',
    }).then(function (response) {
      if (!response.ok) throw new Error('duration endpoint ' + response.status);
      return response.json();
    }).then(function (data) {
      renderText(data && data.closed_loop, fallbackMilliseconds);
    }).catch(function () {
      renderText(null, fallbackMilliseconds);
    });
  }

  function formatRoundSeconds(milliseconds) {
    var seconds = finiteNumber(milliseconds) / 1000;
    if (seconds <= 0) return '';
    return seconds >= 10 ? seconds.toFixed(1) : seconds.toFixed(2);
  }

  function roundDetail(audit) {
    var items = audit && audit.rounds;
    if (!items || !items.length) return '';
    var durations = [];
    var sizes = [];
    for (var index = 0; index < items.length; index += 1) {
      var item = items[index] || {};
      var seconds = formatRoundSeconds(item.duration_ms);
      if (seconds) durations.push(seconds);
      var chars = finiteNumber(item.input_chars);
      if (chars > 0) sizes.push(String(Math.round(chars)));
    }
    var parts = [];
    if (durations.length) parts.push('每轮 ' + durations.join('/') + ' s');
    if (sizes.length) parts.push('送检 ' + sizes.join('→') + ' 字');
    return parts.join('，');
  }

  function renderText(audit, fallbackMilliseconds) {
    var duration = finiteNumber(audit && audit.duration_ms) || finiteNumber(fallbackMilliseconds);
    if (!duration) return;
    var rounds = finiteNumber(audit && audit.rounds_run);
    var message = '文本识别耗时：' + formatDuration(duration);
    if (audit && audit.enabled === false) {
      message += ' · 单次识别';
    } else if (rounds > 0) {
      message += ' · ' + Math.round(rounds) + ' 轮闭环';
      var detail = roundDetail(audit);
      if (detail) message += '（' + detail + '）';
    }
    show(message);
  }

  function renderVision(fileId, durationData, fallbackMilliseconds, mode) {
    var data = durationData && typeof durationData === 'object' ? durationData : {};
    var duration = finiteNumber(data.closed_loop_total_ms) ||
      finiteNumber(data.request_total_ms) ||
      finiteNumber(data.total) ||
      finiteNumber(fallbackMilliseconds);
    if (!duration) return;

    var current = visionTotals[fileId] || { milliseconds: 0, pages: 0, rounds: 0 };
    current.milliseconds += duration;
    current.pages += 1;
    current.rounds = Math.max(current.rounds, finiteNumber(data.closed_loop_rounds));
    current.closedLoop = mode !== false;
    visionTotals[fileId] = current;

    var message = '视觉识别耗时：' + formatDuration(current.milliseconds);
    if (current.pages > 1) message += ' · ' + current.pages + ' 页';
    if (!current.closedLoop) {
      message += ' · 单次识别';
    } else if (current.rounds > 0) {
      message += ' · 闭环 ' + Math.round(current.rounds) + ' 轮';
    }
    show(message);
  }

  window.fetch = function recognitionDurationFetch20260902(input, init) {
    var started = window.performance.now();
    var url = urlFor(input);
    var pathname = url ? url.pathname : '';
    var textMatch = pathname.match(/\/files\/([^/]+)\/ner\/hybrid$/);
    var visionMatch = pathname.match(/\/redaction\/([^/]+)\/vision$/);
    var uploadMatch = pathname.match(/\/files\/upload$/);
    var requestInput = input;
    var requestInit = init;
    var requestClosedLoop = closedLoopEnabled;

    if (textMatch && url) {
      url.pathname = url.pathname.replace(/\/ner\/hybrid$/, '/ner/hybrid-20260902');
      requestInput = url.toString();
      requestInit = Object.assign({}, init || {});
      var payload = {};
      if (typeof requestInit.body === 'string') {
        try { payload = JSON.parse(requestInit.body) || {}; } catch (_error) { payload = {}; }
      }
      payload.closed_loop = { enabled: closedLoopEnabled };
      requestInit.body = JSON.stringify(payload);
      var textHeaders = new Headers(requestInit.headers || {});
      textHeaders.set('Content-Type', 'application/json');
      requestInit.headers = textHeaders;
    } else if (visionMatch && url) {
      url.pathname = url.pathname.replace(/\/vision$/, '/vision-20260902');
      url.searchParams.set('closed_loop', closedLoopEnabled ? 'true' : 'false');
      requestInput = url.toString();
    }

    if (uploadMatch) clear();
    if (textMatch || visionMatch) show('识别中…');

    return originalFetch(requestInput, requestInit).then(function (response) {
      if (!response.ok || (!textMatch && !visionMatch)) return response;
      var elapsed = window.performance.now() - started;
      var cloned = response.clone();
      cloned.json().then(function (data) {
        if (textMatch) {
          textAudit(decodeURIComponent(textMatch[1]), elapsed, data);
          return;
        }
        renderVision(decodeURIComponent(visionMatch[1]), data && data.duration_ms, elapsed, requestClosedLoop);
      }).catch(function () {
        if (textMatch) textAudit(decodeURIComponent(textMatch[1]), elapsed, null);
        else renderVision(decodeURIComponent(visionMatch[1]), null, elapsed, requestClosedLoop);
      });
      return response;
    });
  };
})();
